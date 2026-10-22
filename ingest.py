"""Parse source texts into sections, paragraphs, and sentences."""
import os
import re

import pysbd
import yaml
from bs4 import BeautifulSoup

from config import config

MIN_PARAGRAPH_CHARS = 40
HEADINGS = ["h1", "h2", "h3", "h4", "h5"]
# marxists.org metadata, navigation, signatures, and endnote blocks
SKIP_CLASSES = {
    "information", "info", "footer", "updat", "endnote", "toc", "link", "skip", "title",
    "sig", "date", "index", "next", "transcriber", "ednote",
}
# Inline editor insertions such as "[in 1848]", which are not the author's words
EDITOR_CLASSES = {"inote", "enote", "anote"}

_segmenter = pysbd.Segmenter(language="en", clean=False)


def normalize(text: str) -> str:
    text = text.replace("­", "")  # soft hyphen
    text = re.sub(r"\[\d+[a-z]?\]", "", text)  # footnote markers like [12]
    text = re.sub(r"\[\s*\]", "", text)  # brackets left after removing footnote links
    text = re.sub(r"\s+", " ", text)
    text = re.sub(r" +([,.;:!?)\]’”])", r"\1", text)  # gaps left by removed tags
    return text.strip()


def split_sentences(text: str) -> list[str]:
    return [s.strip() for s in _segmenter.segment(text) if s.strip()]


# --- Parsers: each returns [(section_title, [paragraph_text])] ---

def parse_html(html: str) -> list[tuple[str, list[str]]]:
    soup = BeautifulSoup(html, "lxml")
    for tag in soup(["script", "style", "sup"]):
        tag.decompose()
    for tag in soup.find_all(class_=lambda c: c in EDITOR_CLASSES):
        tag.decompose()
    for a in soup.find_all("a", href=re.compile(r"^#")):
        if re.fullmatch(r"\s*\[?\d+[a-z]?\]?\s*", a.get_text()):
            a.decompose()

    sections = [("", [])]
    body = soup.body or soup
    for el in body.find_all(HEADINGS + ["p"]):
        if set(el.get("class", [])) & SKIP_CLASSES:
            continue
        text = normalize(el.get_text(" "))
        if not text:
            continue
        if el.name in HEADINGS:
            if re.search(r"[A-Za-z0-9]", text):  # skip decorative headings like "* * *"
                sections.append((text, []))
        else:
            sections[-1][1].append(text)
    return [s for s in sections if s[1]]


def parse_epub(path: str) -> list[tuple[str, list[str]]]:
    import ebooklib
    from ebooklib import epub

    book = epub.read_epub(path)
    sections = []
    for item in book.get_items_of_type(ebooklib.ITEM_DOCUMENT):
        sections.extend(parse_html(item.get_content().decode("utf-8", errors="ignore")))
    return sections


def parse_pdf(path: str) -> list[tuple[str, list[str]]]:
    import fitz

    with fitz.open(path) as doc:
        raw = "\n".join(page.get_text() for page in doc)
    raw = re.sub(r"-\n(?=[a-z])", "", raw)  # rejoin hyphenated line breaks
    raw = re.sub(r"(?m)^\s*\d+\s*$", "", raw)  # bare page numbers
    return [("", [normalize(p) for p in re.split(r"\n\s*\n", raw)])]


def parse_txt(text: str) -> list[tuple[str, list[str]]]:
    sections = [("", [])]
    for block in re.split(r"\n\s*\n", text):
        line = block.strip()
        heading = re.match(r"^(#+\s+|CHAPTER\b|Chapter\s+[IVXLC\d]+\b)", line)
        if heading and "\n" not in line and len(line) < 120:
            sections.append((line.lstrip("# ").strip(), []))
        elif line:
            sections[-1][1].append(normalize(line))
    return [s for s in sections if s[1]]


def parse_file(path: str) -> list[tuple[str, list[str]]]:
    ext = os.path.splitext(path)[1].lower()
    if ext in (".html", ".htm"):
        with open(path, encoding="utf-8", errors="ignore") as f:
            return parse_html(f.read())
    if ext == ".epub":
        return parse_epub(path)
    if ext == ".pdf":
        return parse_pdf(path)
    if ext in (".txt", ".md"):
        with open(path, encoding="utf-8", errors="ignore") as f:
            return parse_txt(f.read())
    raise ValueError(f"Unsupported file type: {path}")


def load_works(path: str = None) -> list[dict]:
    with open(path or config.WORKS_FILE) as f:
        works = yaml.safe_load(f)["works"]
    for w in works:
        missing = [k for k in ("id", "author", "title") if not w.get(k)]
        if missing:
            raise ValueError(f"Work {w.get('id', '?')} is missing {', '.join(missing)}")
        w.setdefault("file", f"{w['id']}.html")
        w.setdefault("source_url", w.get("url"))
    return works


def build_sections(path: str) -> list[tuple[str, list[tuple[str, list[str]]]]]:
    """Parse a file and attach sentence splits, dropping fragments too short to quote."""
    out = []
    for title, paragraphs in parse_file(path):
        kept = [(p, split_sentences(p)) for p in paragraphs if len(p) >= MIN_PARAGRAPH_CHARS]
        if kept:
            out.append((title, kept))
    return out
