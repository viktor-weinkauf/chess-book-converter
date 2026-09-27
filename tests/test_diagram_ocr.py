import copy
from pathlib import Path

import chess
import fitz
import pytest

import chess_converter as c
import diagram_ocr as d

FIXTURES = Path(__file__).parent/"fixtures"
FEN = "3r1k1r/pb2bppp/1p3n2/8/8/1P1q1N2/PB1N1PPP/2RQR1K1 w - - 0 18"


def read_board(name):
    pix = fitz.Pixmap(str(FIXTURES/name))
    with fitz.open() as doc:
        page = doc.new_page(width=pix.width*72/300,height=pix.height*72/300)
        page.insert_image(page.rect,pixmap=pix)
        return d.detect(page)


def test_development_board_all_squares_and_original_coordinates():
    boards = read_board("board-development.png")
    assert len(boards)==1
    assert boards[0]["placement"] == FEN.split()[0]
    assert len(boards[0]["cells"])==64
    assert all(0<=v<170 for cell in boards[0]["cells"] for v in cell["bbox"])


def test_holdout_abstains_instead_of_misclassifying_uncertain_squares():
    boards = read_board("board-holdout.png")
    assert len(boards)==1 and boards[0]["placement"] is None
    gold = chess.Board("3RR3/k1q3p1/ppn5/8/N7/1P4p1/PBP4P/6K1 w - - 0 26")
    unresolved = []
    for cell in boards[0]["cells"]:
        piece = gold.piece_at(chess.parse_square(cell["square"]))
        if cell["piece"] is None:
            unresolved.append(cell["square"])
        else:
            assert cell["piece"] == (piece.symbol() if piece else ".")
    assert set(unresolved)=={"b2","g1","d8"}


@pytest.mark.parametrize("reverse",[False,True])
def test_orientation_side_and_number_from_printed_first_move(reverse):
    board = chess.Board(FEN)
    if reverse:
        rotated = chess.Board(None)
        for square,piece in board.piece_map().items():
            rotated.set_piece_at(63-square,piece)
        board = rotated
    assert d.starting_positions(board.board_fen(),18,False,"Rxe7",c.parse_move,["en"]) == [
        (FEN,"black_bottom" if reverse else "white_bottom")]


def test_initial_castling_history_is_not_guessed():
    assert d.starting_positions(chess.STARTING_BOARD_FEN,1,False,"e4",c.parse_move,["en"]) == []


def test_square_frame_without_checker_pattern_is_not_masked():
    with fitz.open() as doc:
        page=doc.new_page(width=200,height=200)
        page.draw_rect((35,35,165,165),color=(0,0,0),width=1)
        page.insert_text((60,80),"A framed quotation")
        assert d.detect(page)==[]


def test_illegal_first_move_does_not_repair_the_diagram():
    assert d.starting_positions(FEN.split()[0],18,False,"e4",c.parse_move,["en"]) == []


def test_marker_survives_ocr_junk_filter_and_starts_numbered_game():
    record = {"page":1,"marker":"__BOARD_0__","placement":FEN.split()[0]}
    text = "M 151\nЭЙВЕ — СПЕЙЕР\nАмстердам 1924\n__BOARD_0__\n18.Rxe7 Bxf3 19.Ba3 Qa6 20.Rcc7 Qxa3 21.Rxf7+ Ke8 22.Qe1+ 1-0"
    tokens,lines = c.tokenize([text],{1},boundaries=True)
    f = c.GameFinder("Book",["en"],True,lines,{1},"en",diagram_evidence=[record])
    f.run(tokens)
    assert not f.problems
    assert f.games[0].board().fen()==FEN
    assert f.games[0].headers["BookGame"]=="151"
    assert f.games[0].headers["Site"]=="Амстердам"
    assert f.games[0].headers["Date"]=="1924.??.??"
    assert len(list(f.games[0].mainline_moves()))==9
    assert record["status"]=="initial_position"


def test_diagram_marker_survives_when_notation_stages_are_off():
    class Page:
        rect=fitz.Rect(0,0,200,200)
        def get_textpage_ocr(self,**kwargs):
            return None
        def get_text(self,kind=None,**kwargs):
            return [] if kind=="words" else ""
    marker=(20,40,30,48,"__BOARD_0__",0,0,0)
    assert c.ocr_layout(Page(),"eng","unused",300,[],"off",markers=[marker])=="__BOARD_0__"


def test_unresolved_diagram_does_not_restart_from_standard_position():
    text = "№ 1\nAlice - Bob\n__BOARD_0__\n18. Qe4 Some prose 1. e4 e5 2. Nf3 Nc6 1-0"
    tokens,lines=c.tokenize([text],boundaries=True)
    f=c.GameFinder("Book",["en"],True,lines)
    assert f.run(tokens)==[]
    assert any(i["code"]=="diagram_setup_unresolved" for i in f.diagnostics)


def test_cache_checks_square_labels_and_placement():
    record=read_board("board-development.png")[0]
    record["marker"]="__BOARD_0__"
    assert d.valid_evidence(record)
    bad=copy.deepcopy(record);bad["cells"][0]=None
    assert not d.valid_evidence(bad)
    bad=copy.deepcopy(record);bad["placement"]=chess.STARTING_BOARD_FEN
    assert not d.valid_evidence(bad)
    bad=copy.deepcopy(record);bad["cells"][0]["square"]="b8"
    assert not d.valid_evidence(bad)
