"""Lost half-rows must not shift the mainline or borrow another row's moves."""
import pytest

import chess_converter as c


OPENING = '1. e2-e4 e7-e5\n2. Ng1-f3 Nb8-c6\n'
SPLIT = ('8. e2-e3 Bf8-e7\n9. Bf1-d3 O-O\n10. ... Nd7-f8\n'
         '1. h2-h3 ...\n11. ... Nf6-e4\n12. Bg5:e7 ...')


def source(text, page=1):
    return {'text': text, 'page': page, 'bbox': [10, 20, 100, 30], 'dpi': 300}


def readings():
    return [source('10. ... Nd7-f8'), source('11. h3_h3 Cl'), source('11. ... Nf6-e4')]


def test_number_repair_requires_source_and_preserves_all_move_tokens():
    tokens, _ = c.tokenize([SPLIT], {1})
    before = list(tokens)
    evidence = []
    c.repair_source_row_numbers(tokens, readings(), evidence)
    changed = [i for i in range(len(tokens)) if tokens[i] != before[i]]
    assert len(changed) == 1
    assert tokens[changed[0]][:2] == ('num', (11, False))
    assert evidence[0]['source_rows'][1]['bbox'] == [10, 20, 100, 30]
    assert evidence[0]['printed_number'] == 1


@pytest.mark.parametrize('rows', [
    [], readings() + readings(),
    [source('10. ... Nd7-f8'), source('1. h3_h3 Cl'), source('11. ... Nf6-e4')],
    [source('10. ... Nd7-f8'), source('11. h3_h3 Cl', 2), source('11. ... Nf6-e4')],
    [source('10. ... Ra8-e8'), source('11. h3_h3 Cl'), source('11. ... Nf6-e4')],
    [source('10. ... Nd7-f8'), source('11. a2-a4 ...'), source('11. ... Nf6-e4')],
    [source('10. ... Nd7-f8'), source('11. h3_h3 Cl')],
])
def test_number_repair_abstains_without_unique_three_row_source_match(rows):
    tokens, _ = c.tokenize([SPLIT], {1})
    before = list(tokens)
    evidence = []
    c.repair_source_row_numbers(tokens, rows, evidence)
    assert tokens == before and not evidence


def test_number_repair_never_crosses_a_game_heading():
    text = SPLIT.replace('1. h2', '№ 2.\nAlice - Bob\n1. h2')
    tokens, _ = c.tokenize([text], {1}, boundaries=True)
    before = list(tokens)
    c.repair_source_row_numbers(tokens, readings(), [])
    assert tokens == before


def test_missing_white_half_row_cannot_take_the_black_alternate():
    primary = OPENING + ('3. Bf1-b5 ...\n3. ... a7-a6\n'
                         '4. ... Ng8-f6\n5. O-O Bf8-e7\n1-0')
    alternate = OPENING + ('3. ... a7-a6\n4. Bb5-a4 ...\n'
                           '4. ... Ng8-f6\n5. O-O Bf8-e7\n1-0')
    tokens, lines = c.tokenize([primary], {1})
    other, _ = c.tokenize([alternate], {1})
    merged, alts = c.merge_second_reading(tokens, other)
    finder = c.GameFinder('Book', ['en'], True, lines, {1}, 'en',
                          table=c.table_layout(merged), alternatives=alts, mainline_only=True)
    finder.run(merged)
    assert not finder.problems
    assert [n.san() for n in finder.games[0].mainline()] == [
        'e4', 'e5', 'Nf3', 'Nc6', 'Bb5', 'a6', 'Ba4', 'Nf6', 'O-O', 'Be7']
    assert finder.games[0].headers['Result'] == '1-0'


def test_equal_similarity_half_rows_do_not_take_the_only_alternate():
    primary = ('Earlier 1. e4 e5 2. Nf3 Nc6\n3. Qa2.\n' + OPENING
               + '3. a2-a3 ...\n3. ... a7-a6\n4. Bf1-b5 Ng8-f6\n5. O-O Bf8-e7')
    alternate = OPENING + '3. a4-a5 ...\n4. Bf1-b5 Ng8-f6\n5. O-O Bf8-e7'
    tokens, _ = c.tokenize([primary], {1})
    other, _ = c.tokenize([alternate], {1})
    merged, alts = c.merge_second_reading(tokens, other)
    assert not any('a4-a5' in words for words in alts.values())
    assert not any(t[1] == 'a4-a5' for t in merged)


def test_two_numbers_and_four_column_sorted_moves_are_separated_in_order():
    text = OPENING + '4. 3. Bf1-b5 Bb5-a4 + a7-a6 Ng8-f6 +\n5. O-O Bf8-e7\n1-0'
    tokens, lines = c.tokenize([text], {1})
    c.fix_row_numbers(tokens)
    finder = c.GameFinder('Book', ['en'], True, lines, {1}, 'en',
                          table=c.table_layout(tokens), mainline_only=True)
    finder.run(tokens)
    assert not finder.problems
    assert [n.san() for n in finder.games[0].mainline()] == [
        'e4', 'e5', 'Nf3', 'Nc6', 'Bb5', 'a6', 'Ba4', 'Nf6', 'O-O', 'Be7']


@pytest.mark.parametrize('text', [
    '2. Ng1-f3 Nb8-c6\n4. 3. Bf1-b5 Bb5-a4 a7-a6 Ng8-f6\n6. O-O Bf8-e7',
    '2. Ng1-f3 Nb8-c6\n4. 3. Bf1-b5 explanation Bb5-a4 a7-a6 Ng8-f6\n5. O-O Bf8-e7',
])
def test_merged_rows_need_neighbours_and_no_prose(text):
    tokens, _ = c.tokenize([text], {1})
    before = list(tokens)
    c.fix_row_numbers(tokens)
    assert tokens == before


def test_split_capture_with_cyrillic_soft_sign_file_stays_one_move():
    tokens, _ = c.tokenize(['50. ab : Ь4+'], {1})
    assert [t[1] for t in tokens if t[0] == 'word'] == ['ab:Ь4+']
