"""Recovery must read source evidence, never replace an unread move by analysis."""
import chess_converter as c
import diagram_ocr as d
import chess


def test_unread_bold_move_cannot_borrow_matching_number_from_analysis():
    text = ('№ 1.\nAlice - Bob\n1. e4 c5 2. unread\n'
            'Earlier players preferred 2. f4 in this position.\n2... Nc6 3. d4 cxd4 0-1')
    tokens, lines = c.tokenize([text], boundaries=True)
    visual = {i for i,t in enumerate(tokens) if t[0] == 'num' and t[3] in (2,4)}
    f = c.GameFinder('Book',['en'],True,lines,visual_tokens=visual,mainline_only=True)
    f.run(tokens)
    assert not f.problems
    g = f.games[0]
    assert [n.san() for n in g.mainline()] == ['e4','c5']
    assert g.headers['MissingMove'] == '2.'
    assert 'f4' in g.end().comment


def test_equivalent_en_passant_histories_keep_uncertainty_explicit():
    placement = '7k/8/8/8/4pP2/8/8/K7'
    assessment = d.starting_position_analysis(placement,36,True,'Kh7',c.parse_move,['en'])
    assert assessment['unknown_fields'] == ['en_passant']
    canonical = d.continuation_equivalent_setup(assessment,'Kh7',c.parse_move,['en'])
    assert canonical['fen'].split()[3] == '-'
    assert assessment['status'] == 'unresolved_history'
    text = '№ 1\nAlice - Bob\n__BOARD_0__\n36...Kh7 37.Kb2 1/2-1/2'
    tokens,lines = c.tokenize([text], boundaries=True)
    f = c.GameFinder('Book',['en'],True,lines,mainline_only=True,
                     diagram_evidence=[{'page':1,'marker':'__BOARD_0__','placement':placement}])
    f.run(tokens)
    assert not f.problems
    g = f.games[0]
    assert [n.san() for n in g.mainline()] == ['Kh7','Kb2']
    assert g.headers['FENEnPassant'].startswith('unknown;')
    assert any(x['code']=='diagram_history_normalized' for x in f.diagnostics)


def test_castling_history_is_not_normalized_by_en_passant_rule():
    assessment = d.starting_position_analysis('6k1/8/8/8/8/8/8/4K2R',1,False,'Kf2',c.parse_move,['en'])
    assert d.continuation_equivalent_setup(assessment,'Kf2',c.parse_move,['en']) is None


def test_en_passant_capture_cannot_erase_history_it_depends_on():
    assessment = d.starting_position_analysis('7k/8/8/8/4pP2/8/8/K7',36,True,'Kh7',c.parse_move,['en'])
    assert d.continuation_equivalent_setup(assessment,'exf3',c.parse_move,['en']) is None


def test_printed_mainline_can_resume_after_unread_anchor_and_analysis():
    text = ('№ 1.\nAlice - Bob\n1. e4 c5 2. unread\n'
            'Earlier players preferred 2. f4 in this position.\n'
            '2. Nf3 Nc6 3. d4 cxd4 0-1')
    tokens, lines = c.tokenize([text], boundaries=True)
    visual = {i for i,t in enumerate(tokens) if t[0] == 'num' and t[3] in (2,4)}
    f = c.GameFinder('Book',['en'],True,lines,visual_tokens=visual,mainline_only=True)
    f.run(tokens)
    assert not f.problems
    g = f.games[0]
    assert [n.san() for n in g.mainline()] == ['e4','c5','Nf3','Nc6','d4','cxd4']
    assert g.headers['Result'] == '0-1'
    assert 'MissingMove' not in g.headers
