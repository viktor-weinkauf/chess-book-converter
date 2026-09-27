import chess
import json
from pathlib import Path
import pytest

import chess_converter as c
import coordinate_ocr as co


@pytest.mark.parametrize('piece,symbol', [('q','♕'),('r','♖'),('b','♗'),('n','♘')])
@pytest.mark.parametrize('lang', ['en','ru','ru_ocr','de'])
def test_short_capture_promotes_to_exact_printed_piece(piece, symbol, lang):
    board = chess.Board('2r4k/1P6/8/8/8/8/8/K7 w - - 0 33')
    for notation in ('bc'+symbol, 'b:c'+symbol, 'bxc8='+symbol):
        move, _ = c.parse_move(board, notation, [lang], fuzzy=True)
        assert move == chess.Move.from_uci('b7c8'+piece)


@pytest.mark.parametrize('word,lang,piece', [('bcФ','ru','q'),('bcЛ','ru','r'),('bcС','ru','b'),
                                         ('bcК','ru','n'),('bcD','de','q'),('bcS','de','n'),('bcQ','en','q')])
def test_letter_promotions_follow_book_language(word, lang, piece):
    board = chess.Board('2r4k/1P6/8/8/8/8/8/K7 w - - 0 33')
    assert c.parse_move(board,word,[lang])[0] == chess.Move.from_uci('b7c8'+piece)


def test_black_promotion_and_missing_or_illegal_piece():
    board = chess.Board('k7/8/8/8/8/8/1p6/2R4K b - - 0 40')
    assert c.parse_move(board,'bc♘',['ru'])[0] == chess.Move.from_uci('b2c1n')
    assert c.parse_move(board,'bc',['ru'])[0] is None
    assert c.parse_move(board,'bc♔',['ru'])[0] is None
    assert c.parse_move(board,'bc♔',['ru'],fuzzy=True)[0] is None
    for incomplete in ('bc','bxc1','c1','b2c1','b2-c1'):
        assert c.parse_move(board,incomplete,['ru'],fuzzy=True)[0] is None
        assert c.candidate_moves(board,incomplete,['ru']) == []
    # There is no pawn on d2. The printed files cannot change to make it legal.
    assert c.parse_move(board,'dc♕',['ru'],fuzzy=True)[0] is None
    assert c.candidate_moves(board,'dc♕',['ru']) == []
    assert c.closest_move(board,'dc♕',['ru']) == (None,None)
    assert c.parse_move(chess.Board(),'bc♕',['ru'],fuzzy=True)[0] is None


@pytest.mark.parametrize('word', ['b:c3♕', 'b2:c3♕', 'b2:d1♕', 'b2c1♔'])
def test_bad_promotion_rank_files_or_piece_are_not_repaired(word):
    board = chess.Board('k7/8/8/8/8/8/1p6/2R4K b - - 0 40')
    assert c.parse_move(board,word,['ru'],fuzzy=True)[0] is None
    assert c.candidate_moves(board,word,['ru']) == []


@pytest.mark.parametrize('word', ['33.bc♕', 'b:c♘+', 'b8♖!', 'c1=♗'])
def test_visual_promotion_syntax(word):
    assert co.valid_notation(word)


@pytest.mark.parametrize('word', ['bc♔', 'bd♕', 'b3♕', 'bb♘', 'b♕'])
def test_visual_promotion_does_not_invent_missing_square_or_piece(word):
    assert not co.valid_notation(word)


def test_printed_promotion_pixels_regression():
    sample = json.loads((Path(__file__).parent/'fixtures/promotion_word.json').read_text(encoding='utf8'))
    pixels = bytes(0 if p == '1' else 255 for row in sample['rows'] for p in row)
    # The decoder must identify the printed queen itself, without a supplied
    # piece anchor or a chess position.
    decoded = co.decode(pixels,sample['width'],(0,0,sample['width'],sample['height']),
                        [],300,co.load_profile(True))
    assert decoded and decoded[0] == '33.bc♕'


def test_matching_moves_from_the_wrong_initial_position_are_not_a_correct_prefix():
    from corpus_runner import check_mainlines
    expected = chess.pgn.Game()
    expected.add_main_variation(chess.Move.from_uci('e2e4'))
    actual = chess.pgn.Game()
    board = chess.Board();board.remove_piece_at(chess.H7)
    actual.setup(board)
    actual.add_main_variation(chess.Move.from_uci('e2e4'))
    result = check_mainlines([actual],[expected])[0]
    assert result['matching_prefix_plies'] == 0
    assert not result['initial_position_matches']
    assert not result['exact_mainline_and_result']
