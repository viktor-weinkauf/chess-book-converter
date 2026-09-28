import json

import pytest

import chess_converter as c
import chess_batch
import comment_text as text
from conversion_quality import report_path_for


@pytest.mark.parametrize("broken,expected", [
    ("принося-\nщей", "приносящей"),
    ("Одна-\nко", "Однако"),
    ("заби-\nрая", "забирая"),
    ("комбина-\nция", "комбинация"),
    ("продолже-\nние", "продолжение"),
    ("королев-\nском", "королевском"),
    ("воз-\nможность", "возможность"),
    ("по-\nпрежнему", "по-прежнему"),
    ("кто-\nто", "кто-то"),
])
def test_exact_dictionary_reading_preserves_real_hyphens(broken, expected):
    assert text.russian_dictionary() is not None, "Install requirements.txt before running tests"
    original = broken.split("\n")
    evidence = []
    lines = text.repair_lines(original, 18, evidence)
    assert lines == [expected, ""]
    assert original == broken.split("\n")
    assert evidence[0]["source_lines"] == original
    assert evidence[0]["replacement"] == expected
    assert evidence[0]["page"] == 18


@pytest.mark.parametrize("source", [
    "комбина- ция", "комбина-\n\nция", "комбина —\nция",
    "комбина--\nция", "комбина-\nЦия", "комбинa-\nция",
    "Nb1-\nd2", "Ла1-\nа2", "0-\n0", "комбина-\nция-атака",
    "неизвежд-\nабрака", "по-\nнемецкн", "за-\nмоёк",
])
def test_uncertain_text_notation_and_paragraph_breaks_are_unchanged(source):
    lines = source.split("\n")
    assert text.repair_lines(lines, 1, []) == lines


def test_ambiguous_dictionary_reading_is_not_chosen():
    class Dictionary:
        def word_is_known(self, word, *, strict):
            assert strict
            return True
    original = ["какой-", "нибудь"]
    from unittest.mock import patch
    with patch.object(text, "russian_dictionary", return_value=Dictionary()):
        evidence = []
        assert text.repair_lines(original, 1, evidence) == original
    assert evidence[0]["reason"] == "ambiguous" and not evidence[0]["applied"]


def test_missing_dictionary_keeps_text_and_reports_reason(monkeypatch):
    monkeypatch.setattr(text, "russian_dictionary", lambda: None)
    evidence = []
    assert text.repair_lines(["комбина-", "ция"], 1, evidence) == ["комбина-", "ция"]
    assert evidence[0]["reason"] == "dictionary_unavailable"


def test_multiple_repairs_preserve_line_numbers_and_do_not_invent_paragraphs():
    page = "1.e4 комбина-\nция\ne5 2.Nf3 Одна-\nко Nc6 3.Bb5 a6 1-0"
    evidence = []
    tokens, lines = c.tokenize([page], text_evidence=evidence)
    assert lines == page.split("\n")
    assert not any(t[0] == "paragraph" for t in tokens)
    assert next(t for t in tokens if t[1] == "Nc6")[3] == 3
    visual = [dict(kind="coordinate", replacement="Nc6", page=1, line=3,
                   dpi=300, reading="whole_word")]
    mapped = c.visual_token_evidence(tokens, [page], 1, visual)
    assert [tokens[i][1] for i in mapped] == ["Nc6"]


def test_page_break_cannot_join_a_word():
    tokens, _ = c.tokenize(["1.e4 комбина-", "ция e5 2.Nf3 Nc6 3.Bb5 a6 1-0"])
    assert "комбинация" not in [t[1] for t in tokens]


@pytest.mark.parametrize("ocr", [False, True])
def test_comment_and_branch_introduction_remain_on_the_same_moves(ocr):
    page = "1.e4 e5 2.Nf3 Комбина-\nция! 2...Nc6 (Одна-\nко 2...d6 По-\nпрежнему активно.) 3.Bb5 a6 1-0"
    tokens, lines = c.tokenize([page], {1} if ocr else set())
    finder = c.GameFinder("Fixture", ["en"], True, lines, {1} if ocr else set(), "en")
    finder.run(tokens)
    game = finder.games[0]
    assert [n.san() for n in game.mainline()] == ["e4", "e5", "Nf3", "Nc6", "Bb5", "a6"]
    nf3 = list(game.mainline())[2]
    assert nf3.comment == "Комбинация!"
    assert not nf3.nags
    branch = nf3.variations[1]
    assert branch.san() == "d6" and branch.starting_comment == "Однако"
    assert branch.comment == "По-прежнему активно."
    assert not branch.nags


def test_conversion_report_keeps_original_lines_and_dictionary_identity(tmp_path, monkeypatch):
    import fitz
    source = tmp_path / "book.pdf"
    with fitz.open() as doc:
        doc.new_page()
        doc.save(source)
    page = "1.e4 e5 2.Nf3 Комбина-\nция! 2...Nc6 3.Bb5 a6 1-0"
    monkeypatch.setattr(c, "read_book_pages", lambda *args: [page])
    target = tmp_path / "out.pgn"
    c.convert(source, target, {"ocr": "off", "lang": "en"})
    report = json.loads(report_path_for(target).read_text(encoding="utf8"))
    audit = report["text_normalization"]
    assert audit["applied"] == 1 and audit["unresolved"] == 0
    assert audit["dictionary"]["available"]
    assert audit["dictionary"]["predictions"] is False
    assert audit["words"][0]["source_lines"] == page.split("\n")
    assert next(m for m in report["games"][0]["moves"] if m["san"] == "Nc6")["source_line"].startswith("ция!")
    assert "Комбинация!" in target.read_text(encoding="utf-8-sig")


def test_batch_resume_fingerprint_tracks_dictionary(monkeypatch):
    monkeypatch.setattr(text, "dictionary_fingerprint", lambda: "dictionary-one")
    before = chess_batch.pipeline_fingerprint()
    monkeypatch.setattr(text, "dictionary_fingerprint", lambda: "dictionary-two")
    assert before != chess_batch.pipeline_fingerprint()
