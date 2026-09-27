import io

import chess.pgn

from benchmark import compare, tree_differences


def game(moves):
    return chess.pgn.read_game(io.StringIO(moves))


def test_legal_but_wrong_opening_is_not_accurate():
    result = compare([game("1. d4 d5 2. Nf3 Nf6 *")], [game("1. e4 e5 2. Nf3 Nf6 *")])
    assert result["move_path_recall"] == 0
    assert result["exact_games"] == 0


def test_missing_variation_is_measured():
    result = compare([game("1. e4 e5 *")], [game("1. e4 (1. d4 d5) e5 *")])
    assert result["move_path_precision"] == 1
    assert result["move_path_recall"] == 0.5


def test_extra_comment_fails_exact_match():
    result = compare([game("1. e4 {Unrelated biography} e5 *")], [game("1. e4 e5 *")])
    assert result["move_path_recall"] == 1
    assert result["exact_games"] == 0


def test_missing_numbered_game_does_not_shift_later_pairing():
    first, second = game("1. e4 e5 *"), game("1. d4 d5 *")
    first.headers["BookGame"] = "1"
    second.headers["BookGame"] = "2"
    result = compare([second], [first, second])
    assert result["pairing"] == "BookGame"
    assert result["exact_games"] == 1
    assert result["move_path_recall"] == 0.5


def test_missing_branch_frontier_counts_its_whole_subtree_once():
    diff = tree_differences(game('1. e4 e5 *'),game('1. e4 (1. d4 d5 (1...Nf6 2.c4) 2.c4) e5 *'))
    assert diff['missing_moves'] == 5
    assert len(diff['missing_branch_roots']) == 1
    assert diff['missing_branch_roots'][0]['first_move'] == 'd4'
    assert diff['missing_branch_roots'][0]['affected_moves'] == 5


def test_wrong_legal_line_and_moved_comment_are_visible_separately():
    diff = tree_differences(game('1. e4 {Note} c5 *'),game('1. e4 e5 {Note} *'))
    assert diff['missing_moves'] == diff['unexpected_moves'] == 1
    assert diff['comment_differences_on_matched_nodes'][0]['actual'] == 'Note'
    assert diff['missing_branch_roots'][0]['first_move'] == 'e5'


def test_same_moves_from_other_fen_are_not_shared_annotation_nodes():
    actual,expected=game('1. e4 {Comment} *'),game('1. e4 *')
    board=actual.board();board.remove_piece_at(chess.H7);actual.setup(board)
    diff=tree_differences(actual,expected)
    assert not diff['initial_position_matches']
    assert diff['missing_moves'] == diff['unexpected_moves'] == 1
    assert not diff['comment_differences_on_matched_nodes']


def test_variation_order_is_checked_even_when_all_moves_exist():
    diff=tree_differences(game('1. e4 (1.d4) *'),game('1. d4 (1.e4) *'))
    assert diff['missing_moves'] == diff['unexpected_moves'] == 0
    assert len(diff['branch_order_differences']) == 1


def test_missing_game_reports_all_moves_without_crashing():
    diff=tree_differences(None,game('1.e4 e5 *'))
    assert diff['missing_moves'] == 2
    assert diff['missing_branch_roots'][0]['affected_moves'] == 2
