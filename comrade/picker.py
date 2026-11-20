"""Ask an LLM to pick the best of the retrieved quotes, in two steps.

1. Shortlist: one call reads every candidate and returns the ones worth a closer look.
2. Judge: one call scores how hard each shortlisted quote dunks on the target. It
   states what each quote says before it scores it, and it sees quotes the owner
   rated for other posts (the answer key), so the scores follow the owner's taste.

`python main.py evaluate` measures the judge against the answer key. The model only
returns quote IDs and scores. The quote text itself always comes from the
database, so it can't be paraphrased or invented.
"""
from xml.sax.saxutils import escape, quoteattr

from comrade import llm
from comrade import render

SYSTEM = """You shortlist quotes for a Reddit bot that replies to posts and comments \
with a quote from a socialist or communist writer.

You get the target text and a list of candidate quotes, each with an id, its \
author and work, and sometimes a one-line summary of its point. Choose up to \
{shortlist} quotes that could dunk on the target text: quotes that take apart what \
the target says, on their own, with no explanation needed. List them best first. A \
second step scores each one, so include every real contender. Return fewer only when \
fewer candidates are on the topic at all.

Rules:
- Pick a quote only if it answers what the target text says or argues. Sharing \
words with the target isn't enough.
- Prefer plain, forceful statements over dense theory. A quote of several sentences is \
fine when it makes the whole argument.
- A reply should read as a pointed answer to the target, not a lecture. Prefer a \
quote whose point a reader gets on the first read.
- Include different authors and works when the quality is similar.
- If no candidate fits, return an empty list."""

JUDGE_SYSTEM = """You judge quotes for a Reddit bot that replies to posts and comments \
with a quote from a socialist or communist writer. The reply is the quote alone, with \
its citation. Nobody explains it.

You get the target text, the claim it makes, and a list of quotes. Judge every quote: \
decide how hard each one dunks on the target. Compare them with each other, so the \
hardest hit gets the highest score and no two quotes tie. For each quote return these \
fields in order:
- id: the quote's id.
- says: what the quote asserts when read alone, with no context, in one sentence.
- reason: how the quote meets the claim, or why it doesn't, in one sentence.
- score: a number from 0 to 1.

Scoring. Judge the hit, not the topic match:
- 0.9 or more: a knockout. The quote meets the target's exact argument and reverses or \
demolishes it.
- About 0.7: a solid hit. It refutes the argument, but it's less sharp, or the reader \
must take one step to connect it.
- About 0.5: a glancing blow. It's on the topic and on the right side, but it doesn't \
engage the argument.
- 0.3 or less: no hit. It only shares a subject with the target, or it doesn't make \
sense without its original context.

Rules:
- When the target already agrees with the quote's side, such as a tenant angry at a \
landlord, score how hard the quote hits what the target is angry at.
- A definition, a description, or a piece of a story is not a dunk, however relevant. \
It can't score above 0.6.
- Length is not a fault. A quote of two to four sentences that makes the whole argument \
in plain words beats a short slogan. Abstract theory in dated language rarely lands.
- Most quotes are not knockouts. Use the whole range, and use two decimal places."""

EXAMPLES = """

These examples show how the bot's owner scored quotes for other targets. Match their taste:

{examples}"""

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

JUDGE_SCHEMA = {
    "type": "object",
    "properties": {
        "quotes": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {"id": {"type": "integer"}, "says": {"type": "string"},
                               "reason": {"type": "string"}, "score": {"type": "number"}},
                "required": ["id", "says", "reason", "score"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["quotes"],
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
        attrs = f'id="{q["id"]}" author={quoteattr(_cited(q))} work={quoteattr(q["title"])}'
        if q.get("year"):
            attrs += f' year="{q["year"]}"'
        summary = f"\n<summary>{escape(q['summary'])}</summary>" if q.get("summary") else ""
        blocks.append(f"<quote {attrs}>\n{escape(q['text'])}{summary}\n</quote>")
    return "\n\n".join(blocks)


PICKS = 5       # the best quote plus runners-up
SHORTLIST = 10  # quotes the judge scores


def _score(value) -> float:
    """The model's 0 to 1 dunk score, clamped. A missing or malformed score counts as 0."""
    try:
        return round(min(max(float(value), 0.0), 1.0), 2)
    except (TypeError, ValueError):
        return 0.0


def _cited(q: dict) -> str:
    return f'{q["speaker"]}, quoted by {q["author"]}' if q.get("speaker") else q["author"]


def shortlist(target_text: str, quotes: list[dict], point: str = None, n: int = SHORTLIST) -> list[dict]:
    """Return up to n validated contenders from search results, best first, not yet scored.

    `point` is the reply angle from `bank.classify`, shown to the model as a hint.
    """
    by_id = {q["id"]: q for q in quotes}
    hint = f"<suggested_point>{escape(point)}</suggested_point>\n\n" if point else ""
    user = (
        f"<target>\n{escape(target_text)}\n</target>\n\n{hint}"
        f"<candidates>\n{format_quotes(quotes)}\n</candidates>"
    )
    result = llm.complete_json(SYSTEM.format(shortlist=n), user, SCHEMA)

    picks = []
    for raw in result.get("picks", []):
        q = by_id.get(raw.get("quote_id"))
        if not q or any(p["quote_id"] == q["id"] for p in picks):
            continue  # hallucinated or repeated id
        try:
            text = render.quote_text(q["passage"], q["start"], q["end"])
        except render.Rejected:
            continue
        picks.append({"quote_id": q["id"], "passage": q["passage"], "start": q["start"], "end": q["end"],
                      "speaker": q.get("speaker"), "text": text, "score": 0.0, "reason": raw.get("reason", "")})
    return picks[:n]


def judge(target_text: str, picks: list[dict], claim: str = None, examples: str = "") -> list[dict]:
    """Score the picks against the target's claim. Returns them sorted, hardest hit first.

    `examples` is rated quotes for other posts, from `answerkey.examples`.
    """
    if not picks:
        return []
    shown = [{"id": i, "text": p["text"], "title": p["passage"]["title"], "speaker": p.get("speaker"),
              "author": p["passage"]["author"]} for i, p in enumerate(picks, 1)]
    stated = f"<claim>{escape(claim)}</claim>\n\n" if claim else ""
    user = f"<target>\n{escape(target_text)}\n</target>\n\n{stated}<quotes>\n{format_quotes(shown)}\n</quotes>"
    system = JUDGE_SYSTEM + (EXAMPLES.format(examples=examples) if examples else "")
    for attempt in (1, 2):  # one retry: the provider drops a connection now and then
        try:
            result = llm.complete_json(system, user, JUDGE_SCHEMA)
            break
        except Exception:
            if attempt == 2:
                raise
    verdicts = {v.get("id"): v for v in result.get("quotes", [])}
    scored = [{**p, "score": _score(verdicts.get(i, {}).get("score")),
               "reason": verdicts.get(i, {}).get("reason") or p["reason"]} for i, p in enumerate(picks, 1)]
    return sorted(scored, key=lambda p: -p["score"])  # stable, so ties keep the shortlist order


def pick(target_text: str, quotes: list[dict], point: str = None, n: int = PICKS, claim: str = None,
         examples: str = "") -> list[dict]:
    """Return the n best picks: [{passage, start, end, text, score, reason}], hardest hit first.

    `claim` is what the target asserts, from `bank.classify`. The judge scores against it.
    """
    return judge(target_text, shortlist(target_text, quotes, point), claim, examples)[:n]
