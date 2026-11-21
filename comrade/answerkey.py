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

from xml.sax.saxutils import escape, quoteattr

from comrade import db
from comrade import picker
from comrade import quotes

PATH = Path(__file__).resolve().parent.parent / "eval" / "key.json"
GRADES = {0: "Miss", 1: "OK", 2: "Dunk"}
RETRIEVED = 5  # top search results added to each pool, beside the shortlist
EXAMPLE_POSTS = 5                  # rated posts shown to the judge
SHOWN_AS = {2: 0.9, 1: 0.6, 0: 0.2}  # the score a grade stands for in an example

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
    from comrade import reply  # reply imports this module for the examples

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


def add_picks(workers: int = 6) -> dict:
    """Run the whole reply path on every post and add its picks to the pools, to be rated.

    Use it after the bank or the search changes, so the key covers what the bot now picks.
    Returns {added, picks, long}: new pool quotes, picks seen, and picks over the automatic length limit.
    """
    from comrade import reply
    from comrade.config import config

    texts = [p["text"] for p in load()["posts"]]
    with ThreadPoolExecutor(workers) as pool:
        found = dict(zip(texts, pool.map(lambda t: reply.find(db.connect(), t)["picks"], texts)))
    picks = [q for qs in found.values() for q in qs]
    with _lock:
        key = load()
        added = sum(_extend(p, found.get(p["text"], [])) for p in key["posts"])
        _save(key)
    return {"added": added, "picks": len(picks), "long": sum(len(q["text"]) > config.MAX_QUOTE_CHARS for q in picks)}


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


def _rated(key: dict) -> list[tuple[dict, list[dict]]]:
    """Each post with the pool quotes that have a grade."""
    out = []
    for post in key["posts"]:
        grades = key["grades"].get(post["id"], {})
        rated = [dict(e, grade=grades[e["hash"]]) for e in post["pool"] if e["hash"] in grades]
        if rated:
            out.append((post, rated))
    return out


def examples(text: str, n: int = EXAMPLE_POSTS) -> str:
    """Rated quotes for n other posts, as examples of the owner's taste for the judge.

    The post with this text is left out, so `evaluate` never shows the judge the answers.
    The choice is seeded by the text, so the same post always gets the same examples.
    """
    rng = random.Random(text)
    others = [(p, rated) for p, rated in _rated(load()) if p["text"] != text]
    blocks = []
    for post, rated in rng.sample(others, min(n, len(others))):
        lines = []
        for g in sorted(SHOWN_AS, reverse=True):
            graded = [e for e in rated if e["grade"] == g]
            if graded:
                e = rng.choice(graded)
                lines.append(f'<quote author={quoteattr(e["author"])} score="{SHOWN_AS[g]}">{escape(e["text"])}</quote>')
        blocks.append(f"<example>\n<target>{escape(post['text'])}</target>\n" + "\n".join(lines) + "\n</example>")
    return "\n\n".join(blocks)


def _order(scores: list[float], grades: list[int]):
    """How often a better-graded quote outscores a worse one: 1 is perfect, 0.5 is chance."""
    pairs = [(a, b) for a, ga in zip(scores, grades) for b, gb in zip(scores, grades) if ga > gb]
    return sum(1 if a > b else 0.5 if a == b else 0 for a, b in pairs) / len(pairs) if pairs else None


def evaluate(runs: int = 3, workers: int = 6) -> dict:
    """Measure the judge: give it each post's rated quotes and grade the one it scores highest.

    Every quote the judge can pick has a grade, so no run leaves a pick unmeasured. This
    tests the scoring step only. The search and the shortlist decided what's in the pools.
    The judge varies between runs, so the result is the mean of several.
    """
    from comrade import bank

    posts = _rated(load())
    mean = lambda values: sum(values) / len(values) if values else None

    def one(item):
        post, rated = item
        picks = [{"text": e["text"], "passage": {"author": e["author"], "title": e["title"]},
                  "speaker": e["speaker"], "grade": e["grade"], "reason": ""} for e in rated]
        claim = bank.classify(post["text"])["claim"]
        tops, orders = [], []
        for _ in range(runs):
            scored = picker.judge(post["text"], picks, claim, examples(post["text"]))
            tops.append(scored[0])
            orders.append(_order([p["score"] for p in scored], [p["grade"] for p in scored]))
        grades = [t["grade"] for t in tops]
        return {"id": post["id"], "text": post["text"], "quote": tops[0]["text"], "author": tops[0]["passage"]["author"],
                "score": tops[0]["score"], "grades": grades, "order": mean([o for o in orders if o is not None]),
                "dunks_in_pool": sum(e["grade"] == 2 for e in rated), "pool": len(rated)}

    with ThreadPoolExecutor(workers) as pool:
        rows = list(pool.map(one, posts))
    picks = [g for r in rows for g in r["grades"]]
    return {
        "rows": rows, "posts": len(rows), "runs": runs,
        "dunk_rate": mean([g == 2 for g in picks]), "miss_rate": mean([g == 0 for g in picks]),
        "mean": mean(picks), "order": mean([r["order"] for r in rows if r["order"] is not None]),
        "chance": mean([r["dunks_in_pool"] / r["pool"] for r in rows]),  # a random pick's dunk rate
        "unrated": sum(len(p["pool"]) for p in load()["posts"]) - sum(r["pool"] for r in rows),
    }
