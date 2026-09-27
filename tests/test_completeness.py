import io
import json
from pathlib import Path

import chess.pgn
import fitz
import pytest

import coordinate_ocr as co
from benchmark import load_games,move_paths,compare
from conversion_quality import serialize_games

FIXTURES=Path(__file__).parent/'fixtures'


def test_clipped_digit_and_joined_small_font_from_book_pixels():
    for fixture in json.loads((FIXTURES/'clipped_notation.json').read_text(encoding='utf8')):
        rows=fixture['rows'];width,height=len(rows[0]),len(rows)
        raw=bytes(0 if v=='1' else 255 for row in rows for v in row)
        pix=fitz.Pixmap(fitz.csGRAY,width,height,raw,False)
        with fitz.open() as doc:
            page=doc.new_page(width=width*72/300,height=height*72/300)
            page.insert_image(page.rect,pixmap=pix)
            evidence=[]
            words=co.read_words(page,[fixture['word']],fixture['glyphs'],300,evidence)
        assert words[0][4] == fixture['expected']
        assert evidence[0]['reading'] == 'whole_word'
        if fixture['id']=='capablanca-c3':
            assert evidence[0]['expanded_clipped_edge']
            assert evidence[0]['bbox'][0] < fixture['word'][0]
        else:
            assert evidence[0]['style'] == 'regular'


def test_edge_expansion_cannot_jump_whitespace_to_adjacent_word():
    raw=bytes([0]*3+[255]*2+[0]*3+[255]*2)
    assert co.complete_ink_bounds(raw,10,10,(6,0,9,1),300) == (4,0,9,1)


def test_unbounded_ink_does_not_expand_to_a_guessed_word():
    raw=bytes([0]*80)
    assert co.complete_ink_bounds(raw,80,80,(35,0,40,1),300) == (35,0,40,1)


@pytest.mark.skipif(
    not (FIXTURES / 'euwe-capablanca-1928-checked-tree.pgn').is_file(),
    reason="Optional private book annotations are available only in the local evaluation bundle.",
)
def test_new_annotation_reference_preserves_mainline_and_legal_tree():
    full=load_games(FIXTURES/'euwe-capablanca-1928-checked-tree.pgn')[0]
    previous=load_games(FIXTURES/'euwe-capablanca-1928-checked-mainline.pgn')[0]
    assert full.board().fen() == previous.board().fen()
    assert list(full.mainline_moves()) == list(previous.mainline_moves())
    assert full.headers['Result'] == previous.headers['Result']
    assert len(move_paths(full)) == 272
    text=serialize_games([full])
    assert 'Надо же, какой конь!' in text
    assert 'рождественские каникулы' not in text
    restored=chess.pgn.read_game(io.StringIO(text))
    assert compare([restored],[full])['exact_games'] == 1
