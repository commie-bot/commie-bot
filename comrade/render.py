"""Build the reply text from stored sentences and verify it is verbatim."""
import re

FOOTER = "^(I am a bot. Quotes are verbatim from the cited source.)"


class NotVerbatim(Exception):
    pass


def _squash(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def quote_text(passage: dict, start: int, end: int) -> str:
    """Join stored sentences and confirm the result appears exactly in the paragraph."""
    quote = " ".join(passage["sentences"][start:end + 1])
    if _squash(quote) not in _squash(passage["text"]):
        raise NotVerbatim(f"Quote is not an exact substring of paragraph {passage['id']}")
    return quote


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
