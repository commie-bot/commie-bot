"""Download Fidel Castro's speeches from the Castro Speech Database.

The database (University of Texas LANIC, now retired) holds English translations
by the U.S. Foreign Broadcast Information Service (FBIS), 1959 to 1996. Pages
are read from the Internet Archive's copy.

Each page has metadata fields (-DOCUMENT_TYPE-, -HEADLINE-, -TEXT-, and others).
The fetcher keeps documents of type SPEECH, and keeps only the text after the
"(Text)" or "(Excerpts)" marker. Text before that marker is FBIS framing, and
documents without it are summaries in FBIS's words, not Castro's.
"""
import html
import os
from datetime import date, timedelta
import re
import time

import requests
import yaml

from fetch import DELAY, USER_AGENT

ARCHIVE = "https://web.archive.org/web/2019{flag}/http://lanic.utexas.edu/project/castro/"
YEARS = range(1959, 1997)
WORKS_FILE = "castro-db.yaml"
SUBDIR = "castro-db"
TRANSLATOR = "Foreign Broadcast Information Service"

TEXT_MARKER = re.compile(r"^\((Text|Excerpts)\)\s*", re.I)
# FBIS interpolations such as "(applause)" or "(passage indistinct)"
INTERPOLATION = re.compile(
    r"[(\[][^()\[\]]*\b(applause|indistinct|omitted|laughter|shouts?|shouting|crowd|chants?|"
    r"audience|recording|interruption|as heard|ovation|cheers?|words? missing|garbled)\b[^()\[\]]*[)\]]",
    re.I,
)
# Wire reports that FBIS filed as speeches: a dateline, or Castro in the third person
DATELINE = re.compile(r"^[A-Z][A-Za-z .,']{2,40}--")
REPORTED = re.compile(r"\b(Castro|the prime minister|the premier|he) (said|says|stated|added|declared|told|asserted|pointed out|noted|went on)\b", re.I)
SMALL_WORDS = {"a", "an", "and", "as", "at", "by", "for", "in", "of", "on", "or", "the", "to", "with", "vs"}

_session = requests.Session()
_session.headers["User-Agent"] = USER_AGENT


def _get(path: str) -> str:
    """Fetch the original page from the archive, without the Wayback toolbar."""
    for attempt in range(4):
        try:
            resp = _session.get(ARCHIVE.format(flag="id_") + path, timeout=120)
            if resp.status_code == 200:
                return resp.content.decode("latin-1")
            if resp.status_code == 404:
                resp.raise_for_status()
        except (requests.ConnectionError, requests.Timeout):
            pass
        time.sleep(10 * (attempt + 1))  # the archive throttles bursts
    raise requests.HTTPError(f"Gave up on {path}")


def page_url(year: int, name: str) -> str:
    return ARCHIVE.format(flag="") + f"db/{year}/{name}"


def list_year(year: int) -> list[str]:
    """File names of every document listed for one year, in date order."""
    page = _get(f"{year}/")
    names = re.findall(rf"db/{year}/(\d{{8}}(?:-\d+)?\.html)", page)
    return list(dict.fromkeys(names))


def _clean(text: str) -> str:
    """Tidy the gaps left by removed interpolations, such as "Comrades, , Guests"."""
    text = re.sub(r"\s+", " ", text)
    text = re.sub(r"([,;])(\s*[,;])+", r"\1", text)
    return re.sub(r" +([,.;:!?])", r"\1", text).strip()


def _is_header(block: str) -> bool:
    """FBIS framing at the top of a document: headline, dispatch line, or editor's note."""
    return (block.isupper() or "GMT" in block or block.startswith("(")
            or re.match(r"(Speech|Text|Live speech|Address|Remarks) (by|of)\b", block, re.I) is not None)


def parse_doc(page: str) -> dict:
    fields = dict(re.findall(r"^-([A-Z_]+)-\s*\n(.*?)\s*$", page, re.M))
    start, end = page.find("-TEXT-"), page.find("-END-")
    text = re.sub(r"<[^>]+>", "", page[start + len("-TEXT-"):end if end > start else None])
    text = re.sub(r"(\w)-\n\s*(\w)", r"\1\2", text)  # words hyphenated across line breaks
    text = re.sub(r"(?m)^\d+\s{3,}", "", text)  # page numbers at the start of a line
    blocks = [re.sub(r"\s+", " ", html.unescape(b)).strip() for b in re.split(r"\n\s*\n", text)]
    blocks = [b for b in blocks if b]
    head = blocks[:4]
    if any(re.match(r"\((Editorial report|Summary|Editor's note)", b, re.I) for b in head):
        paragraphs = []  # FBIS paraphrase, not Castro's words
    else:
        marker = next((i for i, b in enumerate(blocks) if TEXT_MARKER.match(b)), None)
        if marker is not None:
            paragraphs = [TEXT_MARKER.sub("", blocks[marker])] + blocks[marker + 1:]
        else:
            i = 0
            while i < len(blocks) and _is_header(blocks[i]):
                i += 1
            paragraphs = blocks[i:]
    paragraphs = [_clean(INTERPOLATION.sub("", p)) for p in paragraphs]
    return {
        "type": fields.get("DOCUMENT_TYPE", "").upper(),
        "author": fields.get("AUTHOR", ""),
        "headline": fields.get("HEADLINE", ""),
        "date": fields.get("DATE", ""),
        "paragraphs": [p for p in paragraphs if p],
    }


def is_report(paragraphs: list[str]) -> bool:
    if not paragraphs or DATELINE.match(paragraphs[0]):
        return True
    return len(REPORTED.findall(" ".join(paragraphs))) >= 3


def title_case(headline: str) -> str:
    words = headline.lower().split()
    out = [w if (i and w in SMALL_WORDS) else w[:1].upper() + w[1:] for i, w in enumerate(words)]
    return re.sub(r"\b(Cpr|Us|Un|Ussr|Oas|Olas|Cdr|Cia)\b", lambda m: m.group(1).upper(), " ".join(out))


def write_doc(doc: dict, title: str, path: str) -> None:
    body = "\n".join(f"<p>{html.escape(p)}</p>" for p in doc["paragraphs"])
    with open(path, "w", encoding="utf-8") as f:
        f.write(f'<!doctype html>\n<html><head><meta charset="utf-8"><title>{html.escape(title)}</title></head>\n'
                f"<body>\n{body}\n</body></html>\n")


def load_index(base_dir: str) -> dict:
    path = os.path.join(base_dir, WORKS_FILE)
    if not os.path.exists(path):
        return {"works": [], "skipped": []}
    with open(path) as f:
        data = yaml.safe_load(f) or {}
    return {"works": data.get("works", []), "skipped": data.get("skipped", [])}


def save_index(base_dir: str, index: dict) -> None:
    index["works"].sort(key=lambda w: w["id"])
    with open(os.path.join(base_dir, WORKS_FILE), "w") as f:
        f.write("# Generated by `python main.py fetch-castro`. Edit works.yaml, not this file.\n")
        yaml.safe_dump(index, f, sort_keys=False, allow_unicode=True)


def _day(yyyymmdd: str):
    try:
        return date(int(yyyymmdd[:4]), int(yyyymmdd[4:6]), int(yyyymmdd[6:8]))
    except (ValueError, TypeError):
        return None


def covered_dates(base_dir: str) -> set:
    """Days near a Castro speech already in works.yaml. FBIS dates its reports a day or two late."""
    with open(os.path.join(base_dir, "works.yaml")) as f:
        works = yaml.safe_load(f)["works"]
    days = set()
    for w in works:
        d = _day(str(w.get("date", ""))) if w["author"] == "Fidel Castro" else None
        if d:
            days |= {d + timedelta(days=n) for n in range(-1, 3)}
    return days


def _saved_paragraphs(path: str) -> list[str]:
    with open(path, encoding="utf-8") as f:
        return [html.unescape(p) for p in re.findall(r"<p>(.*?)</p>", f.read())]


def prune(base_dir: str, texts_dir: str, log=print) -> int:
    """Remove downloaded speeches that duplicate a works.yaml speech or are wire reports."""
    index, covered = load_index(base_dir), covered_dates(base_dir)
    removed = 0
    for w in list(index["works"]):
        path = os.path.join(texts_dir, w["file"])
        if _day(w["date"]) in covered:
            reason = "duplicate of a works.yaml speech"
        elif not os.path.exists(path) or is_report(_saved_paragraphs(path)):
            reason = "wire report, not Castro's words"
        else:
            continue
        index["works"].remove(w)
        index["skipped"].append(w["id"])
        if os.path.exists(path):
            os.remove(path)
        log(f"  {w['id']}: {reason}")
        removed += 1
    if removed:
        save_index(base_dir, index)
    return removed


def fetch(base_dir: str, texts_dir: str, years=YEARS, limit=None, log=print) -> int:
    """Download new speeches for the given years. Returns the number added.

    Progress is saved after each document, so an interrupted run picks up where it stopped.
    """
    os.makedirs(os.path.join(texts_dir, SUBDIR), exist_ok=True)
    index = load_index(base_dir)
    done = {w["id"] for w in index["works"]} | set(index["skipped"])
    covered = covered_dates(base_dir)
    added = 0
    for year in years:
        time.sleep(DELAY)
        try:
            names = list_year(year)
        except requests.HTTPError as e:
            log(f"{year}: no index ({e})")
            continue
        log(f"{year}: {len(names)} documents")
        for name in names:
            doc_id = "castro-" + name.removesuffix(".html")
            if doc_id in done:
                continue
            time.sleep(DELAY)
            try:
                doc = parse_doc(_get(f"db/{year}/{name}"))
            except requests.HTTPError as e:
                log(f"  {name}: {e}")
                continue
            if (doc["type"] != "SPEECH" or "CASTRO" not in doc["author"].upper() or is_report(doc["paragraphs"])
                    or _day(doc["date"]) in covered):
                index["skipped"].append(doc_id)
            else:
                title = title_case(doc["headline"]) or f"Speech of {doc['date']}"
                work = {
                    "id": doc_id, "author": "Fidel Castro", "title": title, "year": year,
                    "date": doc["date"], "type": "speech", "translator": TRANSLATOR,
                    "source_url": page_url(year, name), "file": f"{SUBDIR}/{doc_id}.html",
                }
                write_doc(doc, title, os.path.join(texts_dir, work["file"]))
                index["works"].append(work)
                added += 1
                log(f"  {name}: {title} ({len(doc['paragraphs'])} paragraphs)")
            done.add(doc_id)
            save_index(base_dir, index)
            if limit and added >= limit:
                return added
    return added
