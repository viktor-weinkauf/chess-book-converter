import copy
import json
from pathlib import Path

import chess.pgn
import fitz
import pytest

import chess_converter as c
import diagram_ocr as d
import diagram_refinement as r

FIXTURES = Path(__file__).parent / "fixtures"
PLACEMENT = "3k4/p3pp1p/1pp2bp1/8/2P5/6P1/PP2PPKP/3N4"


def record():
    return json.loads((FIXTURES/"board-supplement-record.json").read_text())


def test_additional_cells_come_from_pixels_and_keep_original_evidence():
    board = record()
    before = copy.deepcopy(board)
    r.refine(board, fitz.Pixmap(str(FIXTURES/"board-supplement-development.png")), 300)
    assert board["placement"] == PLACEMENT
    assert board["refinement"]["accepted"] == 3
    assert d.valid_evidence(board)
    for old, new in zip(before["cells"], board["cells"]):
        if old["piece"] is not None:
            assert old == new
        else:
            assert new["original_reading"]["piece"] is None
    assert {c["square"] for c in board["cells"] if "original_reading" in c} == {"g2","f6","d1"}


@pytest.mark.parametrize("index,accepted", [(1,63),(2,62),(3,63)])
def test_untrained_diagrams_have_no_wrong_accepted_cells(index, accepted):
    fixture = json.loads((FIXTURES/"diagram_supplement.json").read_text())[index]
    profile = r.load_profile(d.profile_fingerprint(), r.profile_fingerprint())
    assert fixture["split"] == "holdout"
    found = []
    for cell in fixture["cells"]:
        piece, _, _ = d.classify(cell["values"],cell["dark"],profile)
        assert piece in (None,cell["expected"]), cell["square"]
        found.append(piece)
    assert sum(p is not None for p in found) == accepted


def test_out_of_image_cells_remain_unknown():
    board = record()
    board["cells"][0]["bbox"] = [-100,-100,-90,-90]
    board["cells"][0]["piece"] = None
    r.refine(board, fitz.Pixmap(str(FIXTURES/"board-supplement-development.png")), 300)
    assert board["cells"][0]["piece"] is None
    assert board["placement"] is None


def test_supplement_does_not_introduce_wrong_cells_in_previous_holdout():
    pix = fitz.Pixmap(str(FIXTURES/"board-holdout.png"))
    with fitz.open() as doc:
        page = doc.new_page(width=pix.width*72/300,height=pix.height*72/300)
        page.insert_image(page.rect,pixmap=pix)
        board = d.detect(page)[0]
        raster = page.get_pixmap(dpi=300,colorspace=fitz.csGRAY,alpha=False)
    board["marker"] = "__BOARD_0__"
    r.refine(board,raster,300)
    gold = chess.Board("3RR3/k1q3p1/ppn5/8/N7/1P4p1/PBP4P/6K1 w - - 0 26")
    for cell in board["cells"]:
        piece = gold.piece_at(chess.parse_square(cell["square"]))
        assert cell["piece"] in (None, piece.symbol() if piece else ".")
    assert board["placement"] is None


def test_changed_supplement_is_loaded_without_reusing_previous_profile(tmp_path, monkeypatch):
    path = tmp_path/"extra.json"
    path.write_bytes(r.PROFILE.read_bytes())
    monkeypatch.setattr(r,"PROFILE",path)
    old = r.profile_fingerprint()
    profile = json.loads(path.read_text())
    profile["templates"] = []
    path.write_text(json.dumps(profile))
    new = r.profile_fingerprint()
    assert old != new
    assert len(r.load_profile(d.profile_fingerprint(),new)["templates"]) == len(d.load_profile()["templates"])


def test_cached_ocr_gets_refined_before_game_parsing(tmp_path, monkeypatch):
    source = tmp_path/"book.pdf"
    pix = fitz.Pixmap(str(FIXTURES/"board-supplement-development.png"))
    with fitz.open() as doc:
        page = doc.new_page(width=pix.width*72/300,height=pix.height*72/300)
        page.insert_image(page.rect,pixmap=pix)
        doc.save(source)
    cached = record()
    monkeypatch.setattr(c,"read_book_pages",lambda *args: [""])
    def cached_ocr(*args, evidence, **kwargs):
        evidence.append(copy.deepcopy(cached))
        return {0: "№ 154\nEuwe - Capablanca\n" + cached["marker"] +
                "\n18...Kd7 19.Kf3 Kd6 20.Ke3 Kc5 21.Kd3 Kb4 22.f4 1/2-1/2"}
    monkeypatch.setattr(c,"ocr_book",cached_ocr)
    destination = tmp_path/"out.pgn"
    c.book_to_pgn(source,destination,{"lang":"en","ocr":"always"},lambda *args: None)
    with destination.open(encoding="utf-8-sig") as stream:
        game = chess.pgn.read_game(stream)
    assert game.board().fen() == PLACEMENT + " b - - 0 18"
    assert game.headers["SourceStart"] == "diagram"
    assert len(list(game.mainline_moves())) == 8
    assert cached["placement"] is None  # refinement does not rewrite OCR evidence on disk
