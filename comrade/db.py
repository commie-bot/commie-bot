import sqlite3
import time

from comrade.config import config

SCHEMA = """
CREATE TABLE IF NOT EXISTS works (
    id TEXT PRIMARY KEY,
    author TEXT NOT NULL,
    title TEXT NOT NULL,
    year INTEGER,
    type TEXT,
    translator TEXT,
    source_url TEXT,
    file TEXT
);
CREATE TABLE IF NOT EXISTS sections (
    id INTEGER PRIMARY KEY,
    work_id TEXT NOT NULL REFERENCES works(id),
    idx INTEGER NOT NULL,
    title TEXT
);
CREATE TABLE IF NOT EXISTS paragraphs (
    id INTEGER PRIMARY KEY,
    work_id TEXT NOT NULL REFERENCES works(id),
    section_id INTEGER REFERENCES sections(id),
    idx INTEGER NOT NULL,
    text TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS paragraphs_work ON paragraphs(work_id);
CREATE TABLE IF NOT EXISTS sentences (
    paragraph_id INTEGER NOT NULL REFERENCES paragraphs(id),
    idx INTEGER NOT NULL,
    text TEXT NOT NULL,
    PRIMARY KEY (paragraph_id, idx)
);
CREATE VIRTUAL TABLE IF NOT EXISTS paragraphs_fts USING fts5(text);
CREATE TABLE IF NOT EXISTS embeddings (
    paragraph_id INTEGER PRIMARY KEY REFERENCES paragraphs(id),
    model TEXT NOT NULL,
    vec BLOB NOT NULL
);
-- Quote candidates: runs of 1 to 3 sentences that can stand alone. Rebuilt from
-- sentences by `extract`. Scores and vectors are keyed by a hash of the text, so
-- they survive re-ingest.
CREATE TABLE IF NOT EXISTS quotes (
    id INTEGER PRIMARY KEY,
    paragraph_id INTEGER NOT NULL REFERENCES paragraphs(id),
    start INTEGER NOT NULL,
    end INTEGER NOT NULL,
    text TEXT NOT NULL,
    hash TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS quotes_paragraph ON quotes(paragraph_id);
CREATE INDEX IF NOT EXISTS quotes_hash ON quotes(hash);
CREATE VIRTUAL TABLE IF NOT EXISTS quotes_fts USING fts5(text);
-- LLM ratings (1 to 5) and one-line summaries. Rating 0 means the model read the
-- paragraph and didn't choose this quote.
CREATE TABLE IF NOT EXISTS quote_scores (
    hash TEXT PRIMARY KEY,
    rating INTEGER NOT NULL,
    summary TEXT,
    model TEXT NOT NULL
);
-- Paragraphs the LLM has rated, by a hash of their text
CREATE TABLE IF NOT EXISTS rated_paragraphs (
    hash TEXT PRIMARY KEY,
    model TEXT NOT NULL
);
-- kind is "text" (the quote) or "summary" (its LLM summary)
CREATE TABLE IF NOT EXISTS quote_vectors (
    hash TEXT NOT NULL,
    kind TEXT NOT NULL,
    model TEXT NOT NULL,
    vec BLOB NOT NULL,
    PRIMARY KEY (hash, kind, model)
);
CREATE TABLE IF NOT EXISTS posted (
    id INTEGER PRIMARY KEY,
    paragraph_id INTEGER NOT NULL,
    start INTEGER NOT NULL,
    end INTEGER NOT NULL,
    reddit_id TEXT,
    permalink TEXT,
    posted_at REAL NOT NULL
);
"""


def connect(path: str = None) -> sqlite3.Connection:
    conn = sqlite3.connect(path or config.DB_PATH, timeout=60)  # embed and rate can run at the same time
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA foreign_keys = ON")
    conn.executescript(SCHEMA)
    return conn


def delete_work(conn: sqlite3.Connection, work_id: str):
    """Remove a work and everything derived from it, so re-ingest is idempotent."""
    ids = [r[0] for r in conn.execute("SELECT id FROM paragraphs WHERE work_id = ?", (work_id,))]
    if ids:
        marks = ",".join("?" * len(ids))
        conn.execute(f"DELETE FROM sentences WHERE paragraph_id IN ({marks})", ids)
        conn.execute(f"DELETE FROM embeddings WHERE paragraph_id IN ({marks})", ids)
        conn.execute(f"DELETE FROM paragraphs_fts WHERE rowid IN ({marks})", ids)
        quote_ids = [r[0] for r in conn.execute(f"SELECT id FROM quotes WHERE paragraph_id IN ({marks})", ids)]
        for i in range(0, len(quote_ids), 900):
            chunk = quote_ids[i:i + 900]
            conn.execute(f"DELETE FROM quotes_fts WHERE rowid IN ({','.join('?' * len(chunk))})", chunk)
        conn.execute(f"DELETE FROM quotes WHERE paragraph_id IN ({marks})", ids)
    conn.execute("DELETE FROM paragraphs WHERE work_id = ?", (work_id,))
    conn.execute("DELETE FROM sections WHERE work_id = ?", (work_id,))
    conn.execute("DELETE FROM works WHERE id = ?", (work_id,))


def insert_work(conn: sqlite3.Connection, work: dict, sections: list[tuple[str, list[tuple[str, list[str]]]]]):
    """Insert a work. `sections` is [(section_title, [(paragraph_text, [sentences])])]."""
    delete_work(conn, work["id"])
    conn.execute(
        "INSERT INTO works (id, author, title, year, type, translator, source_url, file) VALUES (?,?,?,?,?,?,?,?)",
        (work["id"], work["author"], work["title"], work.get("year"), work.get("type"),
         work.get("translator"), work.get("source_url"), work.get("file")),
    )
    p_idx = 0
    for s_idx, (title, paragraphs) in enumerate(sections):
        cur = conn.execute(
            "INSERT INTO sections (work_id, idx, title) VALUES (?,?,?)", (work["id"], s_idx, title)
        )
        section_id = cur.lastrowid
        for text, sentences in paragraphs:
            cur = conn.execute(
                "INSERT INTO paragraphs (work_id, section_id, idx, text) VALUES (?,?,?,?)",
                (work["id"], section_id, p_idx, text),
            )
            pid = cur.lastrowid
            conn.execute("INSERT INTO paragraphs_fts (rowid, text) VALUES (?,?)", (pid, text))
            conn.executemany(
                "INSERT INTO sentences (paragraph_id, idx, text) VALUES (?,?,?)",
                [(pid, i, s) for i, s in enumerate(sentences)],
            )
            p_idx += 1
    conn.commit()
    return p_idx


def get_passages(conn: sqlite3.Connection, paragraph_ids: list[int]) -> dict[int, dict]:
    """Return paragraph text, sentences, and citation metadata keyed by paragraph id."""
    if not paragraph_ids:
        return {}
    marks = ",".join("?" * len(paragraph_ids))
    rows = conn.execute(
        f"""SELECT p.id, p.text, s.title AS section, w.id AS work_id, w.author, w.title,
                   w.year, w.type, w.source_url
            FROM paragraphs p
            JOIN works w ON w.id = p.work_id
            LEFT JOIN sections s ON s.id = p.section_id
            WHERE p.id IN ({marks})""",
        paragraph_ids,
    ).fetchall()
    passages = {r["id"]: dict(r) for r in rows}
    for r in conn.execute(
        f"SELECT paragraph_id, idx, text FROM sentences WHERE paragraph_id IN ({marks}) ORDER BY paragraph_id, idx",
        paragraph_ids,
    ):
        passages[r["paragraph_id"]].setdefault("sentences", []).append(r["text"])
    return passages


def recently_posted(conn: sqlite3.Connection, days: int) -> set[int]:
    cutoff = time.time() - days * 86400
    return {r[0] for r in conn.execute("SELECT paragraph_id FROM posted WHERE posted_at >= ?", (cutoff,))}


def record_post(conn: sqlite3.Connection, paragraph_id: int, start: int, end: int, reddit_id: str, permalink: str):
    conn.execute(
        "INSERT INTO posted (paragraph_id, start, end, reddit_id, permalink, posted_at) VALUES (?,?,?,?,?,?)",
        (paragraph_id, start, end, reddit_id, permalink, time.time()),
    )
    conn.commit()
