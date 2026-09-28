"""Conservative Russian line-wrap repair, with source evidence and no predictions."""

import hashlib
import re
from functools import lru_cache
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path

# A dash, mixed-script token, move, or compound fragment is not a candidate.
END = re.compile(r"(?<![\w\-‐])([А-ЯЁа-яё][а-яё]{1,35})([-‐])[ \t]*$")
START = re.compile(r"^[ \t]*([а-яё]{2,36})(?![\w\-‐])([.,;:!?…»”\"'’)\]}]*)")


@lru_cache(maxsize=1)
def russian_dictionary():
    try:
        import pymorphy3
        import pymorphy3_dicts_ru
        # Use the shipped dictionary, not an unrelated PYMORPHY2_DICT_PATH.
        return pymorphy3.MorphAnalyzer(path=pymorphy3_dicts_ru.get_path(), lang="ru",
                                      probability_estimator_cls=None)
    except (ImportError, OSError, ValueError):
        return None


def dictionary_info():
    result = {"language": "ru", "predictions": False, "exact_matching": True}
    for package in ("pymorphy3", "pymorphy3-dicts-ru"):
        try:
            result[package] = version(package)
        except PackageNotFoundError:
            result[package] = None
    result["available"] = russian_dictionary() is not None
    return result


def dictionary_fingerprint():
    """Batch resume must notice a changed or newly installed dictionary."""
    digest = hashlib.sha256(repr(dictionary_info()).encode())
    dictionary = russian_dictionary()
    if dictionary is not None:
        for path in sorted(Path(dictionary.dictionary.path).iterdir()):
            if path.is_file():
                digest.update(path.name.encode())
                digest.update(path.read_bytes())
    return digest.hexdigest()


def repair_lines(lines, page, evidence):
    """Keep line count and originals intact; repair only two adjacent lines.

    Both complete spellings are checked by exact dictionary membership. If
    both/neither exist, leave the original text. Word forms are never inferred
    from a suffix, frequency, a reference PGN, or subsequent chess moves.
    """
    repaired = list(lines)
    for index in range(len(lines) - 1):
        left = END.search(lines[index])
        right = START.match(lines[index + 1])
        if not left or not right:
            continue
        head, tail = left[1], right[1]
        joined, hyphenated = head + tail, head + "-" + tail
        dictionary = russian_dictionary()
        record = {"page": page, "line": index, "next_line": index + 1,
                  "source_lines": [lines[index], lines[index + 1]],
                  "original": head + left[2] + "\n" + tail + right[2], "applied": False}
        if dictionary is None:
            record["reason"] = "dictionary_unavailable"
        else:
            plain_known = dictionary.word_is_known(joined.casefold(), strict=True)
            hyphen_known = dictionary.word_is_known(hyphenated.casefold(), strict=True)
            if plain_known == hyphen_known:
                record["reason"] = "ambiguous" if plain_known else "unknown_word"
            else:
                replacement = joined if plain_known else hyphenated
                replacement += right[2]
                record.update(applied=True, replacement=replacement,
                              reason="removed_wrap_hyphen" if plain_known else "kept_lexical_hyphen")
                # Use the current strings: the preceding line may have already
                # consumed a leading fragment. No line is removed or invented.
                end = END.search(repaired[index])
                repaired[index] = repaired[index][:end.start(1)] + replacement
                repaired[index + 1] = repaired[index + 1][right.end():]
        evidence.append(record)
    return repaired
