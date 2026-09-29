import base64
import copy
import json
from pathlib import Path
import zlib

import fitz
import pytest
import chess_converter as c
import page_layout as layout

CASE = json.loads((Path(__file__).parent / 'fixtures/double_height_words.json').read_text(encoding='utf-8'))


def repair(words, *, blank=False):
    samples = zlib.decompress(base64.b64decode(CASE['samples']))
    if blank: samples = b'\xff' * len(samples)
    pix = fitz.Pixmap(fitz.csGRAY, CASE['width'], CASE['height'], samples, False)
    with fitz.open() as doc:
        page = doc.new_page(width=pix.width*72/300, height=pix.height*72/300)
        page.insert_image(page.rect, pixmap=pix)
        records = []
        result = layout.repair_word_boxes(page, words, 300, records)
    return result, records


def test_original_pixels_restore_three_lines_without_sorting_move_numbers():
    words = copy.deepcopy(CASE['words'])
    before = copy.deepcopy(words)
    fixed, records = repair(words)
    assert words == before
    assert [tuple(w[4:]) for w in fixed] == [tuple(w[4:]) for w in words]
    assert [[w[4] for w in row] for row in c.visual_lines(fixed)][:3] == CASE['expected_rows']
    assert len(records) == 7 and all(layout.valid_geometry(r) for r in records)
    # The unmodified OCR puts the black response before White's 22nd move.
    assert [[w[4] for w in row] for row in c.visual_lines(words)][:3] != CASE['expected_rows']
    again, audit = repair(fixed)
    assert again == fixed and not audit


@pytest.mark.parametrize('failure', ['blank', 'no_peers', 'wrong_line', 'far_neighbours'])
def test_unsupported_line_alignment_does_not_move_target(failure):
    words = copy.deepcopy(CASE['words'])
    target = next(i for i,w in enumerate(words) if w[4] == '22.815++')
    if failure == 'no_peers':
        for i,w in enumerate(words): w[5] = i
    if failure == 'wrong_line': words[target][6] = 1000
    if failure == 'far_neighbours':
        for w in words:
            if w[6] == 0 and w[4] != '22.815++': w[1] += 50; w[3] += 50
    fixed, _ = repair(words, blank=failure == 'blank')
    assert fixed[target] == words[target]


def test_text_is_not_used_to_infer_move_order():
    words = copy.deepcopy(CASE['words'])
    for i, w in enumerate(words): w[4] = f'word-{i}'
    fixed, records = repair(words)
    assert len(records) == 7
    assert [w[4] for w in fixed] == [w[4] for w in words]
    original, _ = repair(CASE['words'])
    assert [w[:4] for w in fixed] == [w[:4] for w in original]


@pytest.mark.parametrize('bad', [None, {}, {'kind':'word_geometry'},
    {'kind':'word_geometry','method':'peer-lines-and-ink-v1','word':'x','bbox':[0,0,1,2],'original_bbox':[0,float('nan'),1,2]}])
def test_invalid_geometry_cache_record_is_rejected(bad):
    assert not layout.valid_geometry(bad)


def test_corrected_word_geometry_survives_ocr_cache(monkeypatch, tmp_path):
    _, records = repair(CASE['words'])
    (tmp_path / 'key-p1.json').write_text(json.dumps({'text':'cached text','glyphs':records}), encoding='utf-8')
    monkeypatch.setattr(c, 'find_tessdata', lambda lang: 'unused')
    monkeypatch.setattr(c, 'ocr_cache', lambda *a, **k: (tmp_path, 'key'))
    def unexpected(*args, **kwargs):
        raise AssertionError('A valid geometry record must not invalidate cached OCR')
    monkeypatch.setattr(c, 'ProcessPoolExecutor', unexpected)
    evidence = []
    result = c.ocr_book('djvu', tmp_path/'book.djvu', [0], 'eng', str(tmp_path),
                        lambda *args: None, evidence=evidence)
    assert result == {0:'cached text'}
    assert len(evidence) == len(records)
    assert all(r['page'] == 1 and r['dpi'] == c.OCR_DPI for r in evidence)
