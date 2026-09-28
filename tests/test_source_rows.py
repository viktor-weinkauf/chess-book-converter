"""Original page readings supplement a matched row, without inserting moves."""
import chess
import json
import pytest

import chess_converter as c


TEXT = ('1. d2-d4 d7-d5\n2. c2-c4 e7-ed\n3. Kb1-c3 e5:d4\n'
        '4. Qd1:d4 Kb8-c6\n5. Qd4:d5 Bc8-e6')


def setup(text=TEXT):
    tokens, _ = c.tokenize([text], {1})
    c.table_layout(tokens)
    index = next(i for i,t in enumerate(tokens) if t[1] == 'e7-ed')
    return tokens, index


def record(text='2. c2-c4 e7-e5', page=1):
    return {'text':text,'page':page,'bbox':[10,20,130,30],'dpi':300}


def test_original_row_supplies_a_literal_rank_with_provenance():
    tokens, index = setup()
    original = list(tokens)
    alternatives, evidence = {index:['e7-eb']}, []
    c.add_source_row_readings(tokens,alternatives,[record()],evidence)
    assert tokens == original
    assert alternatives[index] == ['e7-eb','e7-e5']
    assert evidence[0]['token'] == index
    assert evidence[0]['source_row']['bbox'] == [10,20,130,30]
    board = chess.Board()
    for san in ('d4','d5','c4'):board.push_san(san)
    assert c.pawn_rank_consensus(board,[tokens[index][1],*alternatives[index]]).uci() == 'e7e5'


@pytest.mark.parametrize('records', [
    [record(page=2)],
    [record('3. c2-c4 e7-e5')],
    [record('2. b2-b4 e7-e5')],
    [record('2. c2-c4 e7-eb')],
    [record('2. c2-c4 e7-e3')],
    [record('Instead 2. c2-c4 e7-e5')],
    [record(),record()],
])
def test_ambiguous_or_unmatched_source_row_is_not_used(records):
    tokens, index = setup()
    alternatives, evidence = {}, []
    c.add_source_row_readings(tokens,alternatives,records,evidence)
    assert not alternatives and not evidence


def test_conflicting_explicit_rank_is_not_overridden():
    tokens, index = setup()
    alternatives, evidence = {index:['e7-e6']}, []
    c.add_source_row_readings(tokens,alternatives,[record()],evidence)
    assert alternatives[index] == ['e7-e6']
    assert evidence[0]['code'] == 'source_row_conflict'


def test_repeated_row_number_on_same_page_cannot_borrow_another_games_reading():
    tokens, index = setup(TEXT + '\n2. c2-c4 e7-e6')
    alternatives, evidence = {}, []
    c.add_source_row_readings(tokens,alternatives,[record()],evidence)
    assert not alternatives


def test_complementary_source_rank_glyphs_confirm_a_pawn_capture():
    board = chess.Board()
    for san in ('d4','d5','c4','e5','Nc3'):board.push_san(san)
    assert c.pawn_rank_consensus(board,['eb:d4','ed:d4']).uci() == 'e5d4'
    assert c.pawn_rank_consensus(board,['eb:d4','eb:d4']) is None
    assert c.pawn_rank_consensus(board,['eb:d4','ed:d4','e6:d4']) is None
    assert c.pawn_rank_consensus(board,['eb:d4','ed:c4']) is None


def test_misread_queen_with_split_capture_is_one_move_token():
    tokens, _ = c.tokenize(['4. ddl: d4 Kb8-c6'],{1})
    assert [t[1] for t in tokens if t[0]=='word'] == ['Фdl:d4','Kb8-c6']
    clean, _ = c.tokenize(['4. ddl: d4 Kb8-c6'])
    assert [t[1] for t in clean if t[0]=='word'] == ['ddl:','d4','Kb8-c6']


def test_moved_row_number_before_ellipsis_keeps_black_move_and_next_white_row():
    text = ('1. e2-e4 e7-e5\n2. Ng1-f3 Nb8-c6\n3. Bf1-b5 ...\n'
            '3. 4. ce a7-a6\nBb5-a4 Ng8-f6\n5. O-O Bf8-e7\n1-0')
    tokens, lines = c.tokenize([text], {1})
    c.fix_row_numbers(tokens)
    rows = c.table_layout(tokens)
    f = c.GameFinder('Book',['en'],True,lines,{1},'en',table=rows,mainline_only=True)
    f.run(tokens)
    assert not f.problems
    assert [n.san() for n in f.games[0].mainline()] == ['e4','e5','Nf3','Nc6','Bb5','a6','Ba4','Nf6','O-O','Be7']
    assert f.games[0].headers['Result'] == '1-0'


def test_real_prose_between_two_numbers_and_move_is_not_an_ellipsis():
    text = '3. 4. пояснение a7-a6\nBb5-a4 Ng8-f6'
    tokens, _ = c.tokenize([text], {1})
    before = list(tokens)
    c.fix_row_numbers(tokens)
    assert tokens == before


def test_source_rows_cache_is_reused_and_corrupt_cache_is_rebuilt(tmp_path, monkeypatch):
    source = tmp_path/'source.pdf'
    with c.fitz.open() as doc:
        page = doc.new_page()
        page.insert_text((30,50),'2. c2-c4 e7-e5')
        doc.save(source)
    monkeypatch.setattr(c,'ocr_cache',lambda *a,**k:(tmp_path,'test'))
    monkeypatch.setattr(c,'find_tessdata',lambda *a:str(tmp_path))
    calls = []
    def read(page, **kwargs):
        calls.append(1)
        return page.get_textpage()
    monkeypatch.setattr(c.fitz.Page,'get_textpage_ocr',read)
    cache = tmp_path/'source-rows-v1-test-p1.json'
    cache.write_text('{"interrupted":true}')
    first = c.read_source_rows('pdf',source,{0},'eng',tmp_path,lambda *a:None)
    second = c.read_source_rows('pdf',source,{0},'eng',tmp_path,lambda *a:None)
    assert first == second and len(calls) == 1
    assert first[0]['text'] == '2. c2-c4 e7-e5' and first[0]['page'] == 1
    assert len(first[0]['bbox']) == 4


@pytest.mark.parametrize('cancel', [False,True])
def test_optional_reading_failure_keeps_export_but_cancellation_propagates(tmp_path, monkeypatch, cancel):
    source, target = tmp_path/'source.pdf', tmp_path/'out.pgn'
    source.write_bytes(b'fixture')
    monkeypatch.setattr(c,'read_book_pages',lambda *a:[''])
    monkeypatch.setattr(c,'ocr_book',lambda *a,**k:{0:TEXT+'\n0-1'})
    def fail(*a):
        raise c.Stopped() if cancel else OSError('test reading failed')
    monkeypatch.setattr(c,'read_source_rows',fail)
    if cancel:
        with pytest.raises(c.Stopped):
            c.book_to_pgn(source,target,{'lang':'en','ocr':'always','mainline_only':True},lambda *a:None)
        assert not target.exists()
    else:
        c.book_to_pgn(source,target,{'lang':'en','ocr':'always','mainline_only':True},lambda *a:None)
        report=json.loads(c.report_path_for(target).read_text(encoding='utf-8'))
        assert target.exists() and report['output']['written']
        assert any(i['code']=='source_rows_unavailable' for i in report['issues'])
