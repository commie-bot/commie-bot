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

# Book matter that isn't the author's text. Matched against whole section titles.
SKIP_SECTIONS = [
    r"copyright( page)?", r"(table of )?contents", r"acknowledge?ments?", r"about the (author|authors|book|publisher|layout)",
    r"index", r"(end|foot)?notes?", r"bibliography", r"select bibliography", r"glossary", r"chronology.*",
    r"suggested further reading", r"resources", r"dedication", r"praise", r"(half)?title page", r"imprint page",
    r"back cover", r"credits", r"key dates", r"also by .*", r"editor'?s'? note", r"editorial note",
    r"foreword.*", r"preface by .*", r"translator'?s'? (note|introduction)", r".*_fn\d.*", r"verso ebook license",
]

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

def parse_html(html: str, default_title: str = "") -> list[tuple[str, list[str]]]:
    soup = BeautifulSoup(html, "lxml")
    for tag in soup(["script", "style", "sup", "blockquote"]):  # block quotes are other writers' words
        tag.decompose()
    for tag in soup.find_all(class_=_is_quoted_class):
        tag.decompose()
    for a in soup.find_all("a", href=re.compile(r"^#")):
        if re.fullmatch(r"\s*\[?\d+[a-z]?\]?\s*", a.get_text()):
            a.decompose()
    for br in soup.find_all("br"):
        br.replace_with("\n")

    sections = [(default_title, [])]
    body = soup.body or soup
    for el in body.find_all(HEADINGS + ["p", "div"]):
        if el.name == "div" and el.find(HEADINGS + ["p", "div"]):
            continue  # only leaf divs hold text; some ebooks use them instead of <p>
        if set(el.get("class", [])) & SKIP_CLASSES:
            continue
        text = normalize(re.sub(r"(\w)-\n\s*(\w)", r"\1\2", el.get_text(" ")))
        if not text:
            continue
        if el.name in HEADINGS:
            if re.search(r"[A-Za-z0-9]", text):  # skip decorative headings like "* * *"
                sections.append((text, []))
        else:
            sections[-1][1].append(text)
    return [s for s in sections if s[1]]


def _is_quoted_class(classes) -> bool:
    """Editor insertions and epigraphs, which are not the author's words."""
    if not classes:
        return False
    names = classes.split() if isinstance(classes, str) else classes
    return any(c in EDITOR_CLASSES or "epi" in c.lower() for c in names)


def _toc_titles(book) -> dict:
    """Map each ebook document to the first table-of-contents title that points at it."""
    titles = {}

    def walk(entries):
        for entry in entries:
            if isinstance(entry, tuple):
                entry, children = entry
                walk([entry])
                walk(children)
            elif getattr(entry, "href", None):
                titles.setdefault(entry.href.split("#")[0].split("/")[-1], entry.title)

    walk(book.toc)
    return titles


def parse_epub(path: str) -> list[tuple[str, list[str]]]:
    import ebooklib
    from ebooklib import epub

    try:
        book = epub.read_epub(path, options={"ignore_ncx": False})
    except AttributeError:  # malformed table of contents
        book = epub.read_epub(path, options={"ignore_ncx": True})
    titles = _toc_titles(book)
    sections = []
    for item in book.get_items_of_type(ebooklib.ITEM_DOCUMENT):
        title = titles.get(item.get_name().split("/")[-1], "")
        sections.extend(parse_html(item.get_content().decode("utf-8", errors="ignore"), title))
    return sections


def parse_pdf(path: str, pages=None) -> list[tuple[str, list[str]]]:
    """Read text blocks page by page, dropping running headers and page numbers.

    A block that starts in lowercase continues the previous one, which joins
    sentences split across pages and columns.
    """
    import fitz
    from collections import Counter

    with fitz.open(path) as doc:
        first, last = pages or (1, len(doc))  # 1-based and inclusive, to leave out front and back matter
        pages = [[b[4] for b in doc[i].get_text("blocks") if b[6] == 0] for i in range(first - 1, last)]
    key = lambda t: re.sub(r"[\d\s]+", "", t.lower())
    repeats = Counter(k for blocks in pages for k in {key(b) for b in blocks if len(b) < 120})
    paragraphs = []
    for blocks in pages:
        for block in blocks:
            text = normalize(re.sub(r"-\n(?=[a-z])", "", re.sub(r"\[Page \w+ ?\]", "", block)))
            if not text or re.fullmatch(r"[\divxlc\s.-]+", text, re.I):
                continue  # page numbers
            if len(text) < 120 and repeats[key(text)] >= 3:
                continue  # running headers and footers
            if paragraphs and text[0].islower() and not re.search(r"[.?!:\"”’)]$", paragraphs[-1]):
                paragraphs[-1] += " " + text
            else:
                paragraphs.append(text)
    return [("", paragraphs)]


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


def parse_file(path: str, pages=None) -> list[tuple[str, list[str]]]:
    ext = os.path.splitext(path)[1].lower()
    if ext in (".html", ".htm"):
        with open(path, encoding="utf-8", errors="ignore") as f:
            return parse_html(f.read())
    if ext == ".epub":
        return parse_epub(path)
    if ext == ".pdf":
        return parse_pdf(path, pages)
    if ext in (".txt", ".md"):
        with open(path, encoding="utf-8", errors="ignore") as f:
            return parse_txt(f.read())
    raise ValueError(f"Unsupported file type: {path}")


def load_works(path: str = None) -> list[dict]:
    """Load works.yaml plus any files in its include list that exist (such as castro-db.yaml)."""
    path = path or config.WORKS_FILE
    with open(path) as f:
        data = yaml.safe_load(f)
    works = data["works"]
    for name in data.get("include", []):
        extra = os.path.join(os.path.dirname(path), name)
        if os.path.exists(extra):
            with open(extra) as f:
                works += (yaml.safe_load(f) or {}).get("works", [])
    works = list({w["id"]: w for w in works}.values())  # a later entry with the same id wins
    for w in works:
        missing = [k for k in ("id", "author", "title") if not w.get(k)]
        if missing:
            raise ValueError(f"Work {w.get('id', '?')} is missing {', '.join(missing)}")
        w.setdefault("file", f"{w['id']}.html")
        w.setdefault("source_url", w.get("url"))
    return works


def keep_section(title: str, work: dict, path: str) -> bool:
    """Apply the default book-matter filter and the work's own section rules."""
    title = title.strip()
    if work.get("only_sections"):
        return any(re.fullmatch(p, title, re.I) for p in work["only_sections"])
    if not title and path.lower().endswith(".epub"):
        return False  # untitled ebook documents are covers, front matter, and footnotes
    return not any(re.fullmatch(p, title, re.I) for p in SKIP_SECTIONS + work.get("skip_sections", []))


def build_sections(path: str, work: dict = None) -> list[tuple[str, list[tuple[str, list[str]]]]]:
    """Parse a file and attach sentence splits, dropping fragments too short to quote."""
    work = work or {}
    out = []
    for title, paragraphs in parse_file(path, work.get("pages")):
        if not keep_section(title, work, path):
            continue
        kept = [(p, split_sentences(p)) for p in paragraphs if len(p) >= MIN_PARAGRAPH_CHARS]
        if kept:
            out.append((title, kept))
    return out
