import json
from pathlib import Path

import chess
import fitz
import pytest

import chess_converter as c
import coordinate_ocr as co


def test_heldout_pixels_read_coordinates_and_captures():
    fixtures = json.loads((Path(__file__).parent/'fixtures/coordinate_words.json').read_text(encoding='utf8'))
    profile = co.load_profile()
    for sample in fixtures:
        pixels = bytes(0 if v == '1' else 255 for row in sample['rows'] for v in row)
        result = co.decode(pixels, sample['width'], (0,0,sample['width'],sample['height']), sample['glyphs'], 300, profile)
        assert (result[0] if result else None) == sample['expected'], sample['id']


def test_identical_letter_shapes_are_rejected():
    data, templates = co.load_profile()
    ambiguous = [(('h' if ch == 'c' else ch), w, h, bitmap) for ch,w,h,bitmap in templates if ch == 'c']
    fixture = json.loads((Path(__file__).parent/'fixtures/coordinate_words.json').read_text(encoding='utf8'))[5]
    pixels = bytes(0 if v == '1' else 255 for row in fixture['rows'] for v in row)
    assert co.decode(pixels,fixture['width'],(0,0,fixture['width'],fixture['height']),fixture['glyphs'],300,
                     (data,templates+ambiguous)) is None


def test_plain_font_and_prose_are_unchanged():
    with fitz.open() as doc:
        page=doc.new_page()
        page.insert_text((25,35), '1. e4 e5 2. Nf3 Nc6 Ordinary introduction and other prose.', fontsize=11)
        words=page.get_text('words');audit=[]
        restored=co.read_words(page,words,[],300,audit)
    assert [w[4] for w in restored] == [w[4] for w in words]
    assert not audit


def test_column_coordinates_map_both_word_and_letters():
    records=[{'bbox':[10,100,30,110], 'characters':[{'bbox':[10,101,15,109]}]}]
    co.restore_coordinates(records,[(0,50,80,150,-60)])
    assert records[0]['bbox'] == [10,40,30,50]
    assert records[0]['characters'][0]['bbox'] == [10,41,15,49]
    with pytest.raises(ValueError):
        co.restore_coordinates(records,[])


def record(word,line,page=1,reading='whole_word'):
    return {'kind':'coordinate','replacement':word,'page':page,'line':line,'dpi':300,'reading':reading}


def parse(text,records):
    tokens,lines=c.tokenize([text],{1},boundaries=True)
    visual=c.visual_token_evidence(tokens,[text],1,records)
    finder=c.GameFinder('Book',['en'],True,lines,{1},'en',visual_tokens=visual)
    finder.run(tokens)
    return finder


def test_bold_mainline_is_reserved_across_prose_and_open_brackets():
    text='''№ 1.
Alice - Bob
1. e4 e5 2. Nf3 Nc6
Instead 3. Bc4 is an analysis move.
(unclosed comment
3. Bb5 a6 4. Ba4 Nf6 1-0'''
    f=parse(text,[record('3.Bb5',5)])
    board=chess.Board()
    expected=[]
    for san in 'e4 e5 Nf3 Nc6 Bb5 a6 Ba4 Nf6'.split():
        move=board.parse_san(san);expected.append(move);board.push(move)
    assert list(f.games[0].mainline_moves()) == expected
    assert 'analysis move' in str(f.games[0])
    assert {d['code'] for d in f.diagnostics} >= {'variation_anchor','visual_mainline_resumed'}
    after_nc6 = f.games[0].end().parent.parent.parent.parent
    assert any(n.san() == 'Bc4' for n in after_nc6.variations)


def test_typography_reservation_does_not_cross_game_boundary():
    text='''№ 1.
Alice - Bob
1. e4 e5 2. Nf3 Nc6 3. Bb5 a6 1-0
№ 2.
Carol - Dave
1. d4 d5 2. c4 e6 3. Nc3 Nf6 0-1'''
    f=parse(text,[record('2.c4',5)])
    assert len(f.games) == 2
    assert len(list(f.games[0].mainline_moves())) == 6
    assert not any(d['code']=='visual_mainline_reserved' for d in f.diagnostics)


def test_ambiguous_word_or_other_line_never_inherits_typography():
    text='1. e4 e5 2. Nf3\nNf3 and Nf3\n3. Bb5'
    tokens,_=c.tokenize([text],{1})
    assert not c.visual_token_evidence(tokens,[text],1,[record('Nf3',1),record('e5',2)])


def test_number_only_evidence_does_not_validate_unread_move():
    text='11...де7'
    tokens,_=c.tokenize([text],{1})
    visual=c.visual_token_evidence(tokens,[text],1,[record(text,0,reading='number_only')])
    assert visual == {0}
    assert tokens[1][0]=='word'


def test_visually_read_illegal_move_is_not_replaced_with_legal_lookalike():
    tokens=[('word','e5',1,0)]
    f=c.GameFinder('Book',['en'],True,['e5'],{1},visual_tokens={0})
    assert f.parse_token(chess.Board(),tokens,0)[0] is None
    assert f.token_candidates(chess.Board(),tokens,0)==[]
    assert f.read_token(chess.Board(),tokens,0)[0] is None


@pytest.mark.parametrize('ending',['):',').',')?!','):»'])
def test_book_reference_punctuation_does_not_swallow_close_bracket(ending):
    text=f'1. e4 e5 2. Nf3 (see №77{ending} Other comment. 2...Nc6 3. Bb5 a6 1-0'
    f=parse(text,[])
    assert len(list(f.games[0].mainline_moves()))==6
    assert c.normalize_ocr('(№77): «Я видел') == '(№77): «Я видел'
    assert c.normalize_ocr('Ке4: f6+') == 'Ке4:f6+'
    assert c.normalize_ocr('45 : е4') == '45:е4'
    assert c.normalize_ocr('Точнее: c6') == 'Точнее: c6'


def test_coordinate_cache_roundtrip_and_invalid_evidence(tmp_path, monkeypatch):
    monkeypatch.setattr(c,'find_tessdata',lambda lang:'unused')
    monkeypatch.setattr(c,'ocr_cache',lambda *args:(tmp_path,'visual'))
    def no_pool(**kwargs):
        raise PermissionError()
    monkeypatch.setattr(c,'ProcessPoolExecutor',no_pool)
    evidence={**record('e4',0),'style':'bold','reading':'whole_word','original_word':'е4',
              'changed':True,'bbox':[1,2,3,4],
              'characters':[{'char':'e','bbox':[1,2,2,4],'similarity':0.99}]}
    calls=[]
    def read(job):
        calls.append(job[2]);return job[2],'e4',[evidence]
    monkeypatch.setattr(c,'ocr_page',read)
    args=('pdf',tmp_path/'book.pdf',[0],'eng',str(tmp_path),lambda *a:None)
    first=[];second=[]
    c.ocr_book(*args,evidence=first)
    c.ocr_book(*args,evidence=second)
    assert first==second and calls==[0]
    cache=tmp_path/'visual-p1.json'
    damaged=json.loads(cache.read_text());damaged['glyphs'][0]['line']='bad'
    cache.write_text(json.dumps(damaged))
    c.ocr_book(*args)
    assert calls==[0,0]


def test_coordinate_switch_and_profile_change_invalidate_cache(tmp_path,monkeypatch):
    source=tmp_path/'source.pdf';source.write_bytes(b'example')
    (tmp_path/'eng.traineddata').write_bytes(b'model')
    monkeypatch.setattr(c,'find_tessdata',lambda lang:str(tmp_path))
    monkeypatch.setenv('CHESS_CONVERTER_CACHE',str(tmp_path/'cache'))
    old=c.ocr_cache(source,'eng')
    assert c.ocr_cache(source,'eng',coordinates='off')!=old
    monkeypatch.setattr(co,'profile_fingerprint',lambda:'new-profile')
    assert c.ocr_cache(source,'eng')!=old
