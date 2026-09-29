import chess
import pytest
import chess_converter as c


@pytest.mark.parametrize('word,expected', [
    ('a1♕48.♖:a1', ['a1♕', '48.♖:a1']),
    ('47...a1♕48.♖:a1', ['47...a1♕', '48.♖:a1']),
    ('a8=N+12...Rxa8', ['a8=N+', '12...Rxa8']),
    ('b:a8♖+12...Kxa8', ['b:a8♖+', '12...Kxa8']),
    ('b7xa8Q12...Kxa8', ['b7xa8Q', '12...Kxa8']),
])
def test_split_preserves_every_printed_character(word, expected):
    assert c.split_glued_promotion(word) == expected
    assert ''.join(expected) == word
    assert c.tokenize([word])[0] == c.tokenize([' '.join(expected)])[0]


@pytest.mark.parametrize('word', ['a1♕Rxa1', 'a1♕48.', 'a2♕48.Rxa2',
                                 'a1♔48.Rxa1', 'a148.Rxa1', 'a1♕48.biography',
                                 'worda1♕48.Rxa1', 'a1♕48.Rxa1text'])
def test_incomplete_or_prose_words_are_not_reconstructed(word):
    assert c.split_glued_promotion(word) == [word]


def test_promotion_and_numbered_reply_are_played_with_their_comments():
    text = 'k7/8/8/8/8/7K/p7/7R b - - 0 47\n47...a1♕48.♖:a1\nPromotion exchanged. 48...Kb7 *'
    tokens, lines = c.tokenize([text], {1})
    finder = c.GameFinder('fixture', ['en'], True, lines, ocr_pages={1}, mainline_only=True)
    finder.run(tokens)
    assert not finder.problems
    game = finder.games[0]
    assert [m.uci() for m in game.mainline_moves()] == ['a2a1q', 'h1a1', 'a8b7']
    assert 'Promotion exchanged' in list(game.mainline())[1].comment
    assert game.board().fullmove_number == 47


@pytest.mark.parametrize('ending', ['Ничья!', 'Ничья.', 'Draw.', 'Remis!'])
def test_result_at_start_of_final_paragraph_excludes_postgame_moves(ending):
    text = ('1. e4 e5 2. Nf3 Nc6 3. Bb5 a6\n[#]\n'
            f'{ending} Анализ позиции: 4. Ba4 Nf6 5. O-O Be7\nUnrelated biography.')
    tokens, lines = c.tokenize([text], {1}, boundaries=True)
    f = c.GameFinder('fixture', ['en'], True, lines, ocr_pages={1}, mainline_only=True)
    f.run(tokens)
    assert len(f.games) == 1 and not f.problems
    g = f.games[0]
    assert len(list(g.mainline_moves())) == 6
    assert g.headers['Result'] == '1/2-1/2'
    assert 'biography' not in str(g)


@pytest.mark.parametrize('text', [
    'Ничья возможна при точной игре',
    'Ничья? Ещё не ясно',
    '(\nНичья! Но есть 3... Nf6\n)',
])
def test_draw_discussion_is_not_a_game_ending(text):
    tokens, lines = c.tokenize(['1. e4 e5 2. Nf3 Nc6 3. Bb5\n'+text+'\n3... a6 1-0'], boundaries=True)
    f = c.GameFinder('fixture', ['en'], True, lines, mainline_only=True)
    f.run(tokens)
    assert f.games[0].headers['Result'] == '1-0'


def test_split_does_not_fill_an_absent_numbered_turn():
    tokens, lines = c.tokenize(['k7/8/8/8/8/7K/p7/7R b - - 0 47\n47...a1♕49.♖:a1 *'])
    f = c.GameFinder('fixture', ['en'], True, lines, mainline_only=True)
    f.run(tokens)
    assert [m.uci() for m in f.games[0].mainline_moves()] == ['a2a1q']
    assert f.games[0].headers['MissingMove'] == '48.'
