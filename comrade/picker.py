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
author and work, and sometimes a one-line summary of its point. Choose up to 3 \
quotes that respond most directly and memorably to the target text.

Rules:
- Pick a quote only if it answers what the target text says or argues. Sharing \
words with the target isn't enough.
- Prefer sharp, quotable lines over dense theory.
- Rank the best pick first. Use different authors or works across picks when the \
quality is similar.
- If no candidate fits, return an empty list."""

SCHEMA = {
    "type": "object",
    "properties": {
        "picks": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {"quote_id": {"type": "integer"}, "reason": {"type": "string"}},
                "required": ["quote_id", "reason"],
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
        attrs = f'id="{q["id"]}" author={quoteattr(q["author"])} work={quoteattr(q["title"])}'
        if q.get("year"):
            attrs += f' year="{q["year"]}"'
        summary = f"\n<summary>{escape(q['summary'])}</summary>" if q.get("summary") else ""
        blocks.append(f"<quote {attrs}>\n{escape(q['text'])}{summary}\n</quote>")
    return "\n\n".join(blocks)


def pick(target_text: str, quotes: list[dict]) -> list[dict]:
    """Return validated picks from search results: [{passage, start, end, reason}], best first."""
    by_id = {q["id"]: q for q in quotes}
    user = (
        f"<target>\n{escape(target_text)}\n</target>\n\n"
        f"<candidates>\n{format_quotes(quotes)}\n</candidates>"
    )
    result = llm.complete_json(SYSTEM, user, SCHEMA)

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
                      "reason": raw.get("reason", "")})
    return picks[:3]
