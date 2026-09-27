import io
from pathlib import Path

import chess
import chess.pgn
import pytest

import chess_converter as c
from benchmark import compare, load_games
from conversion_quality import serialize_games, tree_signature

FIXTURES = Path(__file__).parent / "fixtures"
LOCAL_ANNOTATIONS = pytest.mark.skipif(
    not all((FIXTURES / name).is_file() for name in (
        "page18-checked-notation.txt", "euwe-speijer-1924-checked-tree.pgn")),
    reason="Optional private book annotations are available only in the local evaluation bundle.",
)


def annotated_sample(keep_text=True):
    text = (FIXTURES/"page18-checked-notation.txt").read_text(encoding="utf-8-sig")
    tokens,lines = c.tokenize([text],boundaries=True)
    # Bold mainline words transcribed from the scan, separately from PGN gold.
    anchors = {5:["18.Rxe7!"],10:["18...Bxf3","19.Ba3!!"],11:["19...Qa6."],
               12:["20.Rcc7!","21.Rxf7+","22.Qe1+"]}
    records = [dict(kind="coordinate",replacement=w,page=1,line=l,dpi=300,reading="whole_word")
               for l,words in anchors.items() for w in words]
    visual = c.visual_token_evidence(tokens,[text],1,records)
    finder = c.GameFinder("Book",["en"],keep_text,lines,visual_tokens=visual)
    finder.run(tokens)
    return finder


@LOCAL_ANNOTATIONS
def test_book_page_all_101_paths_comments_and_nags():
    finder = annotated_sample()
    gold = load_games(FIXTURES/"euwe-speijer-1924-checked-tree.pgn")
    result = compare(finder.games,gold)
    assert not finder.problems
    assert result["exact_games"] == 1
    assert result["games"][0]["matched_move_paths"] == 101
    assert "математика" not in serialize_games(finder.games)
    restored = chess.pgn.read_game(io.StringIO(serialize_games(finder.games)))
    assert tree_signature(restored) == tree_signature(gold[0])


@LOCAL_ANNOTATIONS
def test_no_comments_keeps_all_branches_and_annotations():
    finder = annotated_sample(False)
    gold = load_games(FIXTURES/"euwe-speijer-1924-checked-tree.pgn")
    result = compare(finder.games,gold)
    assert result["move_path_precision"] == result["move_path_recall"] == 1
    assert "Любопытно" not in serialize_games(finder.games)


def extract(text,ocr=False):
    tokens,lines = c.tokenize([text],{1} if ocr else set(),boundaries=True)
    finder = c.GameFinder("Book",["en"],True,lines,{1} if ocr else set(),"en")
    finder.run(tokens)
    assert not finder.problems
    return finder


def test_parenthesized_continuation_after_last_move():
    f = extract("1. e4 (instead, 1... c5 2. Nf3) e5 2. Nf3 Nc6 3. Bb5 a6 1-0")
    e4 = f.games[0].variations[0]
    assert [n.san() for n in e4.variations] == ["e5","c5"]
    assert e4.variations[1].starting_comment == "instead,"
    assert e4.variations[1].variations[0].san() == "Nf3"


def test_punctuation_and_parenthetical_prose_stay_on_the_move():
    f = extract("1. e4 Clear, central control; (a useful plan) 1...e5 2. Nf3 Nc6 3. Bb5 a6 1-0")
    assert f.games[0].variations[0].comment == "Clear, central control; (a useful plan)"


def test_unread_variation_cannot_skip_a_missing_move():
    f = extract("1. e4 e5 2. Nf3 (2. Nc3 Qz9 3. Bc4 Nf6) Nc6 3. Bb5 a6 1-0",True)
    branch = f.games[0].variations[0].variations[0].variations[1]
    assert branch.san() == "Nc3"
    assert not branch.variations
    assert "Qz9" in branch.comment and "Bc4" in branch.comment


def test_unread_sibling_start_cannot_borrow_a_legal_previous_branch():
    f=extract('1.e4 e5 2.Nf3 (2.Bc4 Nf6 3.d3; 2...82?! 3.d3 Nc6 4.Nf3) Nc6 3.Bb5 a6 1-0',True)
    branch=f.games[0].variations[0].variations[0].variations[1]
    end=branch.variations[0].variations[0]
    assert end.san() == 'd3'
    assert not end.variations
    assert '82?!' in end.comment and 'Nc6' in end.comment
    assert any(d['code']=='variation_start_unread' for d in f.diagnostics)
    assert len(list(f.games[0].mainline_moves())) == 6


def test_explicit_new_analysis_after_gap_can_restart_from_known_ancestor():
    f=extract('1.e4 e5 2.Nf3 (2.Bc4 Nf6 3.d3 Qz9 4.Nf3 Однако после 2...Bc5 3.d3 Nf6) Nc6 3.Bb5 a6 1-0',True)
    branch=f.games[0].variations[0].variations[0].variations[1]
    assert [n.san() for n in branch.variations] == ['Nf6','Bc5']
    assert branch.variations[1].variations[0].variations[0].san() == 'Nf6'
    assert not branch.variations[0].variations[0].variations


def test_superfluous_capture_is_not_an_exact_move():
    board = chess.Board()
    assert c.parse_move(board,"Nxf3",["en"])[0] is None
    assert c.parse_move(board,"Nf3",["en"])[0] == chess.Move.from_uci("g1f3")


@pytest.mark.parametrize("enumeration",["1)","2)","12)"])
def test_list_numbers_are_not_closing_brackets(enumeration):
    tokens,_ = c.tokenize([enumeration+" 19.Qxf3"])
    assert tokens[0][:2] == ("list_item",enumeration[:-1])
    assert not any(t[0]=="close" for t in tokens)


def test_move_references_in_comments_are_not_silently_rewritten():
    f = extract("1. e4 e5 2. Nf3 Comment about 9. Qz9, uncertain; Nc6 3. Bb5 a6 1-0",True)
    assert "Qz9," in serialize_games(f.games)


def test_resignation_inside_variation_does_not_end_the_game():
    f=extract("1. e4 e5 2. Nf3 (2. Bc4 Nf6 Белые сдались.) Nc6 3. Bb5 a6 1-0")
    assert [n.san() for n in f.games[0].mainline()]==["e4","e5","Nf3","Nc6","Bb5","a6"]
    assert "сдались" in serialize_games(f.games)


def test_column_heading_does_not_reorder_the_other_column():
    words = []
    for row in range(10):
        words.extend([(5,row*12,80,row*12+9,"№ 151" if row==5 else "left"),
                      (170,row*12,280,row*12+9,"right")])
    regions = c.word_regions(words,150,300)
    assert len(regions) == 2
    assert regions[0].y0 == regions[1].y0
    assert regions[0].y1 == regions[1].y1
