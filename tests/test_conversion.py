import io
import json
from pathlib import Path

import chess
import chess.pgn
import fitz
import pytest

import chess_batch as batch
import chess_converter as converter
from conversion_quality import atomic_text, build_report, report_path_for, serialize_games, tree_signature


def find(text, ocr=False, lang="en"):
    tokens, lines = converter.tokenize([text], {1} if ocr else set())
    finder = converter.GameFinder("Fixture", [lang], True, lines, {1} if ocr else set(), lang)
    finder.run(tokens)
    return finder, tokens


def pdf(path, text):
    with fitz.open() as doc:
        page = doc.new_page()
        page.insert_text((50, 70), text)
        doc.save(path)
    return path


def test_short_finished_game():
    finder, _ = find("1. f3 e5 2. g4 Qh4# 0-1")
    assert len(finder.games) == 1
    assert len(list(finder.games[0].mainline_moves())) == 4
    assert finder.games[0].headers["Result"] == "0-1"


@pytest.mark.parametrize("notation,lang", [
    ("1. e4 e5 2. Nf3 Nc6 3. Bb5 a6 1-0", "en"),
    ("1. e4 e5 2. Кf3 Кc6 3. Сb5 a6 1-0", "ru"),
    ("1. e4 e5 2. Sf3 Sc6 3. Lb5 a6 1-0", "de"),
    ("1. e4 e5 2. ♘f3 ♞c6 3. ♗b5 a6 1-0", "en"),
    ("1. e2—e4 e7—e5 2. Кg1—f3 Кb8—c6 3. Сf1—b5 a7—a6 1-0", "ru"),
])
def test_notation(notation, lang):
    finder, _ = find(notation, lang=lang)
    assert [m.uci() for m in finder.games[0].mainline_moves()] == [
        "e2e4", "e7e5", "g1f3", "b8c6", "f1b5", "a7a6"]


def test_variations_and_comments_survive_export():
    finder, _ = find("1. e4 (1. d4 d5 2. c4) e5 2. Nf3 good development Nc6 3. Bb5 a6 1-0")
    game = finder.games[0]
    assert len(game.variations) == 2
    assert game.variations[1].move.uci() == "d2d4"
    assert "good development" in game.variations[0].variations[0].variations[0].comment
    exported = serialize_games(finder.games)
    assert tree_signature(chess.pgn.read_game(io.StringIO(exported))) == tree_signature(game)


def test_fen_numbering_in_export():
    finder, _ = find("8/8/8/8/8/6k1/4p3/6K1 b - - 0 1\n42... e1=Q+ 43. Kh1 0-1")
    assert finder.games[0].board().fullmove_number == 42
    assert "42..." in serialize_games(finder.games)


def test_invalid_fen_is_diagnosed():
    finder, _ = find("8/8/8/8/8/8/8/8 w - - 0 1\n1. e4 e5")
    assert any(d["code"] == "invalid_fen" for d in finder.diagnostics)


@pytest.mark.parametrize("options", [{"first_page": 0}, {"last_page": -1}, {"first_page": 4, "last_page": 2}])
def test_bad_page_ranges(options):
    with pytest.raises(converter.UserError):
        converter.page_range(options, 5)


def test_report_traces_fuzzy_moves(tmp_path):
    text = "1. e4 e5 2. NfЗ Nc6 3. Bb5 a6 1-0"
    finder, tokens = find(text, ocr=True)
    source = tmp_path / "source.txt"
    source.write_text(text, encoding="utf-8")
    report = build_report(finder, tokens, [text], source, tmp_path / "out.pgn", 0, 1, {},
                          converter.parse_move, lambda w: bool(converter.move_code(w)))
    assert report["status"] == "needs_review"
    assert any(i["code"] == "corrected_move" and i["page"] == 1 for i in report["issues"])
    assert all("source_line" in move for game in report["games"] for move in game["moves"])


def test_atomic_write_keeps_original_on_failure(tmp_path, monkeypatch):
    path = tmp_path / "old.pgn"
    path.write_text("existing", encoding="utf-8")
    def fail(*args):
        raise OSError("simulated interrupted replacement")
    monkeypatch.setattr("conversion_quality.os.replace", fail)
    with pytest.raises(OSError):
        atomic_text(path, "new")
    assert path.read_text() == "existing"
    assert sorted(p.name for p in tmp_path.iterdir()) == ["old.pgn"]


def test_end_to_end_pdf_and_report(tmp_path):
    source = pdf(tmp_path / "book.pdf", "Alice - Bob\n1. e4 e5 2. Nf3 Nc6 3. Bb5 a6 1-0")
    target = tmp_path / "games.pgn"
    converter.convert(source, target, {"ocr": "off"})
    report = json.loads(report_path_for(target).read_text(encoding="utf-8"))
    assert report["output"]["written"] is True
    assert report["summary"]["mainline_plies"] == 6
    assert report["status"] == "completed"
    assert chess.pgn.read_game(io.StringIO(target.read_text())).headers["White"] == "Alice"


def test_strict_does_not_replace_existing_pgn(tmp_path):
    source = pdf(tmp_path / "book.pdf", "1. e4 e5 2. Nf3 Nc6 3. Bb5 a6")
    target = tmp_path / "games.pgn"
    target.write_text("existing", encoding="utf-8")
    with pytest.raises(converter.ReviewRequired):
        converter.convert(source, target, {"ocr": "off", "strict": True})
    assert target.read_text() == "existing"
    assert json.loads(report_path_for(target).read_text())["output"]["written"] is False


def test_no_games_still_has_report(tmp_path):
    source = pdf(tmp_path / "blank.pdf", "Introduction without games.")
    target = tmp_path / "out.pgn"
    with pytest.raises(converter.UserError):
        converter.convert(source, target, {"ocr": "off"})
    assert not target.exists()
    assert json.loads(report_path_for(target).read_text())["summary"]["games"] == 0


def test_report_cannot_overwrite_source(tmp_path):
    source = pdf(tmp_path / "book.pdf", "1. e4 e5 2. Nf3 Nc6 3. Bb5 a6 1-0")
    before = source.read_bytes()
    with pytest.raises(converter.UserError):
        converter.convert(source, tmp_path / "out.pgn", {"report": str(source)})
    assert source.read_bytes() == before


def test_scan_with_page_number_uses_ocr(tmp_path):
    path = tmp_path / "scan.pdf"
    with fitz.open() as image_doc:
        page = image_doc.new_page()
        page.insert_text((50, 70), "1. e4 e5 2. Nf3 Nc6 3. Bb5 a6 1-0")
        pix = page.get_pixmap()
        with fitz.open() as scan:
            p = scan.new_page()
            p.insert_image(p.rect, pixmap=pix)
            p.insert_text((50, 20), "12")
            scan.save(path)
    assert converter.automatic_ocr_pages(path, ["12"], 0, 1) == [0]


def test_batch_resume_and_output_integrity(tmp_path, monkeypatch):
    monkeypatch.setattr(batch, "pipeline_fingerprint", lambda: "test-v1")
    source, output = tmp_path / "books", tmp_path / "out"
    source.mkdir()
    pdf(source / "one.pdf", "1. e4 e5 2. Nf3 Nc6 3. Bb5 a6 1-0")
    (source / "broken.djvu").write_bytes(b"not a djvu")
    first = batch.run_batch(source, output, {"ocr": "off"}, progress=lambda _: None)
    assert first["jobs"]["one.pdf"]["status"] == "completed"
    assert first["jobs"]["broken.djvu"]["status"] == "failed"
    second = batch.run_batch(source, output, {"ocr": "off"}, progress=lambda _: None)
    assert second["jobs"]["one.pdf"]["resumed"]
    (output / "one.pdf.pgn").write_text("tampered", encoding="utf-8")
    third = batch.run_batch(source, output, {"ocr": "off"}, progress=lambda _: None)
    assert not third["jobs"]["one.pdf"]["resumed"]
    monkeypatch.setattr(batch, "pipeline_fingerprint", lambda: "test-v2")
    fourth = batch.run_batch(source, output, {"ocr": "off"}, progress=lambda _: None)
    assert not fourth["jobs"]["one.pdf"]["resumed"]


def test_cancellation_not_swallowed(monkeypatch):
    tokens, lines = converter.tokenize(["1. e4 e5"])
    finder = converter.GameFinder("Fixture", ["en"], True, lines)
    def stop(*args):
        raise converter.Stopped()
    monkeypatch.setattr(finder, "step", stop)
    with pytest.raises(converter.Stopped):
        finder.run(tokens)


def test_serial_ocr_fallback_and_cache(tmp_path, monkeypatch):
    source = tmp_path / "book.djvu"
    source.write_bytes(b"fixture")
    monkeypatch.setattr(converter, "find_tessdata", lambda lang: str(tmp_path))
    monkeypatch.setattr(converter, "ocr_cache", lambda *args: (tmp_path, "test"))
    def unavailable(**kwargs):
        raise PermissionError("Named pipes are not available")
    calls = []
    def ocr(job):
        calls.append(job[2])
        return job[2], f"Page {job[2] + 1}", []
    monkeypatch.setattr(converter, "ProcessPoolExecutor", unavailable)
    monkeypatch.setattr(converter, "ocr_page", ocr)
    assert converter.ocr_book("djvu", source, [0, 1], "eng", str(tmp_path), lambda *a: None) == {
        0: "Page 1", 1: "Page 2"}
    converter.ocr_book("djvu", source, [0, 1], "eng", str(tmp_path), lambda *a: None)
    assert calls == [0, 1]
