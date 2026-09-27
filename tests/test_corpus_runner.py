import io

import chess.pgn
import pytest

from corpus_runner import check_mainlines, run


def game(text, number):
    result = chess.pgn.read_game(io.StringIO(text))
    result.headers["BookGame"] = number
    return result


def test_numbered_corpus_does_not_pair_an_unrelated_game():
    actual = [game("1. d4 d5 0-1", "150"), game("1. e4 e5 1-0", "149")]
    expected = [game("1. e4 e5 1-0", "149")]
    assert check_mainlines(actual, expected)[0]["exact_mainline_and_result"]
    actual[1].headers["Result"] = "*"
    assert not check_mainlines(actual, expected)[0]["exact_mainline_and_result"]


def test_corpus_rejects_paths_as_case_ids(tmp_path):
    manifest = tmp_path / "cases.json"
    manifest.write_text('{"cases":[{"id":"../outside"}]}')
    with pytest.raises(ValueError):
        run(manifest, tmp_path/"out")
    assert not (tmp_path/"out").exists()
