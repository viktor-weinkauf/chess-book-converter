import copy
import json
from pathlib import Path

import chess
import pytest

import chess_converter as c
import diagram_ocr as d
import diagram_refinement as r

FIXTURES = Path(__file__).parent/'fixtures'
PLACEMENT = '5nk1/1p2qrr1/p1p1p1p1/P1PpP3/1P2pPP1/4P1B1/6R1/3Q1RK1'


def analyze(placement=PLACEMENT, first='Kh8', black=True):
    return d.starting_position_analysis(placement,36,black,first,c.parse_move,['en'])


def test_complete_board_keeps_both_possible_en_passant_histories():
    result = analyze()
    assert result['status'] == 'unresolved_history'
    assert result['unknown_fields'] == ['en_passant']
    assert {r['fen'].split()[3] for r in result['candidates']} == {'-', 'f3'}
    assert {r['orientation'] for r in result['candidates']} == {'white_bottom'}
    for candidate in result['candidates']:
        board = chess.Board(candidate['fen'])
        assert board.is_valid() and board.parse_san('Kh8') in board.legal_moves
    assert d.starting_positions(PLACEMENT,36,True,'Kh8',c.parse_move,['en']) == []


def test_explicit_en_passant_first_move_resolves_the_target():
    result = analyze('7k/8/8/4Pp2/8/8/8/K7','exf6',False)
    assert result['status'] == 'resolved'
    assert result['candidates'][0]['fen'].split()[3] == 'f6'
    board = chess.Board(result['candidates'][0]['fen'])
    assert board.is_en_passant(board.parse_san('exf6'))


@pytest.mark.parametrize('fen', [
    '7k/8/8/4P3/8/8/8/K7 w - - 0 26',  # no double-pushed opponent pawn
    '7k/8/8/5p2/8/8/8/K7 w - - 0 26',  # no possible capturer
    '7k/5n2/8/4Pp2/8/8/8/K7 w - - 0 26',  # occupied pawn origin
    '7k/8/5n2/4Pp2/8/8/8/K7 w - - 0 26',  # occupied target
    '4r2k/8/8/4Pp2/8/8/8/4K3 w - - 0 26',  # pinned pawn cannot capture
])
def test_irrelevant_or_impossible_en_passant_does_not_block(fen):
    assert d.possible_en_passant(chess.Board(fen)) == [None]


def test_pawn_on_fifth_rank_without_capture_no_longer_blocks_setup():
    result = analyze('7k/8/8/4P3/8/8/8/K7','e6',False)
    assert result['status'] == 'resolved'


def test_home_square_king_without_rooks_has_no_castling_uncertainty():
    result = analyze('6k1/8/8/8/8/8/8/4K3','Kf2',False)
    assert result['status'] == 'resolved'
    assert result['candidates'][0]['fen'].split()[2] == '-'


def test_home_square_king_and_rook_do_not_prove_castling_rights():
    result = analyze('6k1/8/8/8/8/8/8/4K2R','Kf2',False)
    assert result['status'] == 'unresolved_history'
    assert result['unknown_fields'] == ['castling_rights']
    assert {r['fen'].split()[2] for r in result['candidates']} == {'-', 'K'}


def test_printed_castling_can_supply_the_required_right():
    result = analyze('6k1/8/8/8/8/8/8/4K2R','O-O',False)
    assert result['status'] == 'resolved'
    assert result['candidates'][0]['fen'].split()[2] == 'K'


def test_wrong_first_move_cannot_repair_a_fully_read_position():
    assert analyze(first='Kd5')['status'] == 'incompatible_notation_or_position'


def test_ambiguous_orientation_is_reported_separately():
    result = analyze('7k/8/8/8/8/8/3K4/8','Ke4',False)
    # Both d2 and e7 (after rotation) cannot play Ke4.
    assert result['status'] == 'incompatible_notation_or_position'
    placement = '7k/8/8/8/3K4/8/8/8'
    result = analyze(placement,'Kd5',False)
    assert result['status'] == 'ambiguous_orientation'
    assert len(result['candidates']) == 2


def test_all_cells_read_but_unknown_history_never_exports_guessed_setup():
    fixture = json.loads((FIXTURES/'diagram_followup.json').read_text())[0]
    record = copy.deepcopy(fixture['record'])
    for cell, labelled in zip(record['cells'],fixture['cells']):cell['piece'] = labelled['expected']
    record.update(placement=PLACEMENT,status='recognized')
    page = ('№ 152\nAlekhine - Euwe\n__BOARD_0__\n'
            '36...Kh8 37.Bh4 Qxh4 38.Rh2 Rh7 39.Rxh4 Rxh4 1/2-1/2')
    tokens,lines = c.tokenize([page],start=19,boundaries=True)
    finder = c.GameFinder('Book',['en'],True,lines,diagram_evidence=[record])
    assert finder.run(tokens) == []
    assert record['status'] == 'unresolved_history'
    assert d.valid_evidence(record)
    issue = next(i for i in finder.diagnostics if i['code']=='diagram_history_unresolved')
    assert issue['unknown_fields'] == ['en_passant']


@pytest.mark.parametrize('filename,page,total',[
    ('diagram_followup.json',19,64), ('diagram_followup.json',20,61),
    ('diagram_new_pages.json',27,49), ('diagram_new_pages.json',28,58),
])
def test_diagram_cells_against_separate_visual_labels(filename,page,total):
    fixture = next(f for f in json.loads((FIXTURES/filename).read_text()) if f['page']==page)
    profile = r.load_profile(d.profile_fingerprint(),r.profile_fingerprint())
    accepted=[]
    for cell,original in zip(fixture['cells'],fixture['record']['cells']):
        found = original['piece']
        if found is None:found = d.classify(cell['values'],cell['dark'],profile)[0]
        assert found in (None,cell['expected']), cell['square']
        accepted.append(found)
    assert sum(p is not None for p in accepted) == total


def test_new_templates_have_no_positions_or_game_moves_and_keep_thresholds():
    profile = r.load_profile(d.profile_fingerprint(),r.profile_fingerprint())
    base = d.load_profile(d.profile_fingerprint())
    assert profile['maximum_distance'] == base['maximum_distance'] == .04
    assert profile['minimum_margin'] == base['minimum_margin'] == .005
    extra = json.loads(r.PROFILE.read_text())
    assert len(extra['templates']) == 8
    for template in extra['templates']:
        assert set(template) == {'piece','dark','values','source'}
        assert len(template['values']) == 16*16
