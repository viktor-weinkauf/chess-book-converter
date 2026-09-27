import json
from pathlib import Path

import chess
import fitz
import pytest

import chess_converter as c
import coordinate_ocr as co


FIXTURES=Path(__file__).parent/'fixtures'


@pytest.mark.parametrize('fixture',json.loads((FIXTURES/'split_notation.json').read_text(encoding='utf8')),
                         ids=lambda row:row['id'])
def test_difficult_notation_from_real_development_pixels(fixture):
    rows=fixture['rows'];width,height=len(rows[0]),len(rows)
    pixels=bytes(0 if value=='1' else 255 for row in rows for value in row)
    pix=fitz.Pixmap(fitz.csGRAY,width,height,pixels,False)
    with fitz.open() as doc:
        page=doc.new_page(width=width*72/300,height=height*72/300)
        page.insert_image(page.rect,pixmap=pix)
        evidence=[];words=co.read_words(page,fixture['words'],fixture['glyphs'],300,evidence)
    assert [w[4] for w in words] == [fixture['expected']]
    if fixture['expected']=='151':
        assert not evidence
    else:
        assert evidence[0]['reading']=='whole_word'
    if len(fixture['words'])==2:
        assert evidence[0]['joined_number_fragment']
    if fixture['id']=='page18-words-234':
        assert evidence[0]['isolated_text_band']


def test_fragment_merge_abstains_without_visible_dot():
    fixture=next(r for r in json.loads((FIXTURES/'split_notation.json').read_text(encoding='utf8'))
                 if len(r['words'])==2)
    rows=fixture['rows'];width,height=len(rows[0]),len(rows)
    pixels=bytearray(0 if v=='1' else 255 for row in rows for v in row)
    # The source dot lies between the number's box and the recognized rook.
    left=round(fixture['words'][0][2]*300/72)
    right=round(fixture['glyphs'][0]['bbox'][0]*300/72)
    for y in range(height):
        for x in range(left,right):pixels[y*width+x]=255
    pix=fitz.Pixmap(fitz.csGRAY,width,height,bytes(pixels),False)
    with fitz.open() as doc:
        page=doc.new_page(width=width*72/300,height=height*72/300);page.insert_image(page.rect,pixmap=pix)
        evidence=[];words=co.read_words(page,fixture['words'],fixture['glyphs'],300,evidence)
    assert len(words)==2
    assert not any(e['joined_number_fragment'] for e in evidence)


def test_multiple_substantial_text_bands_are_preserved():
    rows=[b'\xff'*24]*2+[b'\0'*24]*20+[b'\xff'*24]*4+[b'\0'*24]*20
    rect=(0,0,24,len(rows))
    assert co.isolate_text_band(b''.join(rows),24,rect,300)==rect


def test_ligatures_are_disabled_without_a_detected_figurine_family():
    _,plain=co.load_profile(False,'regular')
    _,figures=co.load_profile(True,'regular')
    assert any(len(ch)>1 and ch.startswith('♔') for ch,*_ in figures)
    assert not any(any(p in ch for p in '♔♕♖♗♘') for ch,*_ in plain)


def test_alternative_glyph_boundary_must_keep_the_detected_piece():
    sample=next(r for r in json.loads((FIXTURES/'split_notation.json').read_text(encoding='utf8'))
                if r['expected']=='♘e4')
    rows=sample['rows'];width,height=len(rows[0]),len(rows)
    pixels=bytes(0 if value=='1' else 255 for row in rows for value in row)
    profile=co.load_profile(True,'regular')
    reading=co.decode(pixels,width,(0,0,width,height),[],300,profile)
    assert reading[0]=='♘e4'
    box=list(reading[2][0]['bbox']);box[2]=box[0]+34*72/300
    conflicting=[dict(symbol='♗',bbox=box,similarity=.99)]
    guarded=co.decode(pixels,width,(0,0,width,height),conflicting,300,profile)
    assert guarded is None or guarded[0].startswith('♗')


@pytest.mark.parametrize('ocr',[False,True])
def test_glued_evaluation_preserves_move_annotation_and_closing_branch(ocr):
    text='1. e4 e5 (1...c5!?+-) 2. Nf3 Nc6 3. Bb5 a6-+ 1-0'
    tokens,lines=c.tokenize([text],{1} if ocr else set())
    finder=c.GameFinder('Book',['en'],True,lines,{1} if ocr else set())
    finder.run(tokens);game=finder.games[0]
    assert [n.san() for n in game.mainline()]==['e4','e5','Nf3','Nc6','Bb5','a6']
    branch=game.variations[0].variations[1]
    assert branch.san()=='c5' and branch.nags=={5,18}
    assert game.end().nags=={19}


def test_single_check_and_pure_prose_are_not_split_as_evaluations():
    for word in ['Qe1+','Qe1#','2024+-','comment+-','+-.','27...']:
        assert c.split_move_evaluation(word)==[word]
    assert c.split_move_evaluation('30.♖:с2+-.')==['30.♖:с2','+-.']
    assert c.split_move_evaluation('25.♖gd7!+—)')==['25.♖gd7!','+-)']


@pytest.mark.parametrize('suffix',['','+-','-+;',')'])
def test_evaluations_do_not_bypass_pawn_capture_file_checks(suffix):
    assert co.valid_notation('bc'+suffix)
    assert not co.valid_notation('ac'+suffix)
    assert co.valid_notation('bc♕'+suffix)
    assert not co.valid_notation('ac♕'+suffix)
