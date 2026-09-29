import base64
import copy
import json
from pathlib import Path
import zlib

import fitz
import pytest
import bold_ocr as b

CASE = json.loads((Path(__file__).parent / 'fixtures/bold_three_word.json').read_text(encoding='utf-8'))


@pytest.mark.parametrize('damage', [None, 'blank', 'missing_digit', 'duplicate_word', 'wrong_number'])
def test_independent_three_sample_recovers_whole_source_word(damage):
    samples = bytearray(zlib.decompress(base64.b64decode(CASE['samples'])))
    if damage == 'blank':
        samples[:] = b'\xff' * len(samples)
    if damage == 'missing_digit':
        for y in range(CASE['height']):
            for x in range(307-142,333-142):
                samples[y*CASE['width']+x] = 255
    text = CASE['text']
    if damage == 'duplicate_word': text += ' ' + text
    if damage == 'wrong_number': text = text.replace('19.', '18.')
    records = copy.deepcopy(CASE['records'])
    before = copy.deepcopy(records)
    pix = fitz.Pixmap(fitz.csGRAY, CASE['width'], CASE['height'], bytes(samples), False)
    result = b.refine_page(pix, text, records)
    if damage:
        assert result == text and records == before
    else:
        assert result == CASE['expected']
        record = records[0]
        assert b.co.valid_evidence(record) and record['source_refined']
        assert ''.join(c['char'] for c in record['characters']) == CASE['expected']
        assert min(c['similarity'] for c in record['characters']) >= .91
        templates = json.loads(b.PROFILE.read_text(encoding='utf-8'))['templates']
        assert all(t['source']['file_page'] != CASE['source']['file_page'] for t in templates if t['char'] == '3')
