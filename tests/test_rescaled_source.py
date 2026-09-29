import base64,copy,json,zlib
from pathlib import Path
import fitz
import pytest
import bold_ocr as b

CASE=json.loads((Path(__file__).parent/'fixtures/rescaled_source_word.json').read_text(encoding='utf-8'))

def fixture(blank=False):
    samples=zlib.decompress(base64.b64decode(CASE['samples']))
    if blank:samples=b'\xff'*len(samples)
    return fitz.Pixmap(fitz.csGRAY,CASE['width'],CASE['height'],samples,False),copy.deepcopy(CASE['records'])

def test_original_source_word_requires_two_complete_rescaled_readings():
    pix,records=fixture()
    assert b.refine_page(pix,CASE['text'],records)==CASE['expected']
    audit=records[0]['rescaled_readings']
    assert [r['dpi'] for r in audit]==[360,450]
    assert all(r['replacement']==CASE['expected'] for r in audit)
    assert all(min(c['similarity'] for c in r['characters'])>=.91 for r in audit)
    assert b.co.valid_evidence(records[0])
    assert all(30<c['bbox'][0]<80 and 380<c['bbox'][1]<398 for r in audit for c in r['characters'])

@pytest.mark.parametrize('failure',['blank','missing_rank','wrong_number','duplicate'])
def test_incomplete_or_ambiguous_source_stays_unchanged(failure):
    pix,records=fixture(blank=failure=='blank');text=CASE['text']
    if failure=='missing_rank':
        samples=bytearray(pix.samples)
        for y in range(1597,1651):samples[y*pix.stride+277:y*pix.stride+308]=b'\xff'*31
        pix=fitz.Pixmap(fitz.csGRAY,pix.width,pix.height,bytes(samples),False)
    if failure=='wrong_number':
        text=text.replace('8...','9...')
        records[0]['replacement']=text
    if failure=='duplicate':text+=' '+text
    before=copy.deepcopy(records)
    assert b.refine_page(pix,text,records)==text and records==before

@pytest.mark.parametrize('second',[None,'8...♘h6.'])
def test_one_success_or_disagreement_is_not_consensus(monkeypatch,second):
    pix,_=fixture();answers=iter([('8...♘h5.',[.99],[]),None if second is None else (second,[.99],[])])
    monkeypatch.setattr(b.co,'decode',lambda *a,**k:next(answers))
    assert b.decode_rescaled(pix,(140,1597,328,1651),[],300,b.load_profile(True))==(None,[])
