"""Precomputed quote candidates: extraction rules and the offline LLM rating pass.

A candidate is a run of 1 to 3 sentences from one paragraph that fits in a
reply and can stand alone. Search matches posts against candidates instead of
whole paragraphs, so the score measures the quote that would be posted.
"""
import hashlib
import re
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

from comrade import llm
from comrade import render
from comrade.config import config

MIN_CHARS = 20  # short enough for "Workers of all countries, unite!"
# Opening words that point back to earlier text, so the quote can't stand alone
OPENERS = set("""
this that these those it its he she they his her their him them thus hence therefore but and or so
such here then also however moreover furthermore nor yet again which who whom whose both similarly
consequently accordingly besides finally first second secondly third lastly otherwise instead meanwhile
nevertheless nonetheless likewise still for because since though although whereas while if when as
""".split())


def text_hash(text: str) -> str:
    return hashlib.sha1(text.encode("utf-8")).hexdigest()


def whole_sentences(text: str) -> bool:
    """Starts and ends like a sentence, not a fragment."""
    return (text[0].isupper() or text[0] in "“\"") and bool(re.search(r"[.!?][”\"’)]?$", text))


def standalone(text: str) -> bool:
    """Rules for a quote that makes sense without the paragraph around it."""
    first = re.match(r"[\W_]*(\w+)", text)
    if not first or first.group(1).lower() in OPENERS or not whole_sentences(text):
        return False
    return sum(c.isdigit() for c in text) / len(text) <= 0.05  # tables, statistics, citations


def candidates(passage: dict, chosen: set = frozenset()) -> list[tuple[int, int, str]]:
    """Every 1 to 3 sentence run in a passage that passes the length, standalone, and quotation rules.

    Runs whose hash is in `chosen` (quotes the LLM rated 3 or higher) skip the
    standalone rules, which they can fail, such as one that opens with "But".
    """
    sentences = passage["sentences"]
    out = []
    for start in range(len(sentences)):
        for end in range(start, min(start + config.MAX_SPAN, len(sentences))):
            text = " ".join(sentences[start:end + 1])
            if len(text) > config.MAX_QUOTE_CHARS:
                break
            if not (text_hash(text) in chosen and whole_sentences(text)) and \
                    (len(text) < MIN_CHARS or not standalone(text)):
                continue
            try:
                render.quote_text(passage, start, end)
            except render.Rejected:
                continue
            out.append((start, end, text))
    return out


def _passages(conn, where: str = "", params=()) -> dict[int, dict]:
    rows = conn.execute(
        f"""SELECT p.id, p.text, s.paragraph_id, s.text AS sentence FROM paragraphs p
            JOIN sentences s ON s.paragraph_id = p.id {where} ORDER BY p.id, s.idx""",
        params,
    )
    passages = {}
    for r in rows:
        passages.setdefault(r["id"], {"id": r["id"], "text": r["text"], "sentences": []})["sentences"].append(r["sentence"])
    return passages


def extract(conn, work_ids: list[str] = None) -> int:
    """Rebuild the quote candidates for some works, or all of them."""
    if work_ids:
        marks = ",".join("?" * len(work_ids))
        where, params = f"WHERE p.work_id IN ({marks})", work_ids
        old = f"paragraph_id IN (SELECT id FROM paragraphs WHERE work_id IN ({marks}))"
        conn.execute(f"DELETE FROM quotes_fts WHERE rowid IN (SELECT id FROM quotes WHERE {old})", params)
        conn.execute(f"DELETE FROM quotes WHERE {old}", params)
    else:
        where, params = "", ()
        conn.execute("DELETE FROM quotes_fts")
        conn.execute("DELETE FROM quotes")
    chosen = {r[0] for r in conn.execute("SELECT hash FROM quote_scores WHERE rating >= 3")}
    rows = [(pid, start, end, text, text_hash(text))
            for pid, passage in _passages(conn, where, params).items()
            for start, end, text in candidates(passage, chosen)]
    conn.executemany("INSERT INTO quotes (paragraph_id, start, end, text, hash) VALUES (?,?,?,?,?)", rows)
    _index(conn, f"WHERE q.{old}" if work_ids else "", params)
    conn.commit()
    return len(rows)


def _index(conn, where: str = "", params=()):
    """Add quotes to the keyword index. The summary is indexed too, so matches can use its plain wording."""
    conn.execute(
        f"""INSERT OR REPLACE INTO quotes_fts (rowid, text)
            SELECT q.id, q.text || ' ' || COALESCE(s.summary, '') FROM quotes q
            LEFT JOIN quote_scores s ON s.hash = q.hash {where}""",
        params,
    )


# --- Offline rating -------------------------------------------------------

RATE_SYSTEM = """You label lines for a Reddit bot that replies with quotes from socialist \
and communist writers.

You get passages with numbered sentences; each sentence shows its length in characters. \
In each passage, find up to 2 spans that work as a standalone quote:
- The author's own words, not words they quote from someone else.
- Understandable without the rest of the passage.
- A contiguous run of at most {max_span} sentences and under {max_chars} characters. \
start and end are inclusive sentence numbers.
- Makes a clear point that could answer a post or comment.

Rate each span:
5 = a sharp, memorable line people would share.
4 = a strong, clear statement.
3 = usable: clear, but plain.
Don't return spans that would rate below 3. Many passages have none; return nothing for them.

For each span, write a summary: the point in plain modern English, at most 15 words, \
as if someone were arguing it today. The summary is used to match the quote to posts."""

RATE_SCHEMA = {
    "type": "object",
    "properties": {
        "quotes": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "passage_id": {"type": "integer"},
                    "start": {"type": "integer"},
                    "end": {"type": "integer"},
                    "rating": {"type": "integer"},
                    "summary": {"type": "string"},
                },
                "required": ["passage_id", "start", "end", "rating", "summary"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["quotes"],
    "additionalProperties": False,
}


def _unrated(conn, author: str = None) -> list[dict]:
    """Paragraphs that have candidates and haven't been rated with the current model."""
    from comrade import db

    rated = {r[0] for r in conn.execute("SELECT hash FROM rated_paragraphs WHERE model = ?", (config.LLM_MODEL,))}
    where, params = "WHERE p.id IN (SELECT paragraph_id FROM quotes)", []
    if author:
        where += " AND p.work_id IN (SELECT id FROM works WHERE author LIKE ?)"
        params.append(f"%{author}%")
    ids = [p["id"] for p in _passages(conn, where, params).values() if text_hash(p["text"]) not in rated]
    passages = []
    for i in range(0, len(ids), 900):
        passages += db.get_passages(conn, ids[i:i + 900]).values()
    return sorted(passages, key=lambda p: p["id"])


def _rate_batch(batch: list[dict]) -> list[dict]:
    from comrade.picker import format_candidates

    system = RATE_SYSTEM.format(max_span=config.MAX_SPAN, max_chars=config.MAX_QUOTE_CHARS)
    user = f"<passages>\n{format_candidates(batch)}\n</passages>"
    for attempt in range(5):
        try:
            return llm.complete_json(system, user, RATE_SCHEMA, effort="low").get("quotes", [])
        except Exception:  # rate limits, timeouts, and malformed output are all worth a retry
            if attempt == 4:
                raise
            time.sleep(2 ** attempt * 3)


def _save(conn, batch: list[dict], picks: list[dict]) -> int:
    by_id = {p["id"]: p for p in batch}
    chosen = set()
    for q in picks:
        p = by_id.get(q.get("passage_id"))
        start, end, rating = q.get("start"), q.get("end"), q.get("rating")
        if not p or not all(isinstance(v, int) for v in (start, end, rating)):
            continue
        if not (0 <= start <= end < len(p["sentences"])) or end - start >= config.MAX_SPAN or not 3 <= rating <= 5:
            continue
        text = " ".join(p["sentences"][start:end + 1])
        if len(text) > config.MAX_QUOTE_CHARS or not whole_sentences(text):
            continue
        try:
            render.quote_text(p, start, end)
        except render.Rejected:
            continue
        h = text_hash(text)
        conn.execute("INSERT OR REPLACE INTO quote_scores (hash, rating, summary, model) VALUES (?,?,?,?)",
                     (h, rating, (q.get("summary") or "").strip(), config.LLM_MODEL))
        if not conn.execute("SELECT 1 FROM quotes WHERE paragraph_id = ? AND start = ? AND end = ?",
                            (p["id"], start, end)).fetchone():
            # The model can find a good quote that the rules missed, such as one that opens with "But"
            conn.execute("INSERT INTO quotes (paragraph_id, start, end, text, hash) VALUES (?,?,?,?,?)",
                         (p["id"], start, end, text, h))
        chosen.add(h)
    marks = ",".join("?" * len(by_id))
    for (h,) in conn.execute(f"SELECT hash FROM quotes WHERE paragraph_id IN ({marks})", list(by_id)).fetchall():
        if h not in chosen:
            conn.execute("INSERT OR IGNORE INTO quote_scores (hash, rating, summary, model) VALUES (?,0,NULL,?)",
                         (h, config.LLM_MODEL))
    conn.executemany("INSERT OR REPLACE INTO rated_paragraphs (hash, model) VALUES (?,?)",
                     [(text_hash(p["text"]), config.LLM_MODEL) for p in batch])
    _index(conn, f"WHERE q.paragraph_id IN ({marks})", list(by_id))
    conn.commit()
    return len(chosen)


def rate(conn, author: str = None, limit: int = None, batch_size: int = 8, workers: int = 8,
         progress=None, warn=print) -> tuple[int, int]:
    """Rate unrated paragraphs with the LLM. Returns (paragraphs rated, quotes chosen).

    Progress is saved after each batch, so you can stop and resume.
    """
    passages = _unrated(conn, author)[:limit]
    batches = [passages[i:i + batch_size] for i in range(0, len(passages), batch_size)]
    rated = chosen = 0
    with ThreadPoolExecutor(workers) as pool:
        futures = {pool.submit(_rate_batch, b): b for b in batches}
        for future in as_completed(futures):
            batch = futures[future]
            try:
                chosen += _save(conn, batch, future.result())
                rated += len(batch)
            except Exception as e:
                warn(f"Skipped {len(batch)} paragraphs after retries: {e}")
            if progress:
                progress(len(batch))
    return rated, chosen
