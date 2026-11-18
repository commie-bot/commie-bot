"""Ask an LLM to pick the best of the retrieved quotes.

The model only returns quote IDs. The quote text itself always comes from the
database, so it can't be paraphrased or invented.
"""
from xml.sax.saxutils import escape, quoteattr

from comrade import llm
from comrade import render

SYSTEM = """You pick quotes for a Reddit bot that replies to posts and comments with \
a quote from a socialist or communist writer.

You get the target text and a list of candidate quotes, each with an id, its \
author and work, and sometimes a one-line summary of its point. Choose the single \
quote that responds most directly and memorably to the target text. The bot posts \
your first pick. Add {runners_up} runners-up after it, best first. Return fewer only \
when fewer candidates are on the topic at all; the score shows how well each one fits.

Rules:
- Pick a quote only if it answers what the target text says or argues. Sharing \
words with the target isn't enough.
- Prefer sharp, quotable lines over dense theory.
- A reply should read as a pointed answer to the target, not a lecture. Prefer a \
quote whose point a reader gets on the first read.
- Use different authors or works for the runners-up when the quality is similar.
- Give each pick a score from 0 to 1 for how well it answers the target: 0.9 or more \
is a direct, memorable answer; about 0.7 is on point but less sharp; about 0.5 is \
on the topic but doesn't answer the argument. Score honestly: if the best candidate \
is a loose fit, give it a low score.
- If no candidate fits, return an empty list."""

SCHEMA = {
    "type": "object",
    "properties": {
        "picks": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {"quote_id": {"type": "integer"}, "score": {"type": "number"},
                               "reason": {"type": "string"}},
                "required": ["quote_id", "score", "reason"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["picks"],
    "additionalProperties": False,
}


def format_candidates(passages: list[dict]) -> str:
    blocks = []
    for p in passages:
        sentences = "\n".join(f"[{i}] ({len(s)}) {escape(s)}" for i, s in enumerate(p["sentences"]))
        attrs = f'id="{p["id"]}" author={quoteattr(p["author"])} work={quoteattr(p["title"])}'
        if p.get("year"):
            attrs += f' year="{p["year"]}"'
        blocks.append(f"<passage {attrs}>\n{sentences}\n</passage>")
    return "\n\n".join(blocks)


def format_quotes(quotes: list[dict]) -> str:
    blocks = []
    for q in quotes:
        author = f'{q["speaker"]}, quoted by {q["author"]}' if q.get("speaker") else q["author"]
        attrs = f'id="{q["id"]}" author={quoteattr(author)} work={quoteattr(q["title"])}'
        if q.get("year"):
            attrs += f' year="{q["year"]}"'
        summary = f"\n<summary>{escape(q['summary'])}</summary>" if q.get("summary") else ""
        blocks.append(f"<quote {attrs}>\n{escape(q['text'])}{summary}\n</quote>")
    return "\n\n".join(blocks)


PICKS = 5  # the best quote plus runners-up


def _score(value) -> float:
    """The model's 0 to 1 fit score, clamped. A missing or malformed score counts as 0."""
    try:
        return round(min(max(float(value), 0.0), 1.0), 2)
    except (TypeError, ValueError):
        return 0.0


def pick(target_text: str, quotes: list[dict], point: str = None, n: int = PICKS) -> list[dict]:
    """Return validated picks from search results: [{passage, start, end, reason}], best first.

    `point` is the reply angle from `bank.classify`, shown to the model as a hint.
    """
    by_id = {q["id"]: q for q in quotes}
    hint = f"<suggested_point>{escape(point)}</suggested_point>\n\n" if point else ""
    user = (
        f"<target>\n{escape(target_text)}\n</target>\n\n{hint}"
        f"<candidates>\n{format_quotes(quotes)}\n</candidates>"
    )
    result = llm.complete_json(SYSTEM.format(runners_up=max(n - 1, 0)), user, SCHEMA)

    picks = []
    for raw in result.get("picks", []):
        q = by_id.get(raw.get("quote_id"))
        if not q or any(p["quote_id"] == q["id"] for p in picks):
            continue  # hallucinated or repeated id
        try:
            render.quote_text(q["passage"], q["start"], q["end"])
        except render.Rejected:
            continue
        picks.append({"quote_id": q["id"], "passage": q["passage"], "start": q["start"], "end": q["end"],
                      "speaker": q.get("speaker"), "score": _score(raw.get("score")),
                      "reason": raw.get("reason", "")})
    picks.sort(key=lambda p: -p["score"])  # stable, so ties keep the model's order
    return picks[:n]
