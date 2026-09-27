import json
from pathlib import Path
from types import SimpleNamespace

import chess
import pytest

import chess_converter as c
import coordinate_ocr as co
import figurine_ocr as fo


def test_joint_segmentation_recovers_missing_figures_without_board_context():
    fixtures=json.loads((Path(__file__).parent/'fixtures/joined_notation.json').read_text(encoding='utf8'))
    for row in fixtures:
        pixels=bytes(0 if v=='1' else 255 for line in row['rows'] for v in line)
        box=(0,0,row['width'],row['height'])
        result=co.decode(pixels,row['width'],box,[],300,co.load_profile(True))
        assert result and result[0]==row['expected']
        assert co.decode(pixels,row['width'],box,[],300,co.load_profile(False)) is None


@pytest.mark.parametrize('symbol,piece',[('♔',chess.KING),('♕',chess.QUEEN),('♖',chess.ROOK),('♗',chess.BISHOP),('♘',chess.KNIGHT)])
@pytest.mark.parametrize('table',[False,True])
def test_deep_repair_never_changes_a_visually_identified_piece(monkeypatch,symbol,piece,table):
    board=chess.Board()
    finder=c.GameFinder('Book',['en'],True,['damaged'],{1},table={0} if table else False)
    monkeypatch.setattr(finder,'last_row_move',lambda *args:False)
    tested=[]
    def score(after,*args,**kwargs):
        move=after.peek()
        tested.append(board.piece_type_at(move.from_square))
        # Tempt the old search to pick a pawn because its continuation looks best.
        return (5,0) if board.piece_type_at(move.from_square)==chess.PAWN else (0,0)
    monkeypatch.setattr(finder,'fitting_moves',score)
    result=finder.repair_move(SimpleNamespace(board=board),[('word',symbol+'f3',1,0)],0)
    assert result is None
    assert all(observed==piece for observed in tested)


@pytest.mark.parametrize('before,after,conflict',[
    ('♘:dS','♘a5','lost_capture'),('♘dS','♘a5','changed_file'),
    ('♘93','♘f6','changed_rank'),('♘93','♘f3',None),
    ('11.♘:dS?!','11.♘:d5?!',None),('♗аZ','♗a2',None),
])
def test_alternate_must_preserve_readable_partial_square(before,after,conflict):
    assert fo.alternate_conflict(before,after)==conflict


def test_conflicting_second_reading_retains_primary_and_audit(monkeypatch):
    words=[(0,0,35,10,'H:dS')]
    glyphs=[{'bbox':[0,0,8,10],'symbol':'♘'}]
    monkeypatch.setattr(fo,'read_with_letters',lambda *args:[(0,0,35,10,'Na5')])
    result=fo.repair_words(None,words,glyphs,'eng','unused',300)
    assert result[0][4]=='♘:dS'
    assert glyphs[0]['alternate_reading']=='♘a5'
    assert glyphs[0]['alternate_rejected']=='lost_capture'
    assert glyphs[0]['conflicting_readings']
    assert not glyphs[0].get('used_alternate')
