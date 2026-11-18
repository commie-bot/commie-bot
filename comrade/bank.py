"""The quote bank: a curated set of quotes that work as replies, tagged by topic.

Building it (offline): the tagging pass shows the LLM each rated quote with the
sentences around it. The model decides whether the quote works as a reply to a
general reader, can extend it to complete the thought, and tags it from
`comrade.tags.TAGS`.

Using it (per reply): the LLM tags the post and writes the point an ideal reply
would make. Search ranks bank quotes with those tags by how close their summary
is to that point. The picker then chooses the best one.
"""
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from xml.sax.saxutils import escape, quoteattr

import numpy as np
import requests

from comrade import db
from comrade import embed
from comrade import llm
from comrade import quotes
from comrade import render
from comrade.config import config
from comrade.tags import TAGS

CONTEXT = 2  # sentences shown on each side of a quote
MAX_TAGS = 4
RRF_K = 60

TAG_LIST = "\n".join(f"- {t}: {d}" for t, d in TAGS.items())

# --- Building the bank ------------------------------------------------------

TAG_SYSTEM = f"""You curate quotes for a Reddit bot that replies to posts and comments \
with one quote from a socialist or communist writer.

Each quote comes with the sentences around it. Each sentence shows its number in brackets and its \
length in characters in parentheses. The quote is the sentences between <quote> and </quote>.

For each quote, decide:
1. keep: true if the quote makes a point that a reader today understands without knowing \
the book, speech, or event it comes from, and that could answer a post or comment. \
false for quotes about a specific battle, statistic, local event, minor figure, or \
technical detail of theory.
2. add_before, add_after: how many neighboring sentences (0 to {CONTEXT}) to add to the \
quote on each side. Long quotes are fine, up to {{max_span}} sentences and {{max_chars}} \
characters in all. Add a sentence when it sets up the point, completes the thought, or \
adds the punchline, such as the sentence before a famous line that gives it context. \
Don't add one that repeats the point or wanders off it, and don't start the quote on a \
sentence that depends on an earlier one, such as one opening with "This" or "But".
3. tags: 1 to {MAX_TAGS} topics the quote speaks to, most relevant first, from this list:
{TAG_LIST}

Return every quote, including those you don't keep."""

TAG_SCHEMA = {
    "type": "object",
    "properties": {
        "quotes": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "id": {"type": "integer"},
                    "keep": {"type": "boolean"},
                    "add_before": {"type": "integer"},
                    "add_after": {"type": "integer"},
                    "tags": {"type": "array", "items": {"type": "string", "enum": list(TAGS)}},
                },
                "required": ["id", "keep", "add_before", "add_after", "tags"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["quotes"],
    "additionalProperties": False,
}


def _untagged(conn, author: str = None, min_rating: int = 3) -> list[dict]:
    """One row per rated quote text that the current model hasn't reviewed."""
    where, params = "", [min_rating, config.LLM_MODEL]
    if author:
        where = "AND p.work_id IN (SELECT id FROM works WHERE author LIKE ?)"
        params.append(f"%{author}%")
    rows = conn.execute(
        f"""SELECT MIN(q.id) AS id, q.hash FROM quotes q
            JOIN quote_scores s ON s.hash = q.hash
            JOIN paragraphs p ON p.id = q.paragraph_id
            LEFT JOIN bank_reviewed r ON r.hash = q.hash AND r.model = ?2
            WHERE s.rating >= ?1 AND r.hash IS NULL {where}
            GROUP BY q.hash ORDER BY MIN(q.id)""",
        params,
    ).fetchall()
    return [dict(r) for r in rows]


def _load(conn, ids: list[int]) -> list[dict]:
    marks = ",".join("?" * len(ids))
    rows = [dict(r) for r in conn.execute(
        f"""SELECT q.id, q.hash, q.paragraph_id, q.start, q.end, s.rating, s.summary FROM quotes q
            JOIN quote_scores s ON s.hash = q.hash WHERE q.id IN ({marks})""", ids)]
    passages = db.get_passages(conn, list({r["paragraph_id"] for r in rows}))
    return [{**r, "passage": passages[r["paragraph_id"]]} for r in rows if r["paragraph_id"] in passages]


def format_for_tagging(items: list[dict]) -> str:
    blocks = []
    for q in items:
        sentences = q["passage"]["sentences"]
        lo, hi = max(0, q["start"] - CONTEXT), min(len(sentences), q["end"] + CONTEXT + 1)
        lines = []
        for i in range(lo, hi):
            if i == q["start"]:
                lines.append("<quote>")
            lines.append(f"[{i}] ({len(sentences[i])}) {escape(sentences[i])}")
            if i == q["end"]:
                lines.append("</quote>")
        p = q["passage"]
        attrs = f'id="{q["id"]}" author={quoteattr(p["author"])} work={quoteattr(p["title"])}'
        blocks.append(f"<item {attrs}>\n" + "\n".join(lines) + "\n</item>")
    return "\n\n".join(blocks)


def _span(passage: dict, start: int, end: int, before: int, after: int) -> tuple[int, int, str]:
    """The quote extended by `before` and `after` sentences, if that's still a valid quote, else the original."""
    sentences = passage["sentences"]
    s, e = start - before, end + after
    if 0 <= s <= e < len(sentences) and e - s < config.BANK_MAX_SPAN and (before or after):
        text = " ".join(sentences[s:e + 1])
        if len(text) <= config.BANK_MAX_CHARS and quotes.whole_sentences(text):
            try:
                render.quote_text(passage, s, e)
                return s, e, text
            except render.Rejected:
                pass
    return start, end, " ".join(sentences[start:end + 1])


def _save(conn, items: list[dict], results: list[dict]) -> int:
    """Record the model's decisions, then rebuild the bank rows for these quotes."""
    by_id = {q["id"]: q for q in items}
    kept = 0
    for r in results:
        q = by_id.pop(r.get("id"), None)
        if not q:
            continue
        tags = list(dict.fromkeys(t for t in r.get("tags") or [] if t in TAGS))[:MAX_TAGS]
        keep = bool(r.get("keep")) and bool(tags)
        before, after = r.get("add_before"), r.get("add_after")
        before = min(max(before, 0), CONTEXT) if isinstance(before, int) else 0
        after = min(max(after, 0), CONTEXT) if isinstance(after, int) else 0
        if before or after:
            if _span(q["passage"], q["start"], q["end"], before, after)[:2] == (q["start"], q["end"]):
                before = after = 0  # the extension isn't a valid quote
        conn.execute(
            "INSERT OR REPLACE INTO bank_reviewed (hash, keep, tags, before, after, model) VALUES (?,?,?,?,?,?)",
            (q["hash"], int(keep), ",".join(tags), before, after, config.LLM_MODEL))
        kept += keep
    materialize(conn, [q["hash"] for q in items])
    return kept


SHORT = 60  # characters


def fragment(text: str) -> bool:
    """A quote that can't stand alone: a short one that points back to earlier text,
    such as "They simply destroy them.", or a headline in capitals."""
    first = quotes.re.match(r"[\W_]*(\w+)", text)
    if len(text) < SHORT and first and first.group(1).lower() in quotes.OPENERS:
        return True
    letters = [c for c in text if c.isalpha()]
    return bool(letters) and sum(c.isupper() for c in letters) > 0.6 * len(letters)


def materialize(conn, hashes: list[str] = None) -> int:
    """Rebuild bank rows from the stored decisions, for some quote hashes or all of them.

    Run after ingest, since paragraph ids change when a work is ingested again.
    """
    if hashes is None:
        conn.execute("DELETE FROM bank_tags")
        conn.execute("DELETE FROM bank")
        where, params = "", []
    else:
        if not hashes:
            return 0
        marks = ",".join("?" * len(hashes))
        conn.execute(f"DELETE FROM bank_tags WHERE bank_id IN (SELECT id FROM bank WHERE source_hash IN ({marks}))",
                     hashes)
        conn.execute(f"DELETE FROM bank WHERE source_hash IN ({marks})", hashes)
        where, params = f"AND r.hash IN ({marks})", hashes
    rows = conn.execute(
        f"""SELECT q.hash, q.paragraph_id, q.start, q.end, r.tags, r.before, r.after, s.rating, s.summary
            FROM bank_reviewed r
            JOIN quotes q ON q.id = (SELECT MIN(id) FROM quotes WHERE hash = r.hash)
            LEFT JOIN quote_scores s ON s.hash = r.hash
            WHERE r.keep = 1 AND r.model = ? {where}""",
        [config.LLM_MODEL] + params).fetchall()
    passages = {}
    pids = list({r["paragraph_id"] for r in rows})
    for i in range(0, len(pids), 900):
        passages.update(db.get_passages(conn, pids[i:i + 900]))
    attributed = {h: (own, speaker) for h, own, speaker in
                  conn.execute("SELECT hash, own, speaker FROM attributions")}
    n = 0
    for r in rows:
        p = passages.get(r["paragraph_id"])
        if not p:
            continue
        start, end, text = _span(p, r["start"], r["end"], r["before"], r["after"])
        if fragment(text):
            continue
        own, speaker = attributed.get(quotes.text_hash(text), (1, ""))
        if not own and not speaker:
            continue  # someone else's words, and we don't know whose
        cur = conn.execute(
            """INSERT INTO bank (source_hash, paragraph_id, start, end, text, rating, summary, speaker)
               VALUES (?,?,?,?,?,?,?,?)""",
            (r["hash"], r["paragraph_id"], start, end, text, r["rating"], r["summary"], speaker or None))
        conn.executemany("INSERT INTO bank_tags (bank_id, tag, rank) VALUES (?,?,?)",
                         [(cur.lastrowid, t, i) for i, t in enumerate(r["tags"].split(",")) if t in TAGS])
        n += 1
    conn.commit()
    return n


def _tag_batch(items: list[dict]) -> list[dict]:
    system = TAG_SYSTEM.format(max_span=config.BANK_MAX_SPAN, max_chars=config.BANK_MAX_CHARS)
    user = f"<items>\n{format_for_tagging(items)}\n</items>"
    for attempt in range(5):
        try:
            return llm.complete_json(system, user, TAG_SCHEMA, effort="low").get("quotes", [])
        except Exception:  # rate limits, timeouts, and malformed output are all worth a retry
            if attempt == 4:
                raise
            time.sleep(2 ** attempt * 3)


def build(conn, author: str = None, limit: int = None, batch_size: int = 20, workers: int = 8,
          progress=None, warn=print) -> tuple[int, int]:
    """Review rated quotes that aren't in the bank yet. Returns (reviewed, kept). Resumable."""
    todo = [r["id"] for r in _untagged(conn, author)][:limit]
    batches = [todo[i:i + batch_size] for i in range(0, len(todo), batch_size)]
    reviewed = kept = 0
    with ThreadPoolExecutor(workers) as pool:
        futures = {}
        for ids in batches:
            items = _load(conn, ids)
            futures[pool.submit(_tag_batch, items)] = items
        for future in as_completed(futures):
            items = futures[future]
            try:
                kept += _save(conn, items, future.result())
                reviewed += len(items)
            except Exception as e:
                warn(f"Skipped {len(items)} quotes after retries: {e}")
            if progress:
                progress(len(items))
    return reviewed, kept


# --- Using the bank ----------------------------------------------------------

CLASSIFY_SYSTEM = f"""You help a Reddit bot that replies to posts and comments with one \
quote from a socialist or communist writer.

Read the target text and return:
- tags: 1 to 3 topics a reply should speak to, most specific first, from this list. \
Use a broad tag such as class-struggle or capitalism only when no specific tag fits:
{TAG_LIST}
- point: the point the ideal reply quote would make, in plain modern English, at most 20 \
words. Write it as a claim, such as "Landlords profit from tenants without producing \
anything." If the target argues against socialism, the point answers that argument."""

CLASSIFY_SCHEMA = {
    "type": "object",
    "properties": {
        "tags": {"type": "array", "items": {"type": "string", "enum": list(TAGS)}},
        "point": {"type": "string"},
    },
    "required": ["tags", "point"],
    "additionalProperties": False,
}


def classify(text: str) -> dict:
    """Tags and the ideal reply point for a post: {tags: [...], point: str}."""
    result = llm.complete_json(CLASSIFY_SYSTEM, f"<target>\n{escape(text)}\n</target>", CLASSIFY_SCHEMA)
    tags = list(dict.fromkeys(t for t in result.get("tags") or [] if t in TAGS))[:3]
    return {"tags": tags, "point": (result.get("point") or "").strip()}


_cache = {}


def _matrix(conn) -> tuple[np.ndarray, np.ndarray]:
    """Bank ids and the summary vectors of their source quotes, cached until the bank changes."""
    key = (config.EMBED_MODEL, tuple(conn.execute("SELECT COUNT(*), MAX(id) FROM bank").fetchone()))
    if key not in _cache:
        rows = conn.execute(
            """SELECT b.id, v.vec FROM bank b
               JOIN quote_vectors v ON v.hash = b.source_hash AND v.kind = 'summary' AND v.model = ?""",
            (config.EMBED_MODEL,)).fetchall()
        ids = np.array([r[0] for r in rows], dtype=np.int64)
        matrix = (np.frombuffer(b"".join(r[1] for r in rows), dtype=np.float16).reshape(len(rows), -1)
                  .astype(np.float32) if rows else np.zeros((0, 0), dtype=np.float32))
        _cache.clear()
        _cache[key] = (ids, matrix)
    return _cache[key]


# Weight on a bank quote's fused score by its rating
RATING_WEIGHT = {3: 0.85, 4: 1.0, 5: 1.25}
MAX_PER_AUTHOR = 4


def search(conn, text: str, plan: dict, k: int = None, author: str = None, warn=print) -> list[dict]:
    """Bank quotes for a post, best first. `plan` comes from `classify`."""
    k = k or config.CANDIDATES
    ids, matrix = _matrix(conn)
    if not len(ids):
        return []
    ranked = []
    try:
        point = embed.embed_query(plan["point"] or text)
        post = embed.embed_query(text)
    except requests.RequestException as e:
        warn(f"Embedding search skipped (Ollama unreachable at {config.OLLAMA_URL}): {e}")
        point = post = None

    tag_hits = {}
    if plan["tags"]:
        marks = ",".join("?" * len(plan["tags"]))
        for bank_id, rank, n in conn.execute(
                f"""SELECT bank_id, MIN(rank), COUNT(*) FROM bank_tags WHERE tag IN ({marks})
                    GROUP BY bank_id""", plan["tags"]):
            tag_hits[bank_id] = (n, rank)

    if point is not None:
        point_sim = matrix @ point
        post_sim = matrix @ post
        order = np.argsort(-point_sim)
        ranked.append(ids[order[:300]].tolist())
        ranked.append(ids[np.argsort(-post_sim)[:300]].tolist())
        if tag_hits:
            # Within the post's tags: more matching tags first, then closeness to the point
            sims = dict(zip(ids.tolist(), point_sim.tolist()))
            tagged = sorted((i for i in tag_hits if i in sims),
                            key=lambda i: -(sims[i] + 0.05 * (tag_hits[i][0] - 1) - 0.02 * tag_hits[i][1]))
            ranked += [tagged[:300], tagged[:300]]  # counted twice: the tags are the strongest signal
    elif tag_hits:
        ranked.append(sorted(tag_hits, key=lambda i: (-tag_hits[i][0], tag_hits[i][1]))[:300])

    fused = {}
    for lst in ranked:
        for rank, bank_id in enumerate(lst):
            fused[bank_id] = fused.get(bank_id, 0.0) + 1.0 / (RRF_K + rank)
    if not fused:
        return []
    marks = ",".join("?" * len(fused))
    rows = {r["id"]: dict(r) for r in conn.execute(
        f"SELECT id, paragraph_id, start, end, text, rating, summary, speaker FROM bank WHERE id IN ({marks})", list(fused))}
    for bank_id, r in rows.items():
        r["score"] = fused[bank_id] * RATING_WEIGHT.get(r["rating"], 1.0)
    tags_by_id = {}
    for bank_id, tag in conn.execute(f"SELECT bank_id, tag FROM bank_tags WHERE bank_id IN ({marks}) ORDER BY rank",
                                     list(fused)):
        tags_by_id.setdefault(bank_id, []).append(tag)

    skip = db.recently_posted(conn, config.REPEAT_DAYS)
    ordered = sorted((r for r in rows.values() if r["paragraph_id"] not in skip), key=lambda r: -r["score"])
    passages = db.get_passages(conn, list({r["paragraph_id"] for r in ordered[:k * 20]}))
    results, per_author, seen = [], {}, set()
    for r in ordered:
        p = passages.get(r["paragraph_id"])
        if not p or (author and author.lower() not in p["author"].lower()):
            continue
        if not author and per_author.get(p["author"], 0) >= MAX_PER_AUTHOR:
            continue
        if r["text"] in seen:
            continue  # the same words in two editions of a work
        seen.add(r["text"])
        per_author[p["author"]] = per_author.get(p["author"], 0) + 1
        results.append({**r, "tags": tags_by_id.get(r["id"], []), "passage": p,
                        **{f: p.get(f) for f in ("author", "title", "section", "year", "source_url")}})
        if len(results) >= k:
            break
    return results

# --- Attribution ---------------------------------------------------------------

ATTRIBUTE_SYSTEM = """You check quotes for a bot that credits each quote to the person who said it.

Each item shows a quote, the paragraph it comes from, and the end of the paragraph \
before it. Decide whether the quote is the credited author's own words.

Authors often quote other people without quotation marks, especially in histories and \
in works of economics: a speech, letter, petition, newspaper, witness, or another \
writer, often introduced in the paragraph before with "wrote", "said", "declared", or a \
colon. Words the author is describing or paraphrasing in their own voice count as \
their own.

Song lyrics, poems, slogans, and document excerpts set off as their own paragraph are \
quotations too.

Return own: true if the words are the author's. If not, return speaker: who the words \
belong to, as the text identifies them, such as "Frederick Douglass" or "a petition by \
enslaved people in Massachusetts, 1773". Use an empty string if the text doesn't say. \
Never guess a speaker the text doesn't name."""

ATTRIBUTE_SCHEMA = {
    "type": "object",
    "properties": {
        "items": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {"id": {"type": "integer"}, "own": {"type": "boolean"},
                               "speaker": {"type": "string"}},
                "required": ["id", "own", "speaker"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["items"],
    "additionalProperties": False,
}

TAIL = 700  # characters of the previous paragraph to show


def _unattributed(conn, author: str = None) -> list[dict]:
    rows = conn.execute(
        """SELECT b.id, b.text, b.paragraph_id, p.text AS paragraph, p.work_id, p.idx, w.author, w.title,
                  (SELECT text FROM paragraphs WHERE work_id = p.work_id AND idx = p.idx - 1) AS previous
           FROM bank b JOIN paragraphs p ON p.id = b.paragraph_id JOIN works w ON w.id = p.work_id
           WHERE w.author LIKE ? ORDER BY b.id""", (f"%{author or ''}%",)).fetchall()
    done = {r[0] for r in conn.execute("SELECT hash FROM attributions WHERE model = ?", (config.LLM_MODEL,))}
    return [dict(r) for r in rows if quotes.text_hash(r["text"]) not in done]


def format_for_attribution(items: list[dict]) -> str:
    blocks = []
    for q in items:
        prev = (q["previous"] or "")[-TAIL:]
        para = q["paragraph"].replace(q["text"], f"<quote>{q['text']}</quote>", 1)
        hint = ""
        if prev.rstrip().endswith(":"):
            hint = ("<hint>The previous paragraph ends with a colon, so this paragraph is probably "
                    "a block quotation of whoever it introduces.</hint>\n")
        elif prev and len(prev) < 120 and not prev.rstrip().endswith((".", "!", "?", "”", '"')):
            hint = "<hint>The previous paragraph looks like a line of verse or a heading.</hint>\n"
        blocks.append(
            f'<item id="{q["id"]}" author={quoteattr(q["author"])} work={quoteattr(q["title"])}>\n'
            f"<previous_paragraph>{escape(prev)}</previous_paragraph>\n{hint}"
            f"<paragraph>{escape(para).replace('&lt;quote&gt;', '<quote>').replace('&lt;/quote&gt;', '</quote>')}"
            f"</paragraph>\n</item>")
    return "\n\n".join(blocks)


def _attribute_batch(items: list[dict]) -> list[dict]:
    user = f"<items>\n{format_for_attribution(items)}\n</items>"
    for attempt in range(5):
        try:
            return llm.complete_json(ATTRIBUTE_SYSTEM, user, ATTRIBUTE_SCHEMA, effort="medium").get("items", [])
        except Exception:
            if attempt == 4:
                raise
            time.sleep(2 ** attempt * 3)


def attribute(conn, author: str = None, limit: int = None, batch_size: int = 15, workers: int = 8,
              progress=None, warn=print) -> tuple[int, int]:
    """Check whose words each bank quote is. Returns (checked, not the author's own). Resumable."""
    todo = _unattributed(conn, author)[:limit]
    batches = [todo[i:i + batch_size] for i in range(0, len(todo), batch_size)]
    checked = others = 0
    with ThreadPoolExecutor(workers) as pool:
        futures = {pool.submit(_attribute_batch, b): b for b in batches}
        for future in as_completed(futures):
            batch = futures[future]
            try:
                by_id = {q["id"]: q for q in batch}
                for r in future.result():
                    q = by_id.pop(r.get("id"), None)
                    if not q:
                        continue
                    own = bool(r.get("own", True))
                    speaker = "" if own else (r.get("speaker") or "").strip()
                    conn.execute("INSERT OR REPLACE INTO attributions (hash, own, speaker, model) VALUES (?,?,?,?)",
                                 (quotes.text_hash(q["text"]), int(own), speaker, config.LLM_MODEL))
                    checked += 1
                    others += not own
                conn.commit()
            except Exception as e:
                warn(f"Skipped {len(batch)} quotes after retries: {e}")
            if progress:
                progress(len(batch))
    return checked, others


def stats(conn) -> dict:
    reviewed, kept = conn.execute(
        "SELECT COUNT(*), COALESCE(SUM(keep), 0) FROM bank_reviewed WHERE model = ?", (config.LLM_MODEL,)).fetchone()
    tags = dict(conn.execute("SELECT tag, COUNT(*) FROM bank_tags GROUP BY tag ORDER BY COUNT(*) DESC").fetchall())
    extended = conn.execute(
        """SELECT COUNT(DISTINCT b.id) FROM bank b JOIN quotes q ON q.hash = b.source_hash
           WHERE b.text <> q.text""").fetchone()[0]
    return {"reviewed": reviewed, "kept": kept, "extended": extended, "tags": tags}
