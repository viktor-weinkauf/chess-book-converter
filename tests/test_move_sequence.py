"""Numbers constrain extraction; legal moves cannot fill missing source turns."""
import io
import json
from pathlib import Path

import chess.pgn
import pytest

import chess_converter as c
from conversion_quality import serialize_games


def extract(text, table=False, visual=(), ocr=True):
    tokens, lines = c.tokenize([text], {1} if ocr else set(), boundaries=True)
    rows = c.table_layout(tokens) if table else None
    finder = c.GameFinder('Book', ['en'], True, lines, {1} if ocr else set(), 'en',
                          table=rows, visual_tokens=visual)
    finder.run(tokens)
    assert not finder.problems
    return finder, tokens


FRIEDEMANN_ROWS = '''№ 2.
Friedemann - Keres
1. d2-d4 Ng8-f6
2. c2-c4 e7-e6
3. Ng1-f3 d7-d5
4. Nb1-c3 c7-c6
5. Bc1-g5 Nb8-d7
7. e2-e3 Bf8-e7
8. Bf1-d3 O-O
9. Qd1-c2 Rf8-e8
39... Nd6-f5
40. Ne5-f3 h6-h5
0-1'''


def test_missing_whole_turn_stops_before_later_legal_row():
    f, _ = extract(FRIEDEMANN_ROWS, table=True)
    game = f.games[0]
    assert len(list(game.mainline_moves())) == 10
    assert game.end().san() == 'Nbd7'
    assert game.headers['Result'] == '*'
    assert game.headers['SourceResult'] == '0-1'
    assert game.headers['MissingMove'] == '6.'
    assert game.headers['ExtractionStatus'] == 'incomplete'
    assert '39' not in str(game)
    gap = next(d for d in f.diagnostics if d['code'] == 'move_sequence_gap')
    assert (gap['expected_number'], gap['printed_number'], gap['expected_side']) == (6, 7, 'white')
    restored = chess.pgn.read_game(io.StringIO(serialize_games(f.games)))
    assert len(list(restored.mainline_moves())) == 10


@pytest.mark.parametrize('ocr', [False, True])
def test_inline_missing_turn_is_not_digit_correction(ocr):
    f, _ = extract('№ 1.\nAlice - Bob\n1. e4 e5 3. Nf3 Nc6 4. Bb5 a6 1-0', ocr=ocr)
    assert len(list(f.games[0].mainline_moves())) == 2
    assert f.games[0].headers['MissingMove'] == '2.'


def test_gap_does_not_prevent_next_numbered_game():
    f, _ = extract(FRIEDEMANN_ROWS + '\n№ 3.\nCarol - Dave\n1. e2-e4 e7-e5\n2. Ng1-f3 Nb8-c6\n3. Bf1-b5 a7-a6\n1-0', table=True)
    assert [g.headers['BookGame'] for g in f.games] == ['2', '3']
    assert [len(list(g.mainline_moves())) for g in f.games] == [10, 6]
    assert f.games[1].headers['Result'] == '1-0'
    assert 'ExtractionStatus' not in f.games[1].headers


def test_comment_reference_to_future_move_does_not_stop_mainline():
    f, _ = extract('№ 1.\nAlice - Bob\n1. e4 e5 2. Nf3 The threat is 9. Bb5, however play continues. 2...Nc6 3. Bb5 a6 1-0')
    assert len(list(f.games[0].mainline_moves())) == 6
    assert not any(d['code'] == 'move_sequence_gap' for d in f.diagnostics)


def test_table_cannot_borrow_next_rows_black_move_to_fill_gap():
    text='№ 1.\nAlice - Bob\n1. e2-e4\n2. Ng1-f3 Nb8-c6\n3. Bf1-b5 a7-a6\n4. Bb5-a4 Ng8-f6\n5. O-O Bf8-e7\n1-0'
    f, _ = extract(text, table=True)
    assert len(list(f.games[0].mainline_moves())) == 1
    assert f.games[0].headers['MissingMove'] == '1...'


def test_missing_ellipsis_requires_an_exact_black_only_move():
    text='№ 1.\nAlice - Bob\n1. e2-e4\n1. e7-e5\n2. Ng1-f3 Nb8-c6\n3. Bf1-b5 a7-a6\n4. Bb5-a4 Ng8-f6\n1-0'
    f, _ = extract(text, table=True)
    assert len(list(f.games[0].mainline_moves())) == 8
    assert any(d['code']=='move_side_recovered' for d in f.diagnostics)


def test_same_number_for_the_wrong_side_is_a_gap():
    f, _ = extract('№ 1.\nAlice - Bob\n1. e4 e5 2...Nf6 3. Nc3 0-1')
    game = f.games[0]
    assert len(list(game.mainline_moves())) == 2
    assert game.headers['MissingMove'] == '2.'


def test_matching_number_later_cannot_resume_after_an_established_gap():
    f, _ = extract('№ 1.\nAlice - Bob\n1. e4 e5 3. Nf3 2. Nc3 Nc6 1-0')
    assert len(list(f.games[0].mainline_moves())) == 2
    assert f.games[0].headers['ExtractionStatus'] == 'incomplete'


def test_corrupt_number_needs_exact_moves_and_next_row_corroboration():
    text='№ 1.\nAlice - Bob\n1. e2-e4 e7-e5\n8. Ng1-f3 Nb8-c6\n3. Bf1-b5 a7-a6\n4. Bb5-a4 Ng8-f6\n5. O-O Bf8-e7\n1-0'
    f, _ = extract(text, table=True)
    assert len(list(f.games[0].mainline_moves())) == 10
    assert f.games[0].headers['Result'] == '1-0'
    assert any(d['code']=='move_number_recovered' for d in f.diagnostics)
    # A real omission has 4 after 3; it is not a mislabeled 2 followed by 3.
    g, _ = extract(text.replace('8. Ng1-f3 Nb8-c6\n3. Bf1-b5',
                                '3. Ng1-f3 Nb8-c6\n4. Bf1-b5'), table=True)
    assert len(list(g.games[0].mainline_moves())) == 2


def test_clean_table_does_not_repair_numbers_when_ocr_is_off():
    text=FRIEDEMANN_ROWS.replace('7. e2-e3 Bf8-e7\n8.', '8. e2-e3 Bf8-e7\n7.')
    f, _ = extract(text, table=True, ocr=False)
    assert len(list(f.games[0].mainline_moves())) == 10


def test_three_exact_misaligned_moves_can_recover_black_half_turn():
    text='№ 1.\nAlice - Bob\n1. e2-e4\n2. Ng1-f3 e7-e5 Nb8-c6\n3. Bf1-b5 a7-a6\n4. Bb5-a4 Ng8-f6\n5. O-O Bf8-e7\n1-0'
    f, _ = extract(text, table=True)
    assert len(list(f.games[0].mainline_moves())) == 10
    assert f.games[0].headers['Result'] == '1-0'


def test_repeated_mainline_number_does_not_append_or_destroy_later_moves():
    text='№ 1.\nAlice - Bob\n1. e4 e5 2. Nf3 Nc6\n2... unreadable\n3. Bb5 a6 1-0'
    tokens, _ = c.tokenize([text], {1}, boundaries=True)
    repeated=next(i for i,t in enumerate(tokens) if t[:2]==('num',(2,True)))
    f, _ = extract(text, visual={repeated})
    assert len(list(f.games[0].mainline_moves())) == 6
    assert any(d['code']=='move_number_repeated' for d in f.diagnostics)


def test_numbered_variation_and_mainline_rejoin_remain_valid():
    f, _ = extract('1. e4 e5 2. Nf3 (2. Nc3 Nf6 3. Bc4) Nc6 3. Bb5 a6 1-0')
    assert len(list(f.games[0].mainline_moves())) == 6
    assert len(f.games[0].variations[0].variations[0].variations) == 2
    assert not any(d['code']=='move_sequence_gap' for d in f.diagnostics)


def test_page_change_and_split_black_move_keep_the_same_sequence():
    pages=['№ 1.\nAlice - Bob\n1. e4 e5 2. Nf3', '2...Nc6 3. Bb5 a6 1-0']
    tokens,lines=c.tokenize(pages,{1,2},boundaries=True)
    f=c.GameFinder('Book',['en'],True,lines,{1,2})
    f.run(tokens)
    assert not f.problems
    assert len(list(f.games[0].mainline_moves()))==6
    assert not any(d['code']=='move_sequence_gap' for d in f.diagnostics)


def test_initial_fen_numbering_is_preserved_then_gaps_are_enforced():
    f,_=extract('8/8/8/8/8/6k1/4p3/6K1 b - - 0 1\n42... e1=Q+ 44. Kh1 0-1')
    game=f.games[0]
    assert game.board().fullmove_number==42
    assert len(list(game.mainline_moves()))==1
    assert game.headers['MissingMove']=='43.'


def test_a_number_read_from_pixels_is_not_overridden_by_row_lookahead():
    text='№ 1.\nAlice - Bob\n1. e2-e4 e7-e5\n8. Ng1-f3 Nb8-c6\n3. Bf1-b5 a7-a6\n4. Bb5-a4 Ng8-f6\n5. O-O Bf8-e7\n1-0'
    tokens,_=c.tokenize([text],{1},boundaries=True)
    index=next(i for i,t in enumerate(tokens) if t[:2]==('num',(8,False)))
    f,_=extract(text,table=True,visual={index})
    assert len(list(f.games[0].mainline_moves()))==2


def test_analysis_after_the_last_move_cannot_be_promoted_across_a_gap():
    f,_=extract('№ 1.\nAlice - Bob\n1. e4 (1...c5 2. Nf3) 2. Bb5 1-0')
    game=f.games[0]
    assert len(list(game.mainline_moves()))==1
    assert not game.end().variations
    gap=next(d for d in f.diagnostics if d['code']=='move_sequence_gap')
    assert {n['uci'] for n in gap['deferred_variations']['nodes']}=={'c7c5','g1f3'}


def test_open_bracket_does_not_hide_a_gap_at_a_bold_mainline_anchor():
    text='№ 1.\nAlice - Bob\n1. e4 e5 (1...c5 2. Nf3\n3. Bb5 a6 1-0'
    tokens,_=c.tokenize([text],{1},boundaries=True)
    anchor=next(i for i,t in enumerate(tokens) if t[:2]==('num',(3,False)))
    f,_=extract(text,visual={anchor})
    assert len(list(f.games[0].mainline_moves()))==2
    assert f.games[0].headers['MissingMove']=='2.'


def test_report_preserves_unread_source_and_strict_export_keeps_existing_file(tmp_path):
    import fitz
    source=tmp_path/'book.pdf'
    with fitz.open() as doc:
        page=doc.new_page()
        page.insert_text((50,70), 'Alice - Bob\n1. e4 e5 3. Nf3 Nc6 4. Bb5 a6 1-0')
        doc.save(source)
    output=tmp_path/'out.pgn'
    message=c.convert(source,output,{'ocr':'off'})
    assert 'break in printed move order' in message
    pgn=output.read_text(encoding='utf-8')
    assert '[Result "*"]' in pgn and '[SourceResult "1-0"]' in pgn
    report=json.loads(Path(str(output)+'.report.json').read_text(encoding='utf-8'))
    assert report['summary']['incomplete_games']==1
    gap=next(d for d in report['issues'] if d['code']=='move_sequence_gap')
    assert gap['severity']=='error'
    assert any('3. Nf3' in line['text'] for line in gap['unparsed_source'])
    assert 'Nf3' not in pgn
    with pytest.raises(c.ReviewRequired):
        c.convert(source,output,{'ocr':'off','strict':True})
    assert output.read_text(encoding='utf-8')==pgn
