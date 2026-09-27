import io
import json
from pathlib import Path

import chess
import chess.pgn

import chess_converter as c
import coordinate_ocr as co
from conversion_quality import serialize_games, tree_signature


def test_regular_font_on_46_untrained_word_crops():
    samples = json.loads((Path(__file__).parent/'fixtures/fine_print_words.json').read_text(encoding='utf8'))
    profiles = {style: co.load_profile(True, style=style) for style in ('bold', 'regular')}
    correct = dict(bold=0, regular=0)
    for sample in samples:
        pixels = bytes(0 if c == '1' else 255 for row in sample['rows'] for c in row)
        for style, profile in profiles.items():
            decoded = co.decode(pixels, sample['width'], (0,0,sample['width'],sample['height']),
                                sample['glyphs'], 300, profile)
            if decoded:
                assert decoded[0] == sample['expected'], sample['id']
                correct[style] += 1
    # This measures the visual verifier, not the whole Tesseract pipeline.
    assert correct['regular'] >= 16
    assert correct['bold'] == 0


def test_regular_profile_ambiguity_is_not_resolved_with_a_chess_square():
    samples = json.loads((Path(__file__).parent/'fixtures/fine_print_words.json').read_text(encoding='utf8'))
    sample = next(s for s in samples if s['id'] == 'volume2-page21-word233')
    data, templates = co.load_profile(True, style='regular')
    ambiguous = [('a', w, h, mask) for ch,w,h,mask in templates if ch == 'd']
    pixels = bytes(0 if c == '1' else 255 for row in sample['rows'] for c in row)
    assert co.decode(pixels, sample['width'], (0,0,sample['width'],sample['height']),
                     sample['glyphs'], 300, (data, templates+ambiguous)) is None


def test_regular_profile_changes_invalidate_fingerprint(tmp_path, monkeypatch):
    path = tmp_path/'regular.json'
    path.write_bytes(co.REGULAR_PROFILE.read_bytes())
    monkeypatch.setattr(co, 'REGULAR_PROFILE', path)
    before = co.profile_fingerprint()
    path.write_bytes(path.read_bytes()+b'\n')
    assert co.profile_fingerprint() != before


def test_regular_reading_validates_notation_without_reserving_mainline():
    text = '1. e4 e5 2. Nf3 Nc6 (instead 3. Bc4 Bc5) 3. Bb5 a6 1-0'
    tokens, lines = c.tokenize([text], {1})
    record = dict(kind='coordinate', style='regular', dpi=300, page=1, line=0,
                  reading='whole_word', replacement='3.Bc4')
    assert not c.visual_token_evidence(tokens, [text], 1, [record])
    verified = c.visual_token_evidence(tokens, [text], 1, [record], mainline_only=False)
    assert verified
    f = c.GameFinder('Book', ['en'], True, lines, {1}, verified_tokens=verified)
    f.run(tokens)
    assert [n.san() for n in f.games[0].mainline()] == ['e4','e5','Nf3','Nc6','Bb5','a6']
    assert f.games[0].end().parent.parent.variations[1].san() == 'Bc4'
    illegal = c.GameFinder('Book', ['en'], True, ['e5'], {1}, verified_tokens={0})
    assert illegal.read_token(chess.Board(), [('word','e5',1,0)], 0)[0] is None
    assert illegal.token_candidates(chess.Board(), [('word','e5',1,0)], 0) == []


def test_continuation_introductions_survive_pgn_roundtrip():
    # An inner bracket continues the outer branch; it has no sibling at e3.
    text = '1. Nf3 d5 2. c4 d4 3. b4 (3. e3 (if 3...c5 4. b4)) 3...g6 1-0'
    tokens, lines = c.tokenize([text], boundaries=True)
    f = c.GameFinder('Book', ['en'], True, lines)
    f.run(tokens)
    game = f.games[0]
    e3 = game.end().parent.parent.variations[1]
    assert e3.san() == 'e3' and e3.comment == 'if'
    assert e3.variations[0].starting_comment == ''
    restored = chess.pgn.read_game(io.StringIO(serialize_games([game])))
    assert tree_signature(restored) == tree_signature(game)
