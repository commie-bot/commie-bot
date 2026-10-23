"""Hybrid retrieval: BM25 (FTS5) and embeddings, merged with reciprocal rank fusion."""
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


def bm25(conn, text: str, limit: int) -> list[int]:
    q = fts_query(text)
    if not q:
        return []
    rows = conn.execute(
        "SELECT rowid FROM paragraphs_fts WHERE paragraphs_fts MATCH ? ORDER BY bm25(paragraphs_fts) LIMIT ?",
        (q, limit),
    )
    return [r[0] for r in rows]


def vector(conn, text: str, limit: int) -> list[int]:
    ids, matrix = embed.load_matrix(conn)
    if not len(ids):
        return []
    scores = matrix @ embed.embed_query(text)
    top = scores.argsort()[::-1][:limit]
    return ids[top].tolist()


def search(conn, text: str, k: int = None, author: str = None, warn=print) -> list[dict]:
    """Return the top-k passages for `text`, best first."""
    k = k or config.CANDIDATES
    ranked = [bm25(conn, text, POOL)]
    try:
        ranked.append(vector(conn, text, POOL))
    except requests.RequestException as e:
        warn(f"Embedding search skipped (Ollama unreachable at {config.OLLAMA_URL}): {e}")

    scores: dict[int, float] = {}
    for ids in ranked:
        for rank, pid in enumerate(ids):
            scores[pid] = scores.get(pid, 0.0) + 1.0 / (RRF_K + rank)

    skip = db.recently_posted(conn, config.REPEAT_DAYS)
    ordered = [pid for pid in sorted(scores, key=scores.get, reverse=True) if pid not in skip]
    passages = db.get_passages(conn, ordered)
    results = []
    for pid in ordered:
        p = passages.get(pid)
        if not p:
            continue
        if author and author.lower() not in p["author"].lower():
            continue
        p["score"] = scores[pid]
        results.append(p)
        if len(results) >= k:
            break
    return results
