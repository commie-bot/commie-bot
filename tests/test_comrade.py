import pytest

from comrade import ingest
from comrade import render

PASSAGE = {
    "id": 1,
    "author": "Karl Marx",
    "title": "Theses on Feuerbach",
    "section": "XI",
    "year": 1845,
    "source_url": None,
    "text": "The philosophers have only interpreted the world, in various ways; the point is to change it.",
    "sentences": ["The philosophers have only interpreted the world, in various ways; the point is to change it."],
}


def test_render_format():
    assert render.render(PASSAGE, 0, 0) == (
        '> "The philosophers have only interpreted the world, in various ways; the point is to change it."\n\n'
        "**Karl Marx**, *Theses on Feuerbach*, XI (1845)\n\n"
        f"{render.FOOTER}"
    )


def test_altered_quote_rejected():
    altered = dict(PASSAGE, sentences=["The philosophers have only interpreted the world; the point is to change it."])
    with pytest.raises(render.NotVerbatim):
        render.render(altered, 0, 0)


def test_section_matching_title_is_omitted():
    p = dict(PASSAGE, section="theses on feuerbach", source_url="https://example.org")
    assert render.citation(p) == "**Karl Marx**, [*Theses on Feuerbach*](https://example.org) (1845)"


def test_sentence_split_roundtrip():
    text = ingest.normalize("Workers of the world, unite! You have nothing to lose but your chains.[3]")
    sentences = ingest.split_sentences(text)
    assert len(sentences) == 2
    passage = dict(PASSAGE, text=text, sentences=sentences)
    assert render.quote_text(passage, 0, 1) == text


def test_parse_json_tolerates_reasoning_and_fences():
    from comrade import llm

    out = '<think>{"not": "this"}</think>\n```json\n{"picks": [{"passage_id": 1, "start": 0, "end": 0, "reason": "x"}]}\n```'
    assert llm.parse_json(out)["picks"][0]["passage_id"] == 1


def test_chapter_links_stay_in_work_and_skip_guides():
    from bs4 import BeautifulSoup

    from comrade.sources import marxists as fetch

    index = "https://example.org/works/1848/manifesto/index.htm"
    html = """<a href="ch01.htm">I</a><a href="ch01.htm#s2">I again</a><a href="guide.htm">Guide</a>
              <a href="../../index.htm">Up</a><a href="ch02.htm">II</a><a href="cover.jpg">img</a>"""
    links = fetch.chapter_links(index, BeautifulSoup(html, "html.parser"))
    assert links == ["https://example.org/works/1848/manifesto/ch01.htm",
                     "https://example.org/works/1848/manifesto/ch02.htm"]


FBIS_PAGE = """-DOCUMENT_TYPE-
SPEECH
-AUTHOR-
F. CASTRO
-HEADLINE-
MAY DAY CELEBRATION
-DATE-
19660502
-TEXT-
Text of Castro Speech

Havana Domestic Radio and Television Services in Spanish 2334 GMT 1 May 1966--F/E

(Text) Comrade guests, (applause) comrade macheteros:

We have worked hard this year, and the revo-
lution goes on.
-END-
"""


def test_fbis_speech_keeps_only_castros_words():
    from comrade.sources import castro_fbis as castro_db

    doc = castro_db.parse_doc(FBIS_PAGE)
    assert doc["type"] == "SPEECH"
    assert doc["paragraphs"] == ["Comrade guests, comrade macheteros:",
                                 "We have worked hard this year, and the revolution goes on."]
    assert not castro_db.is_report(doc["paragraphs"])


def test_fbis_wire_report_is_rejected():
    from comrade.sources import castro_fbis as castro_db

    assert castro_db.is_report(["Havana--Premier Fidel Castro delivered the final speech today."])
    summary = FBIS_PAGE.replace("(Text) Comrade", "(Editorial report) Comrade")
    assert castro_db.parse_doc(summary)["paragraphs"] == []
