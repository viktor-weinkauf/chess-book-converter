import json
from pathlib import Path

import chess
import pytest

import chess_converter as converter
import figurine_ocr as f


@pytest.mark.parametrize("lang", ["en", "ru", "ru_ocr", "de"])
def test_unicode_king_is_never_reinterpreted_as_a_knight(lang):
    board = chess.Board("8/8/8/8/8/6k1/8/6K1 w - - 0 1")
    assert converter.parse_move(board, "♔h1", [lang])[0] == chess.Move.from_uci("g1h1")
    assert converter.to_english("♘f3", lang) == "Nf3"


def test_fuzzy_repair_does_not_change_a_recognized_piece():
    board = chess.Board()
    assert converter.parse_move(board, "♗f3", ["ru_ocr"], fuzzy=True)[0] is None
    assert converter.candidate_moves(board, "♗f3", ["ru_ocr"]) == []


@pytest.mark.parametrize("raw,symbol,expected", [
    ("7.We2!", "♕", "7.♕e2!"), ("8.8\\f3", "♘", "8.♘f3"),
    ("14.lad1", "♖", "14.♖ad1"), ("&:d5", "♗", "♗:d5"),
    ("e4", "♕", "♕e4"), ("20...0)f6", "♘", "20...♘f6"),
])
def test_prefix_repair_keeps_square_number_capture_and_annotation(raw, symbol, expected):
    assert f.replace_prefix(raw, symbol) == expected


@pytest.mark.parametrize("word", ["introduction", "предисловие", "5.Nf36.Nc6"])
def test_prose_and_fused_moves_are_not_rewritten(word):
    assert f.replace_prefix(word, "♘") is None


def test_geometry_does_not_rewrite_a_nearby_word():
    words = [(0, 0, 50, 10, "comment"), (70, 0, 95, 10, "We2!")]
    glyphs = [{"bbox": [70, 0, 80, 10], "symbol": "♕"}]
    fixed = f.restore_words(words, glyphs)
    assert [w[4] for w in fixed] == ["comment", "♕e2!"]
    assert glyphs[0]["original_word"] == "We2!"


def test_two_figures_in_one_ocr_word_are_left_unresolved():
    words = [(0, 0, 50, 10, "Nf3Nc6")]
    glyphs = [{"bbox": [x, 0, x+10, 10], "symbol": "♘"} for x in (0, 25)]
    assert f.restore_words(words, glyphs)[0][4] == "Nf3Nc6"


def test_alternate_replaces_only_malformed_square(monkeypatch):
    original = [(0, 0, 30, 10, "Wf3"), (50, 0, 80, 10, "893")]
    glyphs = [{"bbox": [x, 0, x+10, 10], "symbol": piece} for x, piece in ((0, "♕"), (50, "♘"))]
    monkeypatch.setattr(f, "read_with_letters", lambda *args: [(0,0,30,10,"Qe3"), (50,0,80,10,"Nf3")])
    fixed = f.repair_words(None, original, glyphs, "eng", "unused", 300)
    assert [w[4] for w in fixed] == ["♕f3", "♘f3"]
    assert glyphs[0]["conflicting_readings"]
    assert glyphs[1]["used_alternate"]


def test_shape_tie_is_rejected():
    samples = bytes([0] * 16)
    mask = f.bitmap(samples, 4, (0,0,4,4))
    assert f.classify(samples, 4, (0,0,4,4), [("N",1,mask), ("B",1,mask)]) is None


def test_cached_visual_evidence_survives_resume_and_corruption(tmp_path, monkeypatch):
    monkeypatch.setattr(converter, "find_tessdata", lambda lang: "unused")
    monkeypatch.setattr(converter, "ocr_cache", lambda *args: (tmp_path, "test"))
    def no_pool(**kwargs):
        raise PermissionError()
    monkeypatch.setattr(converter, "ProcessPoolExecutor", no_pool)
    calls = []
    def read(job):
        calls.append(job[2])
        return job[2], "1. e4 e5", [{"symbol":"♘", "bbox":[1,2,3,4], "original_word":"8f3", "replacement":"♘f3"}]
    monkeypatch.setattr(converter, "ocr_page", read)
    audit = []
    args = ("pdf", tmp_path/"source.pdf", [0], "eng", str(tmp_path), lambda *a:None)
    converter.ocr_book(*args, evidence=audit)
    resumed = []
    converter.ocr_book(*args, evidence=resumed)
    assert calls == [0] and resumed == audit
    assert resumed[0]["page"] == 1 and resumed[0]["dpi"] == 300
    (tmp_path/"test-p1.json").write_text("truncated {")
    converter.ocr_book(*args)
    assert calls == [0,0]


def test_cache_changes_when_model_or_figurine_mode_changes(tmp_path, monkeypatch):
    source = tmp_path/"book.pdf"
    source.write_bytes(b"scan")
    model = tmp_path/"eng.traineddata"
    model.write_bytes(b"model one")
    monkeypatch.setattr(converter, "find_tessdata", lambda lang: str(tmp_path))
    monkeypatch.setenv("CHESS_CONVERTER_CACHE", str(tmp_path/"cache"))
    first = converter.ocr_cache(source, "eng")
    assert converter.ocr_cache(source, "eng", figurines="off") != first
    model.write_bytes(b"different model two")
    assert converter.ocr_cache(source, "eng") != first


def test_holdout_shapes_and_prose_negatives():
    samples = json.loads((Path(__file__).parent/"fixtures/figurine_shapes.json").read_text())
    _, templates = f.load_profile()
    for sample in samples:
        width, height = sample["width"], sample["height"]
        pixels = bytes(0 if value == "1" else 255 for row in sample["rows"] for value in row)
        match = f.classify(pixels, width, (0,0,width,height), templates)
        assert (match[0] if match else None) == sample["expected"], sample["id"]
