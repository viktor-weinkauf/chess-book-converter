"""Recover lost row dots without treating numbered commentary as move text."""
import io

import chess.pgn
import pytest

import chess_converter as c


ROWS = """1. e2-e4 e7-e5
2. Ng1-f3 Nb8-c6
3. Bf1-b5 a7-a6
4. Bb5-a4 Ng8-f6
5. O-O Bf8-e7"""


@pytest.mark.parametrize("line", [
    "о немедленном 14...",  # Portisch: Cyrillic 'о' resembles a zero.
    "6 7. d2-d3",
    "6 d2-d3 7.",  # A move first used to hide the later number via any().
    "6 7...",
    "6 (d2-d3)",
    "6 d2-d3)",
])
def test_bare_number_before_structural_tokens_is_not_a_table_row(line):
    # The failure needs other pages to establish a table layout first.
    tokens, _ = c.tokenize([ROWS, line], {1, 2}, boundaries=True)
    original = [t for t in tokens if t[2] == 2]
    rows = c.table_layout(tokens)
    assert rows
    assert [t for t in tokens if t[2] == 2] == original
    assert not any(i in rows for i, t in enumerate(tokens) if t[2] == 2)


@pytest.mark.parametrize("line, expected", [
    ("6 d2-d3", 6),
    ("6 d2-d3 b7-b5", 6),
    ("6 — d2-d3 b7-b5", 6),
    ("6 _ d2-d3", 6),
    ("I e2-e4 e7-e5", 1),
])
def test_lost_dot_still_recovers_ordinary_move_rows(line, expected):
    tokens, _ = c.tokenize([ROWS, line], {1, 2}, boundaries=True)
    rows = c.table_layout(tokens)
    start = next(i for i, t in enumerate(tokens) if t[2] == 2)
    assert start in rows
    assert tokens[start][:2] == ("num", (expected, False))


@pytest.mark.parametrize("broken_reading", ["primary", "alternate"])
def test_numbered_comment_does_not_abort_book_conversion(tmp_path, monkeypatch, broken_reading):
    source, destination = tmp_path / "book.pdf", tmp_path / "book.pgn"
    source.write_bytes(b"unused: page reading is replaced by OCR fixtures")
    monkeypatch.setattr(c, "read_book_pages", lambda *args: ["", ""])
    calls = []

    def ocr(*args, dpi=c.OCR_DPI, **kwargs):
        calls.append(dpi)
        reading = "primary" if dpi == c.OCR_DPI else "alternate"
        comment = "\nо немедленном 14...\n" if reading == broken_reading else "\n"
        return {0: "№ 1.\nAlice - Bob\n" + ROWS,
                1: comment + "6. Rf1-e1 b7-b5\n1-0"}

    monkeypatch.setattr(c, "ocr_book", ocr)
    c.book_to_pgn(source, destination, {"lang": "en", "ocr": "always"}, lambda *args: None)
    assert calls == [c.OCR_DPI, c.ALT_OCR_DPI]
    game = chess.pgn.read_game(io.StringIO(destination.read_text(encoding="utf-8-sig")))
    assert not game.errors
    assert len(list(game.mainline_moves())) == 12
    assert game.headers["Result"] == "1-0"
