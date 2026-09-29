import base64
import json
import zlib
from pathlib import Path

import pytest
import fitz

import chess_converter as c
import page_layout as layout
import table_row_ocr as row_ocr


def test_real_larsen_column_rule_does_not_interrupt_left_column():
    case = json.loads((Path(__file__).parent/'fixtures/larsen-column-words.json').read_text(encoding='utf-8'))
    words, width = case['words'], case['width']
    assert c.column_gap(words, width) == 173
    gap = layout.refined_gap(words, width, c.column_gap, c.visual_lines, c.crosses)
    assert gap == 170
    regions = c.word_regions(words, gap, width)
    assert len(regions) == 2  # complete left column followed by complete right
    assert regions[0].x1 == gap and regions[1].x0 == gap
    assert regions[0].y0 < 53 and regions[0].y1 > 510


def test_backwards_table_jump_requests_geometry_but_never_reorders_text():
    before = '10. f2-f4?\n11. ... h7-h5\n12. h2-h3\n14. Rh1-g1\n10. ... Kf6-g4!\n11. g2-g3'
    after = '10. f2-f4?\n10. ... Kf6-g4!\n11. g2-g3\n11. ... h7-h5\n12. h2-h3\n14. Rh1-g1'
    assert layout.needs_retry(before)
    assert layout.improved(before, after)
    assert not layout.improved(after, before)


@pytest.mark.parametrize('text', [
    '14. Rh1-g1\nЕсли 10. Kf6-g4!, то позиция меняется.',
    '14. Rh1-g1\n10. Kf6-g4! лучше прежнего продолжения.',
    '14. Rh1-g1\n№ 123\n1. d2-d4',
    '14. Rh1-g1\n13. Kf6-g4!',
])
def test_comment_or_new_game_is_not_column_disorder(text):
    assert layout.table_backtracks(text) == 0


READINGS = ['7...\n ®d8—a5+ |\n', '7...\nGd8—a5+\n']


def target():
    return row_ocr.targets('7. ке. Фа8—а5--')[0][1]


def test_black_row_requires_both_printed_ellipsis_and_coordinates():
    assert row_ocr.agreed_reading(target(), READINGS) == '7. ... Фd8—a5+'


@pytest.mark.parametrize('second', [
    '', '7. Gd8—a5+', '7... Gd8—b5+', '8... Gd8—a5+',
    '7... Gd7—a5+', '7... Gd8—a5', '7... Gd8—a5+ prose',
    '7... Gd8—a5+ 8. e4', '7... Ga8—a5+',
])
def test_incomplete_conflicting_or_other_source_row_is_rejected(second):
    assert row_ocr.agreed_reading(target(), [READINGS[0], second]) is None


@pytest.mark.parametrize('text', [
    '7. ... Фа8—а5+', '7. Ке2 Фа8—а5+', '7. ке. Фа8—а5-- prose',
    '7. ке. Фа8—а5--\n7. ке. Фа8—а5--',
])
def test_clean_ambiguous_or_prose_rows_are_not_targets(text):
    assert not row_ocr.targets(text)


def test_dehyphenating_prose_cannot_promote_its_move_to_a_table_row():
    text = ('1. e2-e4 e7-e5\n2. Ng1-f3 Nb8-c6\n3. Bf1-b5 a7-a6\n'
            '4. Bb5-a4 Ng8-f6\n5. O-O Bf8-e7\n'
            'чтобы только после этого продол-\nжать 8. . .ФЬб.\n8. c2-c3 c5:d4')
    tokens, lines = c.tokenize([text], {1})
    rows = c.table_layout(tokens)
    black = next(i for i, t in enumerate(tokens) if t[:2] == ('num', (8, True)))
    white = next(i for i, t in enumerate(tokens) if t[:2] == ('num', (8, False)))
    assert rows and black not in rows and white in rows
    assert 'продолжать' in ' '.join(str(t[1]) for t in tokens)
    assert lines[int(tokens[black][3])] == 'жать 8. . .ФЬб.'


def test_real_keres_pixels_read_ellipsis_and_origin_in_both_scales():
    case = json.loads((Path(__file__).parent/'fixtures/keres-black-row.json').read_text(encoding='utf-8'))
    pix = fitz.Pixmap(fitz.csGRAY, case['width'], case['height'],
                      zlib.decompress(base64.b64decode(case['samples'])), False)
    with fitz.open() as doc:
        page = doc.new_page(width=pix.width*72/300, height=pix.height*72/300)
        page.insert_image(page.rect, pixmap=pix)
        readings = row_ocr.read_crop(page, fitz.Rect(case['bbox']), c.find_tessdata('eng'))
        assert row_ocr.agreed_reading(target(), readings) == '7. ... Фd8—a5+'
        page.draw_rect(page.rect, color=None, fill=(1,1,1), overlay=True)
        blank = row_ocr.read_crop(page, fitz.Rect(case['bbox']), c.find_tessdata('eng'))
        assert row_ocr.agreed_reading(target(), blank) is None


def test_damaged_origin_rank_keeps_capture_destination_attached():
    tokens, _ = c.tokenize(['8. c2—c3 со: d4'], {1})
    assert [t[1] for t in tokens if t[0] == 'word'] == ['c2—c3', 'со:d4']


@pytest.mark.parametrize('boundary', [False, True])
def test_quoted_resignation_does_not_close_game_with_expected_row_still_ahead(boundary):
    text = ('№ 1\nAlice - Bob\n1. e2-e4 e7-e5\n2. Ng1-f3 Nb8-c6\n'
            '3. Bf1-b5 a7-a6\n4. Bb5-a4 Ng8-f6\n5. d2-d3 Bf8-e7\n'
            'В другой партии было 8. h4 h5, и черные сдались.\n')
    if boundary:
        text += '№ 2\nCarol - Dave\n1. d2-d4 d7-d5\n'
    text += '6. Nb1-c3 d7-d6\n7. Bc1-e3 O-O\nБелые сдались.'
    tokens, lines = c.tokenize([text], {1}, boundaries=True)
    finder = c.GameFinder('Book', ['en','ru'], True, lines, ocr_pages={1},
                          table=c.table_layout(tokens), mainline_only=True)
    finder.run(tokens)
    assert not finder.problems
    game = finder.games[0]
    assert len(list(game.mainline_moves())) == (10 if boundary else 14)
    assert game.headers['Result'] == ('1-0' if boundary else '0-1')
    if not boundary:
        assert any(d['code'] == 'analysis_result_retained' for d in finder.diagnostics)
