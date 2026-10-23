"""Build the reply text from stored sentences and verify it is verbatim."""
import re

FOOTER = "^(I am a bot. Quotes are verbatim from the cited source.)"


# Most of a quote can be inside quotation marks before it counts as another writer's words.
MAX_QUOTED = 0.4


class Rejected(Exception):
    """A pick that can't be posted."""


class NotVerbatim(Rejected):
    pass


class QuotesAnotherWriter(Rejected):
    """The span is mostly words the author quotes from someone else."""


def _squash(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def quote_text(passage: dict, start: int, end: int) -> str:
    """Join stored sentences and confirm the result appears exactly in the paragraph."""
    quote = " ".join(passage["sentences"][start:end + 1])
    if _squash(quote) not in _squash(passage["text"]):
        raise NotVerbatim(f"Quote is not an exact substring of paragraph {passage['id']}")
    _check_not_quoted(passage, quote)
    return quote


def _quoted_mask(text: str) -> list[bool]:
    """For each character, whether it's inside double quotation marks."""
    mask, depth, straight = [], 0, False
    for ch in text:
        if ch == "“":
            depth += 1
        if ch == '"':
            straight = not straight
        mask.append(depth > 0 or straight or ch in '"“”')
        if ch == "”":
            depth = max(0, depth - 1)
    return mask


def _check_not_quoted(passage: dict, quote: str) -> None:
    """Reject a span that starts inside a quotation or is mostly quoted text.

    Ingest drops block quotes, but authors also quote other writers inline,
    and those words must not be credited to the author. A span that opens
    with an ellipsis is an excerpt from another source, even without
    quotation marks.
    """
    text, quote = _squash(passage["text"]), _squash(quote)
    start = text.index(quote)
    mask = _quoted_mask(text)
    inside_at_start = start > 0 and mask[start - 1] and text[start - 1] != "”" and mask[start]
    quoted = sum(mask[start:start + len(quote)]) / len(quote)
    if inside_at_start or quoted > MAX_QUOTED or quote.startswith(("...", "…", ". . .")):
        raise QuotesAnotherWriter(f"Quote in paragraph {passage['id']} is mostly another writer's words")


def citation(passage: dict) -> str:
    title = f"*{passage['title']}*"
    if passage.get("source_url"):
        title = f"[{title}]({passage['source_url']})"
    parts = [f"**{passage['author']}**", title]
    section = (passage.get("section") or "").strip()
    if section and section.lower() != passage["title"].lower():
        parts.append(section)
    cite = ", ".join(parts)
    if passage.get("year"):
        cite += f" ({passage['year']})"
    return cite


def render(passage: dict, start: int, end: int) -> str:
    quote = quote_text(passage, start, end)
    return f'> "{quote}"\n\n{citation(passage)}\n\n{FOOTER}'
