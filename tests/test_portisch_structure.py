"""Structural failures found on a full chapter, not just an isolated game."""
import pytest
import chess.pgn
from pathlib import Path

import chess_converter as c
from book_structure import clean_running_headers, section_events


ROWS = """1. e2-e4 e7-e5
2. Ng1-f3 Nb8-c6
3. Bf1-b5 a7-a6
4. Bb5-a4 Ng8-f6
5. O-O Bf8-e7"""


def extract(pages):
    ocr = set(range(1, len(pages) + 1))
    tokens, lines = c.tokenize(clean_running_headers(pages), ocr, boundaries=True)
    finder = c.GameFinder('Book', ['en'], True, lines, ocr, 'en', table=c.table_layout(tokens))
    finder.run(tokens)
    assert not finder.problems
    return finder, tokens


def test_plain_title_names_and_long_intro_across_page_boundary():
    f, _ = extract(['1. Sicilian Defence\nAlice Van Oosterom\nBudapest, 1956\n' +
                    'Introductory prose.\n' * 40,
                    'An essay about music.\n' + ROWS + '\n1-0'])
    assert len(f.games) == 1
    g = f.games[0]
    assert (g.headers['BookGame'], g.headers['White'], g.headers['Black']) == ('1', 'Alice', 'Van Oosterom')
    assert (g.headers['Site'], g.headers['Date']) == ('Budapest', '1956.??.??')
    assert 'essay' not in str(g) and 'Introductory' not in str(g)


def test_repeated_players_are_not_removed_as_running_headers():
    pages = ['1. English Opening\nAlice Bob\n1. c4 e5', '2. English Opening\nAlice Bob\n1. c4 c5']
    assert clean_running_headers(pages) == pages


def test_numbered_prose_and_contents_are_not_game_titles():
    lines = ['1. Some positional principles', 'Study the centre first.', '1. e4 e5',
             '2. Sicilian Defence', 'Alice Bob', 'Budapest, 1956', 'page 100',
             '3. French Defence', 'Carol Dave', 'Paris, 1957', '1. e4 e6']
    boundaries = {i for i, event in section_events(lines).items() if event[0] == 'game_boundary'}
    assert boundaries == {7}


def test_gap_in_first_plain_numbered_game_does_not_swallow_the_next():
    f, _ = extract(['1. Ruy Lopez\nAlice Bob\n' + ROWS + '\n7. d2-d3 d7-d6\n'
                    '2. English Opening\nCarol Dave\n1. c2-c4 e7-e5\n2. Nb1-c3 Ng8-f6\n3. Ng1-f3 Nb8-c6\n1-0'])
    assert [g.headers['BookGame'] for g in f.games] == ['1', '2']
    assert [len(list(g.mainline_moves())) for g in f.games] == [10, 6]
    assert f.games[0].headers['MissingMove'] == '6.'
    assert 'Carol' not in str(f.games[0])


@pytest.mark.parametrize('printed, joined', [
    ('Ki3: d4', 'Ki3:d4'), ('eb : 45', 'eb:45'),
    ('ФВ : 16', 'ФВ:16'), ('JIbl: b54-', 'Лbl:b5+'),
    ('ch: d4', 'ch:d4'), ('Kf4 : еб!', 'Kf4:еб!'),
])
def test_damaged_capture_is_one_token(printed, joined):
    tokens, _ = c.tokenize(['12. ' + printed], {1})
    assert [t[1] for t in tokens if t[0] == 'word'] == [joined]


@pytest.mark.parametrize('number', ['i.', 'lI.', 'I.'])
def test_damaged_first_row_is_confirmed_by_row_two(number):
    f, _ = extract([ROWS.replace('1.', number, 1) + '\n6. Rf1-e1 b7-b5\n1-0'])
    assert len(f.games) == 1 and len(list(f.games[0].mainline_moves())) == 12


def test_split_black_row_uses_source_markers_and_next_row():
    f, _ = extract([ROWS + '\n6. Rf1-e1 ce\nA comment between the two halves.\n'
                    '6. ce 67-b5\n7. Ba4-b3 d7-d6\n1-0'])
    g = f.games[0]
    assert len(list(g.mainline_moves())) == 14
    assert list(g.mainline())[11].san() == 'b5'
    assert any(d['code'] == 'move_side_recovered' for d in f.diagnostics)


def test_fuzzy_black_move_without_split_row_evidence_remains_unresolved():
    f, _ = extract([ROWS + '\n6. Rf1-e1\n6. 67-b5\n7. Ba4-b3 d7-d6\n1-0'])
    assert f.games[0].headers['MissingMove'] == '6...'
    assert len(list(f.games[0].mainline_moves())) == 11


def test_explicit_victory_keeps_bounded_final_explanation():
    f, _ = extract(['1. Ruy Lopez\nAlice Bob\n' + ROWS +
                    '\nБелые выиграли: король беззащитен.\n'
                    '2. English Opening\nCarol Dave\n1. c4 e5 2. Nc3 Nf6 3. Nf3 Nc6 0-1'])
    assert f.games[0].headers['Result'] == '1-0'
    assert 'король беззащитен' in f.games[0].end().comment
    assert 'English' not in str(f.games[0])


def test_alternate_opening_prefix_requires_two_matching_anchor_rows():
    prefix = '1. Ruy Lopez\nAlice Bob\n'
    main = prefix + '4. Bb5-a4 Ng8-f6\n5. O-O Bf8-e7\n1-0'
    tokens, lines = c.tokenize([main], {1}, boundaries=True)
    alt, _ = c.tokenize([prefix + ROWS + '\n1-0'], {1}, boundaries=True)
    tokens, readings = c.merge_second_reading(tokens, alt)
    f = c.GameFinder('Book', ['en'], True, lines, {1}, 'en', table=c.table_layout(tokens), alternatives=readings)
    f.run(tokens)
    assert not f.problems
    assert len(f.games) == 1 and len(list(f.games[0].mainline_moves())) == 10


def test_alternate_opening_does_not_cross_a_different_heading():
    tokens, _ = c.tokenize(['1. First Game\nAlice Bob\n4. Bb5-a4 Ng8-f6\n5. O-O Bf8-e7'], {1}, boundaries=True)
    alt, _ = c.tokenize(['2. Second Game\nCarol Dave\n' + ROWS], {1}, boundaries=True)
    merged, _ = c.merge_second_reading(tokens, alt)
    assert not any(t[:2] == ('num', (1, False)) for t in merged)


@pytest.mark.parametrize('word, expected', [('Jial—cl!', 'Лal—cl!'), ('Ji1—f2', 'Лi1—f2'),
                                           ('Лfl-——cl!', 'Лfl—cl!')])
def test_rook_glyph_and_multiple_dashes_do_not_change_squares(word, expected):
    tokens, _ = c.tokenize([word], {1})
    assert [t[1] for t in tokens] == [expected]


def test_damaged_number_with_close_moves_needs_an_exact_following_row():
    f, _ = extract([ROWS.replace('3. Bf1-b5', '83. Bf1-b5') + '\n1-0'])
    assert len(list(f.games[0].mainline_moves())) == 10
    assert any(d['code'] == 'move_number_recovered' for d in f.diagnostics)


def test_second_reading_cannot_borrow_rows_from_the_next_game():
    first = '1. Ruy Lopez\nAlice Bob\n' + ROWS + '\n1-0\n'
    second = '2. English Opening\nCarol Dave\n1. c2-c4 e7-e5\n2. Nb1-c3 Ng8-f6\n3. Ng1-f3 Nb8-c6\n0-1'
    tokens, lines = c.tokenize([first + second], {1}, boundaries=True)
    # In the alternate pass, game one's rows disappeared. Row numbers in the
    # surviving second game must not overwrite the first game's notation.
    alt, _ = c.tokenize([second], {1}, boundaries=True)
    merged, readings = c.merge_second_reading(tokens, alt)
    f = c.GameFinder('Book', ['en'], True, lines, {1}, 'en', table=c.table_layout(merged), alternatives=readings)
    f.run(merged)
    assert [g.headers['BookGame'] for g in f.games] == ['1', '2']
    assert next(iter(f.games[0].mainline())).san() == 'e4'
    assert next(iter(f.games[1].mainline())).san() == 'c4'


def test_later_wrong_source_square_does_not_swap_earlier_identified_rooks():
    with (Path(__file__).parent / 'fixtures/portisch-donner-1966-checked-mainline.pgn').open() as stream:
        reference = chess.pgn.read_game(stream)
    moves = list(reference.mainline_moves())
    board = reference.board()
    for move in moves[:28]:
        board.push(move)
    text = board.fen() + '''
15. Rf1-c1 ce
15... Nf6-e4
16. Rc1-c7 Be2-a6
17. Qb3-f3 Ne4-g5
18. Qf3-f5 Ba6-c4
19. Rc7:b7
1-0'''
    f, _ = extract([text])
    assert list(f.games[0].mainline_moves()) == moves[28:]
