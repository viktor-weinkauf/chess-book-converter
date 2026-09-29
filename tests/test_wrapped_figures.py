import base64
import copy
import json
from pathlib import Path
import zlib

import fitz
import pytest

import bold_ocr as b

CASE = json.loads((Path(__file__).parent/'fixtures/wrapped_figure.json').read_text(encoding='utf-8'))


@pytest.mark.parametrize('failure', [None, 'number', 'false_number_ocr', 'side', 'piece', 'prose_between', 'duplicate',
                                     'no_anchor', 'different_column', 'distant_line', 'blank_pixels'])
def test_wrapped_move_requires_both_pixel_readings_and_adjacent_source_lines(monkeypatch, failure):
    records, words = copy.deepcopy(CASE['records']), copy.deepcopy(CASE['words'])
    text = CASE['text']
    if failure == 'number': text = text.replace('31.', '32.')
    if failure == 'false_number_ocr':
        text = text.replace('31.', '32.')
        words[0][4] = '32.'  # the actual pixels still say 31.
    if failure == 'side': text = text.replace('31.', '31...')
    if failure == 'piece': text = text.replace('♗', '♕')
    if failure == 'prose_between': text = text.replace('\n', '\nprose\n')
    if failure == 'duplicate': text += '\n' + text
    if failure == 'no_anchor': records = []
    if failure == 'different_column': words[1][0] = words[0][0] + 2
    if failure == 'distant_line': words[1][1] += 30
    before = copy.deepcopy(records)
    samples = zlib.decompress(base64.b64decode(CASE['samples']))
    if failure == 'blank_pixels': samples = b'\xff' * len(samples)
    pix = fitz.Pixmap(fitz.csGRAY, CASE['width'], CASE['height'], samples, False)
    with fitz.open() as doc:
        page = doc.new_page(width=pix.width*72/300, height=pix.height*72/300)
        page.insert_image(page.rect, pixmap=pix)
        monkeypatch.setattr(fitz.Page, 'get_textpage_ocr', lambda *a, **k: None)
        monkeypatch.setattr(fitz.Page, 'get_text', lambda *a, **k: words)
        result = b.refine_wrapped_figures(page, text, records, 'eng', 'unused')
    if failure:
        assert result == text and records == before
    else:
        assert result == CASE['expected']
        assert records[-1]['joined_line_wrap'] and b.co.valid_evidence(records[-1])
        assert records[-1]['source_lines'] == [0, 1]
        assert ''.join(c['char'] for c in records[-1]['characters']) == '31.♗d3.'
