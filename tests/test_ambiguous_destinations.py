"""Missing destination files must not be inferred from a legal continuation."""
from pathlib import Path

import chess
import chess.pgn
import pytest

import chess_converter as c


def reference():
    with (Path(__file__).parent / 'fixtures/anderssen-dufresne-1852-checked-mainline.pgn').open(encoding='utf-8') as stream:
        return chess.pgn.read_game(stream)


def position():
    board = reference().board()
    for move in list(reference().mainline_moves())[:37]:
        board.push(move)
    return board


@pytest.mark.parametrize('word', ['♕#3?', '♛?3', '♕3'])
def test_unread_file_is_blocked_on_every_repair_path(word):
    board = position()
    assert board.parse_san('Qh3') != board.parse_san('Qxf3')
    tokens, lines = [('word', word, 1, 0)], [word]
    finder = c.GameFinder('Test', ['en'], True, lines, ocr_pages={1}, mainline_only=True)
    assert finder.ambiguous_piece_destination(board, tokens, 0)
    assert finder.parse_token(board, tokens, 0)[0] is None
    assert finder.read_token(board, tokens, 0, lenient=True)[0] is None
    assert finder.token_candidates(board, tokens, 0) == []
    assert finder.repair(c.Frame(chess.pgn.Game(), board), tokens, 0) is None


def test_exact_aligned_reading_can_resolve_missing_file():
    tokens, lines = c.tokenize(['♕#3?'], {1})
    finder = c.GameFinder('Test', ['en'], True, lines, ocr_pages={1}, mainline_only=True)
    finder.alternatives[0] = ['Qxf3']
    assert not finder.ambiguous_piece_destination(position(), tokens, 0)
    assert finder.parse_token(position(), tokens, 0)[0] == position().parse_san('Qxf3')


@pytest.mark.parametrize('word', ['Qxf3', '♕:f3?', '♕h3', '2:7+', '175', '♗2:¢3+', '♘:¢6', '♘15?!'])
def test_coordinate_readings_and_digit_ocr_are_not_blanket_rejected(word):
    tokens, lines = [('word', word, 1, 0)], [word]
    finder = c.GameFinder('Test', ['en'], True, lines, ocr_pages={1}, mainline_only=True)
    assert not finder.ambiguous_piece_destination(position(), tokens, 0)


def test_ambiguous_move_keeps_prefix_and_never_resumes_at_later_numbers(tmp_path):
    board = reference().board()
    lines = []
    for move in list(reference().mainline_moves())[:37]:
        lines.append(f"{board.fullmove_number}{'.' if board.turn else '...'} {board.san(move)}")
        board.push(move)
    lines += ['19...♕#3?', '20. Rxe7+ Nxe7', '21. Qxd7+ Kxd7', '1-0']
    tokens, source = c.tokenize(['\n'.join(lines)], {1}, boundaries=True)
    finder = c.GameFinder('Test', ['en'], True, source, ocr_pages={1}, mainline_only=True)
    finder.run(tokens)
    assert not finder.problems
    game = finder.games[0]
    assert list(game.mainline_moves()) == list(reference().mainline_moves())[:37]
    assert game.headers['MissingMove'] == '19...'
    assert game.headers['ExtractionStatus'] == 'incomplete'
    assert game.headers['Result'] == '*'
    assert any(d['code'] == 'ocr_ambiguous_destination' for d in finder.diagnostics)
    from conversion_quality import build_report, serialize_games
    serialize_games(finder.games)
    text = '\n'.join(lines)
    source_path = tmp_path / 'source.txt'
    source_path.write_text(text, encoding='utf-8')
    report = build_report(finder, tokens, [text], source_path, tmp_path / 'out.pgn', 0, 1, {},
                          c.parse_move, lambda word: bool(c.move_code(word)))
    issue = next(i for i in report['issues'] if i['code'] == 'ocr_ambiguous_destination')
    assert issue['retained_plies'] == 37
    assert issue['severity'] == 'error'
    assert any('19...♕#3?' in row['text'] for row in issue['unparsed_source'])
    assert any('21. Qxd7+' in row['text'] for row in issue['unparsed_source'])
    assert '♕#3?' not in game.end().comment
