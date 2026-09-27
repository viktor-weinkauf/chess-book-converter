import pytest

from book_structure import clean_running_headers, section_events
from chess_converter import GameFinder, column_gap, table_layout, tokenize


def extract(pages):
    tokens, lines = tokenize(clean_running_headers(pages), boundaries=True)
    finder = GameFinder("Book", ["en"], True, lines)
    finder.run(tokens)
    return finder


def test_only_numbered_games_excludes_interstitial_opening_example():
    finder = extract(["""Preface: 1. d4 d5 2. c4 e6 3. Nc3 Nf6 1-0
№ 1. First game
Alice - Bob
1. e4 e5 2. Nf3 Good development Nc6 3. Bb5 a6 1-0
An essay with an illustrative line: 1. d4 d5 2. c4 e6 3. Nc3 Nf6 1-0
№ 2. Second game
Carol - Dave
1. d4 d5 2. c4 e6 3. Nc3 Nf6 0-1
Afterword about the author's life.
"""])
    assert len(finder.games) == 2
    assert [g.headers["BookGame"] for g in finder.games] == ["1", "2"]
    pgn = "\n".join(map(str, finder.games))
    assert "Good development" in pgn
    for unwanted in ("Preface", "An essay", "Afterword", "Second game", "Carol - Dave"):
        assert unwanted not in pgn


def test_new_heading_does_not_become_previous_comment_without_result():
    finder = extract(["""№ 1.
Alice - Bob
1. e4 e5 2. Nf3 Nc6 3. Bb5 a6
Biography unrelated to the last game.
№ 2.
Carol - Dave
1. d4 d5 2. c4 e6 3. Nc3 Nf6 0-1"""])
    assert len(finder.games) == 2
    assert "Biography" not in str(finder.games[0])
    assert "Carol" not in str(finder.games[0])
    assert any(d["code"] == "excluded_tail" for d in finder.diagnostics)


def test_credit_ends_game_before_biography():
    finder = extract(["""№ 1.
Alice - Bob
1. e4 e5 2. Nf3 Nc6 3. Bb5 a6
Примечания Н. Крогиуса.
Biography 1. d4 d5 2. c4 e6 3. Nc3 Nf6 0-1"""])
    assert len(finder.games) == 1
    assert "Biography" not in str(finder.games[0])
    assert "Примечания" not in str(finder.games[0])


def test_comment_continues_across_running_header():
    finder = extract(["№ 1.\nAlice - Bob\n1. e4 e5 2. Nf3 Important comment",
                      "Глава первая\n12\ncontinues here Nc6 3. Bb5 a6 1-0"])
    pgn = str(finder.games[0])
    assert "Important comment continues here" in pgn
    assert "Глава" not in pgn


def test_plain_surnames_inside_numbered_heading():
    finder = extract(["№ 1.\nЭстрин Спасский\nРига, 1951\n1. e4 e5 2. Nf3 Nc6 3. Bb5 a6 1-0"])
    assert finder.games[0].headers["White"] == "Эстрин"
    assert finder.games[0].headers["Black"] == "Спасский"


def test_biography_at_eof_not_attached():
    finder = extract(["1. e4 e5 2. Nf3 Nc6 3. Bb5 a6\nUnrelated biography at the end."])
    assert "Unrelated biography" not in str(finder.games[0])


@pytest.mark.parametrize("word", ["сдались.", "сдались,", "сдались!"])
def test_resignation_stops_following_prose(word):
    finder = extract([f"1. e4 e5 2. Nf3 Nc6 3. Bb5 a6 Белые {word}\nBiography unrelated to game."])
    assert finder.games[0].headers["Result"] == "0-1"
    assert "Biography" not in str(finder.games[0])


def test_mixed_width_page_detects_column_gutter():
    words = []
    for row in range(10):
        y = row * 12
        words.extend([(15, y, 80, y+9, "introduction"), (90, y, 210, y+9, "fullwidth"),
                      (220, y, 285, y+9, "text")])
    for row in range(10, 30):
        y = row * 12
        words.extend([(15, y, 80, y+9, "left"), (90, y, 135, y+9, "column"),
                      (165, y, 230, y+9, "right"), (240, y, 285, y+9, "column")])
    gap = column_gap(words, 300)
    assert gap is not None and 139 <= gap <= 161


def test_full_width_page_stays_single_column():
    words = [(0, row*12, 100, row*12+9, "left") for row in range(15)]
    words += [(100, row*12, 200, row*12+9, "centre") for row in range(15)]
    words += [(200, row*12, 300, row*12+9, "right") for row in range(15)]
    assert column_gap(words, 300) is None


def test_final_chess_explanation_before_credit_is_kept():
    finder = extract(["""№ 1.
Alice - Bob
1. e4 e5 2. Nf3 Nc6 3. Bb5 a6
Белые сдались, на 4. Bxc6
решает 4...dxc6.
Примечания Н. Крогиуса.
Unrelated biography outside the game."""])
    pgn = str(finder.games[0])
    assert "решает" in pgn
    assert "Unrelated biography" not in pgn
    assert "Примечания" not in pgn


def test_many_comment_moves_do_not_hide_long_notation_table():
    lines = []
    for number in range(1, 13):
        lines.append(f"{number}. e2-e4 e7-e5")
        lines.append("Comment: " + " ".join(f"{n}. Nf3" for n in range(1, 7)))
    tokens, _ = tokenize(["\n".join(lines)])
    assert table_layout(tokens)


def test_combined_page_number_and_header_removed():
    pages = ["6 – Глава первая\n1. e4 e5", "В роли вундеркинда – 5\n2. Nf3 Nc6",
             "В роли вундеркинда – 7\n3. Bb5 a6"]
    cleaned = clean_running_headers(pages)
    assert all(text.startswith("\n") for text in cleaned)


def test_long_comment_does_not_end_numbered_game():
    commentary = " ".join(["explanation"] * 450)
    finder = extract([f"№ 121.\nAlice - Bob\n1. e4 e5\n{commentary}\n2. Nf3 Nc6 3. Bb5 a6 1-0"])
    assert len(finder.games) == 1
    assert len(list(finder.games[0].mainline_moves())) == 6
    assert commentary in str(finder.games[0])


def test_ocr_piece_read_as_number_sign_is_not_game_heading():
    lines = ["№7.:е5+ &:f7 8.d4", "1.e4 e5 2.Nf3 Nc6", "№ 1. Французская защита", "1.e4 e6"]
    events = section_events(lines)
    assert 0 not in events
    assert events[2] == ("game_boundary", "1")


def test_short_column_block_below_long_introduction():
    words = []
    for row in range(30):
        words.extend([(0, row*12, 100, row*12+9, "introduction"),
                      (100, row*12, 200, row*12+9, "spanning"),
                      (200, row*12, 300, row*12+9, "page")])
    for row in range(30, 40):
        words.extend([(0, row*12, 100, row*12+9, "left"), (110, row*12, 135, row*12+9, "text"),
                      (165, row*12, 230, row*12+9, "right"), (240, row*12, 300, row*12+9, "text")])
    assert column_gap(words, 300) is not None


def test_draw_sentence_is_a_boundary():
    finder = extract(["1. e4 e5 2. Nf3 Nc6 3. Bb5 a6\nНичья.\nUnrelated chapter prose."])
    assert finder.games[0].headers["Result"] == "1/2-1/2"
    assert "Unrelated" not in str(finder.games[0])


def test_inline_draw_keeps_bounded_closing_comment_and_excludes_biography():
    finder = extract(['1. e4 e5 2. Nf3 Nc6 3. Bb5 a6 Ничья.\n'
                      '«Какой ход!» — сказал соперник при анализе партии.\n\n'
                      'В рождественские каникулы состоялся другой турнир.'])
    assert finder.games[0].headers['Result'] == '1/2-1/2'
    assert 'соперник при анализе' in finder.games[0].end().comment
    assert 'каникулы' not in str(finder.games[0])


@pytest.mark.parametrize('ending',['Ничья.','\nНичья.\n'])
def test_draw_in_variation_does_not_finish_main_game(ending):
    finder = extract([f'1. e4 e5 2. Nf3 (2. Bc4 Nf6 {ending}) Nc6 3. Bb5 a6 1-0'])
    game = finder.games[0]
    assert len(list(game.mainline_moves())) == 6
    assert game.headers['Result'] == '1-0'


def test_draw_discussion_after_a_move_is_not_a_result():
    finder = extract(['1. e4 e5 2. Nf3 Здесь возможна ничья. 2...Nc6 3. Bb5 a6 1-0'])
    assert finder.games[0].headers['Result'] == '1-0'
    assert len(list(finder.games[0].mainline_moves())) == 6


def test_number_sign_read_as_m_requires_player_names():
    lines = ["M 150. French defence C12", "Alice - Bob", "1. e4 e6",
             "M 151. prose example", "not a player heading", "1. e4 e5"]
    assert section_events(lines)[0] == ("game_boundary", "150")
    assert 3 not in section_events(lines)


def test_partial_numbered_game_does_not_absorb_next_game_or_biography():
    finder = extract(["№ 149.\nAlice - Bob\n1. e4 e5\nUnrelated biography.\n"
                      "M 150. French defence C12\nCarol - Dave\n1. d4 d5 2. c4 e6 3. Nc3 Nf6 *"])
    assert [g.headers["BookGame"] for g in finder.games] == ["149", "150"]
    assert len(list(finder.games[0].mainline_moves())) == 2
    assert "Unrelated biography" not in str(finder.games[0])


def test_later_comment_move_cannot_fill_a_missing_opening_move():
    pages = ["№ 149.\nAlice - Bob\n1. e4 unreadable\n" + "explanation " * 20 + "4...Nf6"]
    tokens, lines = tokenize(pages, frozenset((1,)), boundaries=True)
    finder = GameFinder("Book", ["en"], True, lines, frozenset((1,)), "en")
    finder.run(tokens)
    assert [m.uci() for m in finder.games[0].mainline_moves()] == ["e2e4"]
