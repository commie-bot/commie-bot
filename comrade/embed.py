"""Quote and summary embeddings via Ollama, stored as float16 blobs in SQLite."""
import numpy as np
import requests

from comrade.config import config

BATCH_SIZE = 64
# nomic-embed-text expects task prefixes; other models ignore them harmlessly
DOC_PREFIX = "search_document: "
QUERY_PREFIX = "search_query: "


def _embed(texts: list[str]) -> np.ndarray:
    resp = requests.post(
        f"{config.OLLAMA_URL}/api/embed",
        json={"model": config.EMBED_MODEL, "input": texts},
        timeout=300,
    )
    resp.raise_for_status()
    vecs = np.array(resp.json()["embeddings"], dtype=np.float32)
    return vecs / np.linalg.norm(vecs, axis=1, keepdims=True)


def embed_query(text: str) -> np.ndarray:
    return _embed([QUERY_PREFIX + text])[0]


def missing(conn) -> list[tuple[str, str, str]]:
    """(hash, kind, text) for quotes and summaries that have no vector for the current model."""
    rows = conn.execute(
        """SELECT DISTINCT q.hash, 'text', q.text FROM quotes q
           LEFT JOIN quote_vectors v ON v.hash = q.hash AND v.kind = 'text' AND v.model = ?
           WHERE v.hash IS NULL
           UNION
           SELECT DISTINCT s.hash, 'summary', s.summary FROM quote_scores s
           JOIN quotes q ON q.hash = s.hash
           LEFT JOIN quote_vectors v ON v.hash = s.hash AND v.kind = 'summary' AND v.model = ?
           WHERE s.summary <> '' AND v.hash IS NULL""",
        (config.EMBED_MODEL, config.EMBED_MODEL),
    ).fetchall()
    return [tuple(r) for r in rows]


def embed_missing(conn, progress=None) -> int:
    """Embed every quote and summary that has no vector. Vectors are stored as float16."""
    rows = missing(conn)
    for i in range(0, len(rows), BATCH_SIZE):
        batch = rows[i:i + BATCH_SIZE]
        vecs = _embed([DOC_PREFIX + text for _, _, text in batch]).astype(np.float16)
        conn.executemany(
            "INSERT OR REPLACE INTO quote_vectors (hash, kind, model, vec) VALUES (?,?,?,?)",
            [(h, kind, config.EMBED_MODEL, v.tobytes()) for (h, kind, _), v in zip(batch, vecs)],
        )
        conn.commit()
        if progress:
            progress(len(batch))
    return len(rows)


_cache = {}


def load_matrix(conn, kind: str) -> tuple[np.ndarray, np.ndarray]:
    """All quote vectors of one kind as (quote ids, float16 matrix), cached until the counts change."""
    key = (kind, config.EMBED_MODEL,
           tuple(conn.execute("SELECT COUNT(*), MAX(id) FROM quotes").fetchone()),
           conn.execute("SELECT COUNT(*) FROM quote_vectors WHERE kind = ?", (kind,)).fetchone()[0])
    if key not in _cache:
        rows = conn.execute(
            """SELECT q.id, v.vec FROM quotes q
               JOIN quote_vectors v ON v.hash = q.hash AND v.kind = ? AND v.model = ?""",
            (kind, config.EMBED_MODEL),
        ).fetchall()
        ids = np.array([r[0] for r in rows], dtype=np.int64)
        matrix = (np.frombuffer(b"".join(r[1] for r in rows), dtype=np.float16).reshape(len(rows), -1)
                  if rows else np.zeros((0, 0), dtype=np.float16))
        for stale in [k for k in _cache if k[0] == kind]:
            del _cache[stale]  # keep one matrix per kind
        _cache[key] = (ids, matrix)
    return _cache[key]


def nearest(conn, kind: str, query: np.ndarray, limit: int) -> list[int]:
    """Quote ids by cosine similarity to the query, best first. Scores in chunks to keep memory flat."""
    ids, matrix = load_matrix(conn, kind)
    if not len(ids):
        return []
    scores = np.concatenate([matrix[i:i + 50000].astype(np.float32) @ query for i in range(0, len(ids), 50000)])
    top = np.argpartition(-scores, min(limit, len(scores) - 1))[:limit]
    return ids[top[np.argsort(-scores[top])]].tolist()
