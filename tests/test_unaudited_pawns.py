import base64
import copy
import json
from pathlib import Path
import zlib

import fitz
import pytest

import bold_ocr as b

CASE = json.loads((Path(__file__).parent / 'fixtures/unaudited_pawn.json').read_text(encoding='utf-8'))


@pytest.mark.parametrize('failure', [None, 'duplicate_text', 'already_audited',
                                     'wrong_number', 'wrong_side', 'known_file', 'known_rank',
                                     'punctuation', 'blank_pixels', 'duplicate_location'])
def test_short_pawn_requires_complete_unambiguous_source_word(monkeypatch, failure):
    text = CASE['text']
    records = []
    if failure == 'duplicate_text': text += '\n' + text
    if failure == 'already_audited': records.append(dict(kind='coordinate', line=0, replacement=text))
    if failure == 'wrong_number': text = text.replace('12', '13')
    if failure == 'wrong_side': text = text.replace('...', '.')
    if failure == 'known_file': text = text.replace('п', 'a')
    if failure == 'known_rank': text = text.replace('б', '5')
    if failure == 'punctuation': text += '!'
    before = copy.deepcopy(records)
    samples = zlib.decompress(base64.b64decode(CASE['samples']))
    width, height = CASE['width'], CASE['height']
    words = copy.deepcopy(CASE['words'])
    if failure == 'blank_pixels': samples = b'\xff' * len(samples)
    if failure == 'duplicate_location':
        samples = b''.join(samples[y*width:(y+1)*width]*2 for y in range(height))
        words += [[w[0]+width*72/300,w[1],w[2]+width*72/300,w[3],*w[4:]] for w in words]
        width *= 2
    pix = fitz.Pixmap(fitz.csGRAY, width, height, samples, False)
    with fitz.open() as doc:
        page = doc.new_page(width=width*72/300, height=height*72/300)
        page.insert_image(page.rect, pixmap=pix)
        monkeypatch.setattr(fitz.Page, 'get_textpage_ocr', lambda *a, **k: None)
        monkeypatch.setattr(fitz.Page, 'get_text', lambda *a, **k: words)
        result = b.refine_unaudited_pawns(page, text, records, 'eng', 'unused')
    if failure:
        assert result == text and records == before
    else:
        assert result == CASE['expected']
        assert len(records) == 1 and records[0]['located_pawn']
        assert b.co.valid_evidence(records[0])
        assert records[0]['primary_reading'] == text


@pytest.mark.parametrize('text', ['12...h6', '12...с6', '112...h6', 'prose h6'])
def test_valid_or_unnumbered_text_does_not_request_pawn_ocr(text):
    assert not b.unaudited_pawns(text, [])


def test_pawn_only_ocr_page_needs_no_existing_figurine_evidence(tmp_path, monkeypatch):
    import chess_converter as c
    source = tmp_path / 'pawn.pdf'
    with fitz.open() as doc:
        doc.new_page(width=100, height=100)
        doc.save(source)
    seen = []
    def refine(page, text, records, *args):
        seen.append(text)
        return '12...h6'
    monkeypatch.setattr(b, 'refine_unaudited_pawns', refine)
    monkeypatch.setattr(c, 'find_tessdata', lambda *a: 'unused')
    pages = [CASE['text'], CASE['text']]
    c.refine_bold_words('pdf', source, tmp_path, pages, [], lambda *a: None, 'eng', {1})
    assert seen == [CASE['text']]
    assert pages == ['12...h6', CASE['text']]
