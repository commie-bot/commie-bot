"""Ask an LLM to pick the best sentence spans from retrieved passages.

The model only returns passage IDs and sentence indexes. The quote text itself
always comes from the database, so it can't be paraphrased or invented.
"""
from xml.sax.saxutils import escape, quoteattr

import llm
from config import config

SYSTEM = """You pick quotes for a Reddit bot that replies to posts and comments with \
a quote from a socialist or communist writer.

You get the target text and a list of candidate passages. Each passage has numbered \
sentences. Choose up to 3 quotes that respond most directly and memorably to the \
target text.

Rules:
- A quote is a contiguous run of sentences from one passage: start and end are \
inclusive sentence numbers. Use at most {max_span} sentences; 1 or 2 is better.
- The quote must make sense on its own, without the surrounding passage. Avoid \
quotes that open with an unresolved reference such as "This", "He", or "Thus".
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
                "properties": {
                    "passage_id": {"type": "integer"},
                    "start": {"type": "integer"},
                    "end": {"type": "integer"},
                    "reason": {"type": "string"},
                },
                "required": ["passage_id", "start", "end", "reason"],
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
        sentences = "\n".join(f"[{i}] {escape(s)}" for i, s in enumerate(p["sentences"]))
        attrs = f'id="{p["id"]}" author={quoteattr(p["author"])} work={quoteattr(p["title"])}'
        if p.get("year"):
            attrs += f' year="{p["year"]}"'
        blocks.append(f"<passage {attrs}>\n{sentences}\n</passage>")
    return "\n\n".join(blocks)


def pick(target_text: str, passages: list[dict]) -> list[dict]:
    """Return validated picks: [{passage, start, end, reason}], best first."""
    by_id = {p["id"]: p for p in passages}
    user = (
        f"<target>\n{escape(target_text)}\n</target>\n\n"
        f"<candidates>\n{format_candidates(passages)}\n</candidates>"
    )
    result = llm.complete_json(SYSTEM.format(max_span=config.MAX_SPAN), user, SCHEMA)

    picks = []
    for raw in result.get("picks", [])[:3]:
        p = by_id.get(raw.get("passage_id"))
        start, end = raw.get("start"), raw.get("end")
        if not p or not isinstance(start, int) or not isinstance(end, int) \
                or not (0 <= start <= end < len(p["sentences"])):
            continue  # hallucinated id or out-of-range span
        end = min(end, start + config.MAX_SPAN - 1)
        picks.append({"passage": p, "start": start, "end": end, "reason": raw.get("reason", "")})
    return picks
