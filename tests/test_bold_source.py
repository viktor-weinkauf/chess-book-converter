import base64
import copy
import json
from pathlib import Path
import zlib

import fitz
import pytest

import bold_ocr as b
import chess_converter as c

CASES = json.loads((Path(__file__).parent/'fixtures/bold_source_words.json').read_text(encoding='utf-8'))


def fixture(case):
    return (fitz.Pixmap(fitz.csGRAY, case['width'], case['height'],
                        zlib.decompress(base64.b64decode(case['samples'])), False),
            copy.deepcopy(case['records']))


@pytest.mark.parametrize('case', CASES, ids=[c['expected'] for c in CASES])
def test_original_pixels_recover_the_complete_word_and_record_evidence(case):
    pix, records = fixture(case)
    before = copy.deepcopy(records[0])
    assert b.refine_page(pix,case['text'],records) == case['expected']
    assert records[0]['source_refined'] and records[0]['reading'] == 'whole_word'
    assert records[0]['primary_reading'] == case['text']
    assert records[0]['primary_characters'] == before['characters']
    assert records[0]['characters']
    assert c.coordinate_ocr.valid_evidence(records[0])


@pytest.mark.parametrize('failure', ['other_line', 'duplicate_word', 'duplicate_audit', 'regular', 'whole', 'number', 'off_page'])
def test_unmatched_or_already_complete_words_are_not_replaced(failure):
    case=CASES[0];pix,records=fixture(case);text=case['text']
    if failure=='other_line': records[0]['line']=1
    if failure=='duplicate_word': text+=' '+text
    if failure=='duplicate_audit': records.append(copy.deepcopy(records[0]))
    if failure=='regular': records[0]['style']='regular'
    if failure=='whole': records[0]['reading']='whole_word'
    if failure=='number':
        text=text.replace('2.','3.');records[0]['replacement']=text
    if failure=='off_page':records[0]['bbox']=[10000,10000,10010,10010]
    before=copy.deepcopy(records)
    assert b.refine_page(pix,text,records)==text and records==before


def test_figurine_ligature_requires_independent_font_detection():
    case=CASES[0];pix,records=fixture(case)
    records=[r for r in records if r.get('kind')=='coordinate']
    assert b.refine_page(pix,case['text'],records)==case['text']


@pytest.mark.parametrize('tail', ['@8', 'Qd7', 'next', '4?!'])
def test_split_square_cannot_consume_an_incompatible_fragment(tail):
    case=CASES[4];pix,records=fixture(case)
    text='27.♗ '+tail
    assert b.refine_page(pix,text,records)==text


def test_split_square_cannot_consume_an_independently_verified_word():
    case=CASES[4];pix,records=fixture(case)
    records.append(dict(line=0,replacement='@7',reading='whole_word'))
    assert b.refine_page(pix,case['text'],records)==case['text']


def test_number_audit_reserves_a_move_even_when_tokenization_joins_its_square():
    text='27.♗d7'
    tokens,_=c.tokenize([text],{1})
    record=dict(kind='coordinate',style='bold',reading='number_only',replacement='27.♗',line=0,page=1,dpi=300)
    assert not c.visual_token_evidence(tokens,[text],1,[record])  # old tree mode retains its mapping
    mapped=c.visual_token_evidence(tokens,[text],1,[record],reserve_partial_numbers=True)
    assert mapped=={0} and tokens[0][:2]==('num',(27,False))
    assert all(tokens[i][0]=='num' for i in mapped)


def test_audit_cannot_choose_between_repeated_numbers_on_same_line():
    text='27.♗ @7 or 27.Bg5'
    tokens,_=c.tokenize([text],{1})
    record=dict(kind='coordinate',style='bold',reading='number_only',replacement='27.♗',line=0,page=1,dpi=300)
    assert not c.visual_token_evidence(tokens,[text],1,[record],reserve_partial_numbers=True)


def test_number_and_dot_join_is_ocr_only_and_requires_a_move_shape():
    assert c.normalize_ocr('24 .Wc2 comment')=='24.Wc2 comment'
    assert c.normalize_ocr('24 .Explanation')=='24 .Explanation'
    assert c.normalize_text('24 .Wc2')=='24 .Wc2'


@pytest.mark.parametrize('reading', ['25.♕c2','24...♕c2','24.♕b2'])
def test_located_word_must_agree_in_number_side_and_square(monkeypatch,reading):
    with fitz.open() as doc:
        page=doc.new_page();page.insert_text((30,50),'24.Wc2')
        monkeypatch.setattr(fitz.Page,'get_textpage_ocr',lambda p,**k:p.get_textpage())
        monkeypatch.setattr(b.co,'decode',lambda *a,**k:(reading,[1.0],[dict(char='Q',bbox=[30,40,40,50],similarity=1.0)]))
        records=[]
        assert b.refine_split_dot(page,'24 .Wc2',records,'eng','unused')=='24 .Wc2'
        assert not records


@pytest.mark.parametrize('cancel',[False,True])
def test_optional_refinement_failure_keeps_export_but_cancellation_propagates(tmp_path,monkeypatch,cancel):
    source,target=tmp_path/'source.pdf',tmp_path/'out.pgn';source.write_bytes(b'fixture')
    monkeypatch.setattr(c,'read_book_pages',lambda *a:[''])
    monkeypatch.setattr(c,'ocr_book',lambda *a,**k:{0:'1. e4 e5 2. Nf3 Nc6 3. Bb5 a6 1-0'})
    def fail(*a):raise c.Stopped() if cancel else OSError('test')
    monkeypatch.setattr(c,'refine_bold_words',fail)
    if cancel:
        with pytest.raises(c.Stopped):c.book_to_pgn(source,target,dict(lang='en',ocr='always',mainline_only=True),lambda *a:None)
        assert not target.exists()
    else:
        c.book_to_pgn(source,target,dict(lang='en',ocr='always',mainline_only=True),lambda *a:None)
        r=json.loads(c.report_path_for(target).read_text(encoding='utf-8'))
        assert target.exists() and any(i['code']=='bold_source_unavailable' for i in r['issues'])


@pytest.mark.parametrize('duplicate',[False,True])
def test_split_dot_requires_one_unique_source_location(monkeypatch,duplicate):
    with fitz.open() as doc:
        page=doc.new_page();page.insert_text((30,50),'24.Wc2')
        if duplicate:page.insert_text((30,80),'24.Wc2')
        monkeypatch.setattr(fitz.Page,'get_textpage_ocr',lambda p,**k:p.get_textpage())
        def decode(samples,stride,box,*args):
            return '24.♕c2',[1.0],[dict(char='Q',bbox=[v*72/300 for v in box],similarity=1.0)]
        monkeypatch.setattr(b.co,'decode',decode)
        records=[];result=b.refine_split_dot(page,'24 .Wc2',records,'eng','unused')
        assert result==('24 .Wc2' if duplicate else '24.♕c2')
        assert bool(records)==(not duplicate)
