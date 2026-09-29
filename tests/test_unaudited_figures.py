import base64
import copy
import json
from pathlib import Path
import zlib

import fitz
import pytest

import bold_ocr as b

CASE = json.loads((Path(__file__).parent / 'fixtures/unaudited_figure.json').read_text(encoding='utf-8'))


@pytest.mark.parametrize('failure', [None, 'no_anchor', 'duplicate_anchor', 'duplicate_text',
                                     'already_audited', 'wrong_number', 'wrong_piece', 'wrong_location', 'blank_pixels'])
def test_complete_source_word_is_required_at_the_independent_figurine(monkeypatch, failure):
    records = copy.deepcopy(CASE['records'])
    text = CASE['text']
    if failure == 'no_anchor': records = []
    if failure == 'duplicate_anchor': records *= 2
    if failure == 'duplicate_text': text += '\n' + text
    if failure == 'already_audited': records.append(dict(kind='coordinate', line=0, replacement=text))
    if failure == 'wrong_number': text = text.replace('3.', '4.'); records[0]['replacement'] = '4.♗'
    if failure == 'wrong_piece': text = text.replace('♗', '♕')
    if failure == 'wrong_location': records[0]['bbox'] = [1000, 1000, 1008, 1008]
    before = copy.deepcopy(records)
    samples = zlib.decompress(base64.b64decode(CASE['samples']))
    if failure == 'blank_pixels': samples = b'\xff' * len(samples)
    pix = fitz.Pixmap(fitz.csGRAY, CASE['width'], CASE['height'], samples, False)
    with fitz.open() as doc:
        page = doc.new_page(width=pix.width*72/300, height=pix.height*72/300)
        page.insert_image(page.rect, pixmap=pix)
        monkeypatch.setattr(fitz.Page, 'get_textpage_ocr', lambda *a, **k: None)
        monkeypatch.setattr(fitz.Page, 'get_text', lambda *a, **k: CASE['words'])
        result = b.refine_unaudited_figures(page, text, records, 'eng', 'unused')
    if failure:
        assert result == text and records == before
    else:
        assert result == CASE['expected']
        assert len(records) == 2 and records[0] == before[0]
        assert records[1]['located_figurine'] and b.co.valid_evidence(records[1])
        assert records[1]['primary_reading'] == text


def test_number_prefix_is_not_matched_inside_a_longer_number():
    records = copy.deepcopy(CASE['records'])
    records[0]['replacement'] = '33.♗'
    assert not b.unaudited_figures(CASE['text'], records)


def test_already_read_notation_does_not_trigger_another_page_ocr():
    assert not b.unaudited_figures('3.♗с4', CASE['records'])
