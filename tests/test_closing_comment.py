import pytest
import chess_converter as c

PREFIX = '№ 1\nAlice - Bob\n1. e4 e5 2. Nf3 Nc6 3. Bb5 a6'


def extract(tail, ending='Ничья!', keep=True):
    tokens, lines = c.tokenize([PREFIX+'\n'+ending+'\n'+tail], boundaries=True)
    f = c.GameFinder('test', ['en'], keep, lines, mainline_only=True)
    f.run(tokens)
    assert not f.problems
    assert len(list(f.games[0].mainline_moves())) == 6
    assert f.games[0].headers['Result'] == '1/2-1/2'
    assert all(len(n.variations) <= 1 for n in f.games[0].mainline())
    return f


@pytest.mark.parametrize('ending', ['Ничья!', 'Ничья! Автор поясняет:', 'Draw.', '1/2-1/2'])
def test_final_analysis_stays_text_at_last_move_with_paragraph_boundary(ending):
    f = extract('Позиция поясняется вариантом 4. Ba4 Nf6\n'
                '(после 4... d6 5. d4) 5. O-O Be7=.\n\n'
                'Игрок родился в небольшом городе.\n1. d4 d5', ending)
    nodes = list(f.games[0].mainline())
    assert 'Ba4' in nodes[-1].comment and 'Be7=.' in nodes[-1].comment
    assert 'родился' not in str(f.games[0])
    assert all('Ba4' not in n.comment for n in nodes[:-1])
    assert any(d['code']=='closing_comment_retained' for d in f.diagnostics)


def test_next_game_title_is_not_consumed():
    f = extract('Вариант 4. Ba4 Nf6 5. O-O Be7.\n'
                '№ 2\nCarol - Dave\n1. d4 d5 2. c4 e6 3. Nc3 Nf6 1-0')
    assert len(f.games)==2 and f.games[1].headers['BookGame']=='2'
    assert 'Carol' not in f.games[0].end().comment


@pytest.mark.parametrize('tail', [
    'Игрок родился в небольшом городе.\n\nДругой абзац.',
    'После турнира автор уехал домой.\n\nДругой абзац.',
    'Вариант 4. Ba4 Nf6 5. O-O Be7. No paragraph boundary',
    'Вариант (4. Ba4 Nf6 5. O-O Be7.\n\nБиография.',
    'Вариант 4. unreadable\n\nБиография.',
    'Вариант ' + 'текст\n'*14 + '4. Ba4 Nf6\n\nБиография.',
])
def test_ambiguous_tail_is_not_attached(tail):
    f = extract(tail)
    assert 'Ba4' not in f.games[0].end().comment
    assert 'родился' not in str(f.games[0])
    assert not any(d['code']=='closing_comment_retained' for d in f.diagnostics)


def test_comment_option_is_respected():
    f = extract('Вариант 4. Ba4 Nf6\n\nБиография.', keep=False)
    assert not f.games[0].end().comment


def test_ambiguous_brackets_do_not_borrow_a_nearby_credit_boundary():
    f = extract('После 4. Ba4 (Nf6\nПримечания автора\nБиография.')
    assert 'Ba4' not in f.games[0].end().comment


def test_missing_boundary_is_reported():
    f = extract('Вариант 4. Ba4 Nf6')
    assert any(d['code']=='closing_comment_unresolved' and d['reason']=='no_nearby_boundary' for d in f.diagnostics)


@pytest.mark.parametrize('tail', ['Чистая победа!', '4:4!',
                                 'на 20. Kp:g2 решает 20... Лg6+.'])
def test_previous_bounded_game_assessments_and_russian_king_notation_survive(tail):
    f = extract(tail+'\n\nИгрок уехал домой.')
    assert f.games[0].end().comment
    assert 'уехал' not in f.games[0].end().comment


def test_page_break_is_not_an_assumed_comment_boundary():
    tokens, lines=c.tokenize([PREFIX+'\nНичья! Вариант 4. Ba4 Nf6',
                             '5. O-O Be7\n\nБиография.'], boundaries=True)
    f=c.GameFinder('test',['en'],True,lines,mainline_only=True)
    f.run(tokens)
    assert 'Ba4' not in f.games[0].end().comment
