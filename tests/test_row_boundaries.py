"""Source rows and page geometry, including rejection of ambiguous repairs."""
import copy
import json
from pathlib import Path

import chess
import pytest

import chess_converter as c
import page_layout as layout
from book_structure import clean_running_headers


GEOMETRY=json.loads((Path(__file__).parent/'fixtures/column_geometry.json').read_text(encoding='utf-8'))
TABLE_PREFIX='1. e2-e4 e7-e5\n2. Ng1-f3 Nb8-c6\n3. Bf1-b5 a7-a6\n'


@pytest.mark.parametrize('case',GEOMETRY,ids=[c['source'] for c in GEOMETRY])
def test_real_geometry_separates_columns_without_crossing_words(case):
    assert c.column_gap(case['words'],case['width'])==case['original_gap']
    gap=layout.refined_gap(case['words'],case['width'],c.column_gap,c.visual_lines,c.crosses)
    assert gap==case['expected_gap']
    regions=c.word_regions(case['words'],gap,case['width'])
    assert any(r.x1==gap for r in regions) and any(r.x0==gap for r in regions)
    if case['source']=='anderssen':
        assert len(regions)==2  # the orphaned player heading stays in the left column
        assert not any(c.crosses(w,gap) for w in case['words'])


def test_short_block_without_numbered_notation_on_both_sides_cannot_override_layout():
    case=next(c for c in GEOMETRY if c['source']=='spasski')
    words=[[*w[:4],'text'] for w in case['words']]
    assert layout.refined_gap(words,case['width'],c.column_gap,c.visual_lines,c.crosses) is None


@pytest.mark.parametrize('text',[
    '№ 3. Opening\nAlice - Bob\n1. e4 e5',
    'Long introduction\n1. e4 e5 2. Nf3 Nc6',
    '22. f3:e4 Re6:e4 28. Qf2:f6 Qd3:c3', # one mixed row is not enough
])
def test_ordinary_pages_do_not_trigger_a_layout_retry(text):
    assert not layout.needs_retry(text)


def test_missing_heading_must_be_recovered_not_deleted():
    before='№ 3. Opening\n'+'Prose in other column\n'*30+'Alice - Bob\n1. e4 e5'
    after='№ 3. Opening\nAlice - Bob\n1. e4 e5'
    assert layout.needs_retry(before) and layout.improved(before,after)
    assert not layout.improved(before,after.replace('№ 3. Opening\n',''))
    assert not layout.improved(after,before)


def test_alternating_titles_need_agreeing_printed_page_counters():
    pages=['12 – Глава первая\n1. e4 e5','В роли вундеркинда – 13\n2. Nf3 Nc6','14 – Глава первая\n3. Bb5 a6']
    clean=clean_running_headers(pages)
    assert all(p.startswith('\n') for p in clean)
    assert [p.count('\n') for p in clean]==[p.count('\n') for p in pages]
    alone=['В роли вундеркинда – 13\n2. Nf3 Nc6']
    assert clean_running_headers(alone)==alone
    wrong=['Unrelated upper text – 12\n1. e4 e5','Other upper text – 25\n2. Nf3 Nc6']
    assert clean_running_headers(wrong)==wrong


def test_header_cleanup_keeps_move_text_and_player_names():
    pages=['Alice Bob – 12\n1. e4 e5','Carol Dave – 13\n2. Nf3 Nc6']
    assert clean_running_headers(pages)==pages
    pages=['12 – 1. e4 e5','13 – 2. Nf3 Nc6']
    assert clean_running_headers(pages)==pages


def test_column_rule_before_complete_table_row_is_ocr_only():
    text='| 20. e3-ed! h5-h4'
    assert c.tokenize([text],{1})[0][0][:2]==('num',(20,False))
    assert c.tokenize([text])[0][0][:2]==('word','|')
    for other in ('| 20. e3-ed!','| 20. e3-ed! explanatory text','I 20. e3-ed! h5-h4'):
        assert c.tokenize([other],{1})[0][0][0]=='word'


@pytest.mark.parametrize('other,accepted',[
    ('20. e3-e4! h5-h4',True),('20. e3-e5 h5-h4',False),
    ('20. e2-e4 h5-h4',False),('20. e3-e4 h7-h5',False),
    ('21. e3-e4 h5-h4',False),
])
def test_advanced_pawn_still_needs_matching_source_row_and_legal_move(other,accepted):
    text=TABLE_PREFIX+'18. Qc2-f2 Nb8-d7\n19. Ba3-c1 Re8-e6\n| 20. e3-ed! h5-h4\n21. Ng3-h1 d5:e4'
    tokens,_=c.tokenize([text],{1})
    index=next(i for i,t in enumerate(tokens) if t[1]=='e3-ed!')
    alternatives={};evidence=[]
    c.add_source_row_readings(tokens,alternatives,[dict(text=other,page=1,bbox=[1,2,100,12],dpi=300)],evidence)
    board=chess.Board('6k1/8/8/7p/8/4P3/8/6K1 w - - 0 20')
    move=c.pawn_rank_consensus(board,[tokens[index][1],*alternatives.get(index,[])])
    assert (move==chess.Move.from_uci('e3e4'))==accepted


@pytest.mark.parametrize('fault',['wrong_number','ambiguous','conflicting_rank'])
def test_advanced_pawn_cannot_override_conflicting_or_ambiguous_source(fault):
    text=TABLE_PREFIX+'18. Qc2-f2 Nb8-d7\n19. Ba3-c1 Re8-e6\n20. e3-ed! h5-h4\n21. Ng3-h1 d5:e4'
    tokens,_=c.tokenize([text],{1});index=next(i for i,t in enumerate(tokens) if t[1]=='e3-ed!')
    row=dict(text='20. e3-e4! h5-h4',page=1,bbox=[1,2,100,12],dpi=300)
    records=[row,row] if fault=='ambiguous' else [row]
    if fault=='wrong_number':row['text']='19. e3-e4! h5-h4'
    alts={index:['e3-e5']} if fault=='conflicting_rank' else {}
    before=copy.deepcopy(alts)
    c.add_source_row_readings(tokens,alts,records,[])
    assert alts==before


def test_layout_retry_cache_is_reused_and_corrupt_cache_is_rebuilt(tmp_path,monkeypatch):
    before='№ 3. Opening\n'+'Prose\n'*30+'Alice - Bob\n1. e4 e5'
    after='№ 3. Opening\nAlice - Bob\n1. e4 e5'
    monkeypatch.setattr(c,'ocr_cache',lambda *a,**k:(tmp_path,'test'))
    monkeypatch.setattr(c,'find_tessdata',lambda *a:'unused')
    calls=[]
    def read(job,repair_columns,diagnostics):
        calls.append(job[2]);diagnostics.append(dict(original_gap=180,refined_gap=179))
        return job[2],after,[]
    monkeypatch.setattr(c,'ocr_page',read)
    cache=tmp_path/'layout-retry-v1-test-p1.json'
    cache.write_text('{"interrupted":true}')
    for _ in range(2):
        pages=[before];glyphs=[dict(page=1,dpi=300),dict(page=2,dpi=300)];diagnostics=[]
        c.refine_page_order('pdf',tmp_path/'source.pdf',tmp_path,pages,[0],glyphs,lambda *a:None,'eng','auto','auto',diagnostics)
        assert pages==[after] and glyphs==[dict(page=2,dpi=300)]
        assert diagnostics[0]['code']=='page_columns_recovered'
    assert calls==[0]
    saved=json.loads(cache.read_text());saved['primary_sha256']='old'
    cache.write_text(json.dumps(saved))
    c.refine_page_order('pdf',tmp_path/'source.pdf',tmp_path,[before],[0],[],lambda *a:None,'eng','auto','auto',[])
    assert calls==[0,0]


@pytest.mark.parametrize('cancel',[False,True])
def test_layout_retry_failure_keeps_base_export_but_cancel_propagates(tmp_path,monkeypatch,cancel):
    source,target=tmp_path/'source.pdf',tmp_path/'out.pgn';source.write_bytes(b'fixture')
    monkeypatch.setattr(c,'read_book_pages',lambda *a:[''])
    monkeypatch.setattr(c,'ocr_book',lambda *a,**k:{0:'1. e4 e5 2. Nf3 Nc6 3. Bb5 a6 1-0'})
    def fail(*a):raise c.Stopped() if cancel else OSError('test')
    monkeypatch.setattr(c,'refine_page_order',fail)
    if cancel:
        with pytest.raises(c.Stopped):c.book_to_pgn(source,target,dict(lang='en',ocr='always',mainline_only=True),lambda *a:None)
        assert not target.exists()
    else:
        c.book_to_pgn(source,target,dict(lang='en',ocr='always',mainline_only=True),lambda *a:None)
        report=json.loads(c.report_path_for(target).read_text(encoding='utf-8'))
        assert target.exists() and any(i['code']=='page_layout_unavailable' for i in report['issues'])
