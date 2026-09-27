import pytest

import figurine_ocr as f


def test_second_ocr_consumes_only_its_split_square(monkeypatch):
    words=[(0,0,22,12,'24.69'),(23,0,34,12,'c4'),(40,0,60,12,'Re6')]
    glyphs=[dict(bbox=[13,1,22,10],symbol='♘')]
    monkeypatch.setattr(f,'read_with_letters',lambda *args:[(0,0,34,12,'24.Nc4'),(40,0,60,12,'Re6')])
    fixed=f.repair_words(None,words,glyphs,'eng','unused',300)
    assert [w[4] for w in fixed] == ['24.♘c4','Re6']
    assert fixed[0][:4] == [0,0,34,12]
    assert glyphs[0]['consumed_words'] == [dict(word='c4',bbox=[23,0,34,12])]
    assert words[1][4]=='c4'


@pytest.mark.parametrize('fragment,box',[
    ('d4',(23,0,34,12)),  # conflicting square
    ('c4',(30,0,48,12)),  # insufficient overlap
    ('c4',(23,20,34,32)), # another line is not consumed
    ('25.c4',(23,0,34,12)), # next move number
    ('Nc4',(23,0,34,12)), # separate piece move
    ('comment',(23,0,34,12)),
])
def test_second_reading_cannot_swallow_other_text(monkeypatch,fragment,box):
    words=[(0,0,22,12,'24.69'),(*box,fragment)]
    glyphs=[dict(bbox=[13,1,22,10],symbol='♘')]
    monkeypatch.setattr(f,'read_with_letters',lambda *args:[(0,0,34,12,'24.Nc4')])
    fixed=f.repair_words(None,words,glyphs,'eng','unused',300)
    assert len(fixed)==2 and fixed[1][4]==fragment
    if box[1] == 0:
        assert not glyphs[0].get('used_alternate')
        assert glyphs[0]['alternate_rejected']=='ambiguous_word_span'


def test_square_with_another_detected_figure_is_not_consumed():
    words=[(0,0,22,12,'24.69'),(23,0,34,12,'c4')]
    glyphs=[dict(bbox=[13,1,22,10],symbol='♘'),dict(bbox=[23,1,28,10],symbol='♗')]
    assert f.alternate_word_span(words,0,(0,0,34,12),'24.♘c4',glyphs,glyphs[0]) is None
