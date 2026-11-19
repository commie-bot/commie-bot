"""A hand-rated answer key for measuring the reply path.

`eval/key.json` holds sample posts, a pool of quotes for each one, and your grade for
each quote: 2 (dunk), 1 (OK), or 0 (miss). Grades are stored by the hash of the quote
text, and the pool keeps the text itself, so the key survives a re-ingest.
"""
import json
import os
import random
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from comrade import db
from comrade import quotes
from comrade import reply

PATH = Path(__file__).resolve().parent.parent / "eval" / "key.json"
GRADES = {0: "Miss", 1: "OK", 2: "Dunk"}
RETRIEVED = 5  # top search results added to each pool, beside the shortlist

_lock = threading.Lock()


def load() -> dict:
    if not PATH.exists():
        return {"posts": [], "grades": {}}
    return json.loads(PATH.read_text())


def _save(key: dict):
    PATH.parent.mkdir(exist_ok=True)
    tmp = PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(key, indent=1, ensure_ascii=False) + "\n")
    os.replace(tmp, PATH)


def _entry(q: dict) -> dict:
    """A pool entry from a search result or a pick."""
    p = q.get("passage") or q
    return {"hash": quotes.text_hash(q["text"]), "text": q["text"], "author": p["author"], "title": p["title"],
            "year": p.get("year"), "speaker": q.get("speaker") or ""}


def _extend(post: dict, found: list[dict]) -> int:
    """Add quotes the pool doesn't have yet. Returns how many were new."""
    have = {e["hash"] for e in post["pool"]}
    new = [e for e in map(_entry, found) if not (e["hash"] in have or have.add(e["hash"]))]
    post["pool"] += new
    return len(new)


def _contenders(text: str) -> list[dict]:
    """The unscored shortlist plus the top search results, in a fixed random order.

    The order is shuffled so the position on the page doesn't hint at the bot's ranking.
    """
    result = reply.find(db.connect(), text, judge=False)
    found = result["picks"] + result["candidates"][:RETRIEVED]
    random.Random(text).shuffle(found)
    return found


def add_post(text: str) -> dict:
    """Add a post and build its pool. Returns the new post."""
    found = _contenders(text)
    with _lock:
        key = load()
        post = {"id": f"p{max([int(p['id'][1:]) for p in key['posts']] or [0]) + 1:02d}", "text": text, "pool": []}
        _extend(post, found)
        key["posts"].append(post)
        _save(key)
    return post


def build(workers: int = 6) -> int:
    """Build the pool for every post that has none. Returns how many posts were filled."""
    empty = [p["text"] for p in load()["posts"] if not p["pool"]]
    with ThreadPoolExecutor(workers) as pool:
        found = dict(zip(empty, pool.map(_contenders, empty)))
    with _lock:
        key = load()
        for p in key["posts"]:
            if p["text"] in found and not p["pool"]:
                _extend(p, found[p["text"]])
        _save(key)
    return len(found)


def grade(post_id: str, quote_hash: str, value) -> None:
    """Record a grade, or clear it when value is None."""
    if value is not None and value not in GRADES:
        raise ValueError(f"Grade must be one of {sorted(GRADES)}")
    with _lock:
        key = load()
        post = next((p for p in key["posts"] if p["id"] == post_id), None)
        if not post or not any(e["hash"] == quote_hash for e in post["pool"]):
            raise ValueError("That quote isn't in the pool for that post")
        grades = key["grades"].setdefault(post_id, {})
        if value is None:
            grades.pop(quote_hash, None)
        else:
            grades[quote_hash] = value
        _save(key)


def evaluate(workers: int = 6) -> dict:
    """Run the reply path on every post and grade its top pick against the key.

    A top pick or runner-up you haven't rated joins the pool, so the page shows it next.
    """
    key = load()
    texts = [p["text"] for p in key["posts"]]
    with ThreadPoolExecutor(workers) as pool:
        results = dict(zip(texts, pool.map(lambda t: reply.find(db.connect(), t), texts)))

    rows, added = [], 0
    with _lock:
        key = load()
        for post in key["posts"]:
            result = results.get(post["text"])
            if result is None:
                continue
            grades = key["grades"].get(post["id"], {})
            picks = result["picks"]
            added += _extend(post, picks)
            top = picks[0] if picks else None
            # the best grade among everything the search returned: the most the picker could have scored
            reachable = [grades[h] for h in (quotes.text_hash(c["text"]) for c in result["candidates"]) if h in grades]
            rows.append({
                "id": post["id"], "text": post["text"],
                "quote": top["text"] if top else "", "author": top["passage"]["author"] if top else "",
                "score": top["score"] if top else None,
                "grade": grades.get(quotes.text_hash(top["text"])) if top else 0,
                "best": max(reachable) if reachable else None,
            })
        _save(key)

    rated = [r for r in rows if r["grade"] is not None]
    count = lambda rs, test: sum(1 for r in rs if test(r))
    return {
        "rows": rows, "posts": len(rows), "rated": len(rated), "added": added,
        "dunks": count(rated, lambda r: r["grade"] == 2),
        "misses": count(rated, lambda r: r["grade"] == 0),
        "mean": sum(r["grade"] for r in rated) / len(rated) if rated else None,
        # posts where the search found a dunk but the top pick wasn't one
        "missed_dunks": count(rated, lambda r: r["best"] == 2 and r["grade"] < 2),
    }
