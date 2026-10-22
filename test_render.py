import pytest

import ingest
import render

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
    import llm

    out = '<think>{"not": "this"}</think>\n```json\n{"picks": [{"passage_id": 1, "start": 0, "end": 0, "reason": "x"}]}\n```'
    assert llm.parse_json(out)["picks"][0]["passage_id"] == 1
