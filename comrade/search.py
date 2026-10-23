"""Hybrid quote retrieval: BM25 (FTS5), quote embeddings, and summary embeddings,
merged with reciprocal rank fusion and weighted by the quote's LLM rating."""
import re

import requests

from comrade import db
from comrade import embed
from comrade.config import config

RRF_K = 60
POOL = 200  # results pulled from each retriever before fusion and filtering

STOPWORDS = set("""
a about above after again against all am an and any are as at be because been before being below
between both but by can could did do does doing down during each few for from further had has have
having he her here hers herself him himself his how i if in into is it its itself just me more most
my myself no nor not now of off on once only or other our ours ourselves out over own same she should
so some such than that the their theirs them themselves then there these they this those through to
too under until up very was we were what when where which while who whom why will with would you your
yours yourself yourselves im dont thats youre theyre isnt cant wont gonna like get got really
""".split())


def fts_query(text: str) -> str:
    terms = [t for t in re.findall(r"[a-z]+", text.lower()) if t not in STOPWORDS and len(t) > 2]
    terms = list(dict.fromkeys(terms))[:30]
    return " OR ".join(f'"{t}"' for t in terms)


# Weight on a quote's fused score by its LLM rating. Unrated quotes are neutral;
# 0 means the model read the paragraph and passed over this quote.
# The penalty for 0 is mild: the model chose at most 2 quotes per paragraph, so it
# passed over some famous lines in dense paragraphs.
RATING_WEIGHT = {None: 1.0, 0: 0.75, 3: 1.0, 4: 1.25, 5: 1.5}
MAX_PER_AUTHOR = 4  # so the most-published authors don't crowd out the rest


def bm25(conn, text: str, limit: int) -> list[int]:
    q = fts_query(text)
    if not q:
        return []
    rows = conn.execute(
        "SELECT rowid FROM quotes_fts WHERE quotes_fts MATCH ? ORDER BY bm25(quotes_fts) LIMIT ?",
        (q, limit),
    )
    return [r[0] for r in rows]


def search(conn, text: str, k: int = None, author: str = None, warn=print) -> list[dict]:
    """Return the top-k quotes for `text`, best first, without overlapping quotes from one paragraph."""
    k = k or config.CANDIDATES
    pool = POOL * 10 if author else POOL  # the author filter runs after retrieval
    ranked = [bm25(conn, text, pool)]
    try:
        query = embed.embed_query(text)
        ranked += [embed.nearest(conn, "text", query, pool), embed.nearest(conn, "summary", query, pool)]
    except requests.RequestException as e:
        warn(f"Embedding search skipped (Ollama unreachable at {config.OLLAMA_URL}): {e}")

    fused: dict[int, float] = {}
    for ids in ranked:
        for rank, qid in enumerate(ids):
            fused[qid] = fused.get(qid, 0.0) + 1.0 / (RRF_K + rank)
    if not fused:
        return []

    marks = ",".join("?" * len(fused))
    rows = {r["id"]: dict(r) for r in conn.execute(
        f"""SELECT q.id, q.paragraph_id, q.start, q.end, q.text, s.rating, s.summary FROM quotes q
            LEFT JOIN quote_scores s ON s.hash = q.hash WHERE q.id IN ({marks})""", list(fused))}
    for qid, row in rows.items():
        row["score"] = fused[qid] * RATING_WEIGHT.get(row["rating"], 1.0)

    skip = db.recently_posted(conn, config.REPEAT_DAYS)
    ordered = sorted((r for r in rows.values() if r["paragraph_id"] not in skip), key=lambda r: -r["score"])
    passages = db.get_passages(conn, list({r["paragraph_id"] for r in ordered}))
    results, taken, per_author = [], {}, {}
    for r in ordered:
        p = passages.get(r["paragraph_id"])
        if not p or (author and author.lower() not in p["author"].lower()):
            continue
        if not author and per_author.get(p["author"], 0) >= MAX_PER_AUTHOR:
            continue
        spans = taken.setdefault(r["paragraph_id"], [])
        if any(r["start"] <= e and s <= r["end"] for s, e in spans):
            continue  # overlaps a better quote from the same paragraph
        spans.append((r["start"], r["end"]))
        per_author[p["author"]] = per_author.get(p["author"], 0) + 1
        results.append({**r, "passage": p, **{f: p.get(f) for f in ("author", "title", "section", "year", "source_url")}})
        if len(results) >= k:
            break
    return results
