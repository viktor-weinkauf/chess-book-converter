"""Uncertain mainline notation must not borrow legal moves from commentary."""
import pytest
import chess_converter as c
import json
from pathlib import Path


def extract(tail, *, audited=True, bold_resume=False, keep=True):
    text = ('№ 1.\nAlice - Bob\n1. e4 e5 2. Nf3\n\n' + tail +
            '\n3. Bc4 Nf6\n1-0\n№ 2.\nCarol - Dave\n1. d4 d5 2. c4 e6\n0-1')
    tokens, lines = c.tokenize([text], {1}, boundaries=True)
    first = next(i for i, t in enumerate(tokens) if t[0] == 'num' and t[1] == (2, True))
    bold = {i for i, t in enumerate(tokens) if t[0] == 'num' and t[3] == 2}
    bold.update(i for i, t in enumerate(tokens) if t[0] == 'num' and t[1] == (3, False))
    if bold_resume:
        bold.add(next(i for i, t in enumerate(tokens) if i > first and t[0] == 'num' and t[1] == (2, True)))
    f = c.GameFinder('Book', ['en'], keep, lines, ocr_pages={1}, mainline_only=True,
                     visual_tokens=bold, verified_tokens={first} if audited else set())
    f.run(tokens)
    assert not f.problems
    return f


@pytest.mark.parametrize('cue', ['Точнее, чем', 'Лучше, чем', 'Сильнее, чем',
                               'Надёжнее, чем', 'Better than', 'Rather than'])
def test_comparative_analysis_cannot_supply_unread_turn_even_without_visual_evidence(cue):
    f = extract(f'2... unread {cue} 2... Nc6.\n3. Bc4 Nf6', audited=False)
    assert [n.san() for n in f.games[0].mainline()] == ['e4', 'e5', 'Nf3']
    assert f.games[0].headers['MissingMove'] == '2...'


@pytest.mark.parametrize('keep', [True, False])
def test_audited_paragraph_number_reserves_damaged_figurine_turn(keep):
    # No language cue here: protection must come from the source paragraph.
    f = extract('2... ♘0c6. Анализ: 2... d6 или 2... Nc6.', keep=keep)
    game = f.games[0]
    assert [n.san() for n in game.mainline()] == ['e4', 'e5', 'Nf3']
    assert game.headers['MissingMove'] == '2...'
    assert game.headers['ExtractionStatus'] == 'incomplete'
    assert game.headers['Result'] == '*'
    assert any(d['code'] == 'mainline_turn_reserved' for d in f.diagnostics)
    assert [n.san() for n in f.games[1].mainline()] == ['d4', 'd5', 'c4', 'e6']


def test_explicit_bold_anchor_can_resolve_reserved_turn():
    f = extract('2... ♘0c6. Нечитаемый фрагмент.\n2... d6', bold_resume=True)
    assert [n.san() for n in f.games[0].mainline()] == ['e4', 'e5', 'Nf3', 'd6', 'Bc4', 'Nf6']


def test_readable_move_after_audited_number_is_still_accepted():
    f = extract('2... ♘c6. Точнее, чем 2... d6.')
    assert [n.san() for n in f.games[0].mainline()] == ['e4', 'e5', 'Nf3', 'Nc6', 'Bc4', 'Nf6']


def test_regular_inline_number_does_not_claim_mainline_status():
    f = extract('Вариант 2... ♘0c6.\n2... d6')
    assert not any(d['code'] == 'mainline_turn_reserved' for d in f.diagnostics)


@pytest.mark.parametrize('ending', ['', '\n1-0', '\n№ 2.\nCarol - Dave\n1. d4 d5 0-1'])
def test_unread_reserved_turn_is_incomplete_even_without_a_later_gap(ending):
    text = '№ 1.\nAlice - Bob\n1. e4 e5 2. Nf3\n\n2... ♘0c6. Анализ: 2... d6.' + ending
    tokens, lines = c.tokenize([text], {1}, boundaries=True)
    number = next(i for i, t in enumerate(tokens) if t[0] == 'num' and t[1] == (2, True))
    f = c.GameFinder('Book', ['en'], True, lines, ocr_pages={1}, mainline_only=True,
                     verified_tokens={number})
    f.run(tokens)
    assert not f.problems
    g = f.games[0]
    assert [n.san() for n in g.mainline()] == ['e4', 'e5', 'Nf3']
    assert g.headers['Result'] == '*' and g.headers['MissingMove'] == '2...'
    assert any(d['code'] == 'mainline_move_unresolved' for d in f.diagnostics)


def test_real_rosanes_ocr_cannot_replace_knight_move_with_queen_analysis(tmp_path):
    data = json.loads((Path(__file__).parent / 'fixtures/rosanes-unread-mainline.json').read_text(encoding='utf-8'))
    prefix = ('№ 5.\nRosanes - Anderssen\n'
              '1. e4 e5 2. f4 exf4 3. Nf3 g5 4. h4 g4 5. Ne5 Nf6 '
              '6. Bc4 d5 7. exd5 Bd6 8. d4\n')
    tokens, lines = c.tokenize([prefix], {34}, start=34, boundaries=True)
    offset = len(tokens)
    tokens.extend((kind, tuple(value) if kind == 'num' else value, page, line + len(lines))
                  for kind, value, page, line in data['tokens'])
    lines += data['lines']
    f = c.GameFinder('Book', ['en'], True, lines, ocr_pages={34, 35}, mainline_only=True,
                     visual_tokens={i + offset for i in data['visual_tokens']},
                     verified_tokens={i + offset for i in data['verified_tokens']})
    f.run(tokens)
    assert not f.problems
    g = f.games[0]
    assert len(list(g.mainline_moves())) == 15
    assert g.end().board().fen() == data['initial_fen']
    assert g.headers['MissingMove'] == '8...' and g.headers['Result'] == '*'
    assert any(d['code'] == 'mainline_comment_substitution_blocked' for d in f.diagnostics)
    from conversion_quality import build_report, serialize_games
    serialize_games(f.games)
    source = tmp_path / 'source.txt'
    source.write_text('\n'.join(lines), encoding='utf-8')
    report = build_report(f, tokens, ['\n'.join(lines), ''], source, tmp_path / 'out.pgn',
                          33, 2, {}, c.parse_move, lambda w: bool(c.move_code(w)))
    blocked = next(d for d in report['issues'] if d['code'] == 'mainline_comment_substitution_blocked')
    assert '♘0h5.' in blocked['source_line']
