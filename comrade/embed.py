"""Paragraph embeddings via Ollama, stored as float32 blobs in SQLite."""
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


def embed_missing(conn, progress=None) -> int:
    """Embed every paragraph that has no vector for the current model."""
    rows = conn.execute(
        """SELECT p.id, p.text FROM paragraphs p
           LEFT JOIN embeddings e ON e.paragraph_id = p.id AND e.model = ?
           WHERE e.paragraph_id IS NULL""",
        (config.EMBED_MODEL,),
    ).fetchall()
    for i in range(0, len(rows), BATCH_SIZE):
        batch = rows[i:i + BATCH_SIZE]
        vecs = _embed([DOC_PREFIX + r["text"] for r in batch])
        conn.executemany(
            "INSERT OR REPLACE INTO embeddings (paragraph_id, model, vec) VALUES (?,?,?)",
            [(r["id"], config.EMBED_MODEL, v.tobytes()) for r, v in zip(batch, vecs)],
        )
        conn.commit()
        if progress:
            progress(len(batch))
    return len(rows)


def load_matrix(conn) -> tuple[np.ndarray, np.ndarray]:
    """Load all vectors for the current model as (ids, matrix)."""
    rows = conn.execute(
        "SELECT paragraph_id, vec FROM embeddings WHERE model = ?", (config.EMBED_MODEL,)
    ).fetchall()
    if not rows:
        return np.array([], dtype=np.int64), np.zeros((0, 0), dtype=np.float32)
    ids = np.array([r[0] for r in rows], dtype=np.int64)
    matrix = np.frombuffer(b"".join(r[1] for r in rows), dtype=np.float32).reshape(len(rows), -1)
    return ids, matrix
