"""Mainline extraction must not execute analysis or lose its attachment."""
import chess.pgn
import pytest

import chess_converter as c
from conversion_quality import serialize_games


def extract(text, *, table=False, bold=(), keep=True, ocr=False):
    tokens, lines = c.tokenize([text], {1} if ocr else set(), boundaries=True)
    visual = {i for i, t in enumerate(tokens) if t[0] == 'num' and t[3] in bold}
    finder = c.GameFinder('Book', ['en'], keep, lines, table=c.table_layout(tokens) if table else None,
                          visual_tokens=visual, mainline_only=True, ocr_pages={1} if ocr else set())
    finder.run(tokens)
    assert not finder.problems
    assert all(len(n.variations) <= 1 for g in finder.games for n in [g, *g.mainline()])
    serialize_games(finder.games)
    return finder


def test_nested_analysis_is_text_and_does_not_end_the_game():
    f = extract('1. e4 (1. d4 d5 (1... Nf6) 2. c4 0-1) e5 2. Nf3 Nc6 3. Bb5 a6 1-0')
    g = f.games[0]
    assert [n.san() for n in g.mainline()] == ['e4', 'e5', 'Nf3', 'Nc6', 'Bb5', 'a6']
    assert g.headers['Result'] == '1-0'
    comment = next(iter(g.mainline())).comment
    assert all(t in comment for t in ['d4', 'd5', 'Nf6', 'c4', '0-1'])
    assert not list(g.mainline())[1].comment


def test_bold_mainline_reserves_same_number_ahead_of_analysis():
    text = ('№ 1.\nAlice - Bob\n1. e4 e5\n'
            'Instead 2. Nc3 Nc6 is possible.\n2. Nf3 Nc6\n3. Bb5 a6\n1-0')
    f = extract(text, bold=(2, 4, 5))
    g = f.games[0]
    assert [n.san() for n in g.mainline()] == ['e4', 'e5', 'Nf3', 'Nc6', 'Bb5', 'a6']
    assert '2. Nc3 Nc6 is possible.' in list(g.mainline())[1].comment


def test_table_analysis_and_next_games_do_not_mix():
    text = ('№ 1.\nAlice - Bob\n1. e2-e4 e7-e5\n'
            'Consider (1... c5 2. Nf3).\n2. Ng1-f3 Nb8-c6\n'
            '3. Bf1-b5 a7-a6\n4. Bb5-a4 Ng8-f6\n5. O-O Bf8-e7\n1-0\n'
            'An unrelated biography.\n№ 2.\nCarol - Dave\n1. d2-d4 d7-d5\n0-1')
    f = extract(text, table=True)
    assert len(f.games) == 2
    assert len(list(f.games[0].mainline_moves())) == 10
    assert 'c5 2. Nf3' in list(f.games[0].mainline())[1].comment
    assert 'biography' not in str(f.games[0])
    assert 'Consider' not in str(f.games[1])


@pytest.mark.parametrize('table', [False, True])
def test_mainline_mode_does_not_hide_a_missing_turn(table):
    f = extract('1. e2-e4 e7-e5\n3. Ng1-f3 Nb8-c6\n4. Bf1-b5 a7-a6\n'
                '5. Bb5-a4 Ng8-f6\n6. O-O Bf8-e7\n1-0', table=table)
    g = f.games[0]
    assert len(list(g.mainline_moves())) == 2
    assert g.headers['MissingMove'] == '2.'
    assert g.headers['Result'] == '*'


def test_no_comments_still_excludes_analysis_moves():
    f = extract('1. e4 (1. d4 d5) e5 2. Nf3 Nc6 1-0', keep=False)
    g = f.games[0]
    assert len(list(g.mainline_moves())) == 4
    assert not any(n.comment for n in [g, *g.mainline()])


def test_analysis_of_earlier_move_stays_text():
    f = extract('1. e4 e5 2. Nf3 Nc6 Instead 2... d6 was possible. 3. Bb5 a6 1-0')
    assert len(list(f.games[0].mainline_moves())) == 6
    assert '2... d6 was possible.' in list(f.games[0].mainline())[3].comment


@pytest.mark.parametrize('cue', ['После', 'after'])
def test_continuation_of_an_alternative_cannot_supply_a_missing_mainline_turn(cue):
    text = ('№ 1.\nAlice - Bob\n1. e4 e5 2. Nf3\n'
            f'Instead 2. Nc3 is possible. {cue} 2... Nc6 (2... d6) the position is different.\n'
            '2... unread Лучше 2... d6 is stronger.\n3. Bc4 Nf6\n1-0')
    f = extract(text, bold=(2, 5))
    game = f.games[0]
    assert [n.san() for n in game.mainline()] == ['e4', 'e5', 'Nf3']
    assert game.headers['MissingMove'] == '2...' and game.headers['Result'] == '*'
    assert 'Nc6' in list(game.mainline())[-1].comment


@pytest.mark.parametrize('cue', ['Лучше', 'Сильнее', 'Энергичнее', 'Скажем,', 'Например,',
                               'Better', 'Stronger', 'Preferable'])
def test_suggested_legal_move_cannot_replace_an_unread_mainline_move(cue):
    text = ('№ 1.\nAlice - Bob\n1. e4 e5 2. Nf3\n'
            f'2... unread {cue} 2... Nc6.\n3. Bc4 Nf6\n1-0')
    f = extract(text, bold=(2, 4))
    assert [n.san() for n in f.games[0].mainline()] == ['e4', 'e5', 'Nf3']
    assert f.games[0].headers['MissingMove'] == '2...'


@pytest.mark.parametrize('cue', ['Скажем,', 'Например,'])
def test_example_keeps_its_continuation_as_comment_then_resumes_mainline(cue):
    text = ('№ 1.\nAlice - Bob\n1. e4 e5 2. Nf3\n'
            f'{cue} 2... Nc6 3. Bc4 и т.д.\n2... d6 3. Bb5+ Bd7\n1-0')
    f = extract(text, bold=(2,))
    nodes = list(f.games[0].mainline())
    assert [n.san() for n in nodes] == ['e4', 'e5', 'Nf3', 'd6', 'Bb5+', 'Bd7']
    assert 'Nc6' in nodes[2].comment and 'Bc4' in nodes[2].comment
    assert not nodes[3].comment


def test_a_confirmed_mainline_anchor_can_resume_after_the_word_after():
    text = ('№ 1.\nAlice - Bob\n1. e4 e5 2. Nf3\n'
            'Instead 2. Nc3 is possible.\nAfter 2... d6\n3. Bc4 Nf6\n1-0')
    f = extract(text, bold=(2, 4, 5))
    assert [n.san() for n in f.games[0].mainline()] == ['e4', 'e5', 'Nf3', 'd6', 'Bc4', 'Nf6']


def test_fragment_without_a_file_or_terminal_rank_cannot_be_filled_by_legal_lookahead(monkeypatch):
    def forbidden(*a, **k):
        raise AssertionError('Lookahead cannot manufacture a missing file')
    monkeypatch.setattr(c.GameFinder, 'fitting_moves', forbidden)
    text = ('№ 1.\nAlice - Bob\n1. e4 e5 2. Nf3\n'
            '2... 2\\п 5?! Better 2... d6.\n3. Bc4 Nf6\n1-0')
    f = extract(text, bold=(2, 4), ocr=True)
    assert [n.san() for n in f.games[0].mainline()] == ['e4', 'e5', 'Nf3']
    assert f.games[0].headers['MissingMove'] == '2...'
    assert any(d['code'] == 'ocr_incomplete_move_fragment' for d in f.diagnostics)


def test_inline_analysis_result_is_not_the_game_result():
    f = extract('1. e4 e5 2. Nf3 Nc6 Instead 2... d6 0-1 was another line. 3. Bb5 a6 1-0')
    assert len(list(f.games[0].mainline_moves())) == 6
    assert f.games[0].headers['Result'] == '1-0'
    assert '0-1' in list(f.games[0].mainline())[3].comment


def test_standalone_result_after_analysis_can_finish_game():
    f = extract('1. e4 e5 2. Nf3 Nc6 Instead 2... d6 was possible.\n1-0')
    assert f.games[0].headers['Result'] == '1-0'


def test_unclosed_analysis_cannot_swallow_a_bold_resume():
    f = extract('№ 1.\nAlice - Bob\n1. e4 e5\n(1... c5\n2. Nf3 Nc6\n3. Bb5 a6\n1-0', bold=(2, 4, 5))
    assert len(list(f.games[0].mainline_moves())) == 6
    assert any(d['code'] == 'mainline_text_resumed' for d in f.diagnostics)


def test_unclosed_analysis_cannot_hide_a_gap_at_bold_anchor():
    f = extract('№ 1.\nAlice - Bob\n1. e4 e5\n(1... c5\n3. Nf3 Nc6\n1-0', bold=(2, 4))
    assert f.games[0].headers['MissingMove'] == '2.'
    assert f.games[0].headers['Result'] == '*'


ROWS = ('1. e2-e4 e7-e5\n2. Ng1-f3 Nb8-c6\n3. Bf1-b5 a7-a6\n'
        '4. Bb5-a4 Ng8-f6\n5. O-O Bf8-e7\n6. Rf1-e1 b7-b5\n')


def merge(text, alternate):
    a, _ = c.tokenize([text], {1})
    b, _ = c.tokenize([alternate], {1})
    evidence = []
    merged, readings = c.merge_second_reading(a, b, evidence)
    return merged, readings, evidence


def test_row_number_uses_two_independent_neighbours_before_move_matching():
    tokens, _, evidence = merge(ROWS.replace('3.', '8.'), ROWS)
    assert [t[1][0] for t in tokens if t[0] == 'num'] == [1, 2, 3, 4, 5, 6]
    assert evidence[0]['code'] == 'row_number_ocr_consensus'
    assert evidence[0]['printed_number'] == 8
    assert evidence[0]['exported_number'] == 3


def test_missing_row_is_not_a_damaged_number():
    omitted = ROWS.replace('3. Bf1-b5 a7-a6\n', '')
    _, _, evidence = merge(omitted, omitted)
    assert not any(d['code'] == 'row_number_ocr_consensus' for d in evidence)


@pytest.mark.parametrize('altered', [ROWS.replace('Bf1-b5 a7-a6', 'd2-d4 e5-d4'),
                                   ROWS.replace('4. Bb5-a4 Ng8-f6', '4. d2-d3 d7-d6')])
def test_disagreeing_row_content_cannot_confirm_number(altered):
    _, _, evidence = merge(ROWS.replace('3.', '8.'), altered)
    assert not any(d['code'] == 'row_number_ocr_consensus' for d in evidence)


def test_trailing_ocr_number_in_empty_black_column_keeps_split_row():
    tokens, _ = c.tokenize([ROWS + '7. Ba4-b3 2...\nAn explanation.\n7... d7-d6'])
    rows = c.table_layout(tokens)
    start = next(i for i, t in enumerate(tokens) if t[:2] == ('num', (7, False)))
    assert start in rows
    assert tokens[start + 2][:2] == ('junk', '...')
    assert any(t[:2] == ('num', (7, True)) for t in tokens)


def test_wrapped_analysis_number_is_not_a_mainline_gap():
    text = (ROWS + 'Because 7. d3 d6 8. c3 Be6\n9. Qb3-.\n'
            '7. Ba4-b3 d7-d6\n8. c2-c3 O-O\n1-0')
    f = extract(text, table=True)
    assert len(list(f.games[0].mainline_moves())) == 16
    assert '9. Qb3-.' in list(f.games[0].mainline())[11].comment
    assert 'MissingMove' not in f.games[0].headers


@pytest.mark.parametrize('readings, expected', [
    (['h7-hb', '17-15'], 'h7h5'),
    (['h7-hb', '17-16'], 'h7h6'),
    (['h7-hb', '17-15', 'h7-h6'], None),
    (['h7-h6', '17-15'], None),  # a clear primary rank is not ambiguous
    (['h7-hb'], None),
    (['h7-hb', '17-14'], None),  # not one of the glyph's supported readings
    (['h7-hb', 'f7-f5'], None),  # a different readable file cannot supply a rank
])
def test_pawn_rank_needs_complementary_ocr_evidence(readings, expected):
    board = chess.Board()
    board.push_san('e4')
    move = c.pawn_rank_consensus(board, readings)
    assert (move.uci() if move else None) == expected
