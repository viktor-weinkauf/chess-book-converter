"""Repeat local book conversions against checked mainline references.

This is a regression runner, not an accuracy certificate for comments/variations.
All paths in a manifest are relative to that manifest, unless absolute.
"""

import argparse
import json
import re
from pathlib import Path

from benchmark import load_games, compare
from chess_converter import convert
from conversion_quality import report_path_for, write_json


def check_mainlines(actual, expected):
    rows = []
    for index, reference in enumerate(expected):
        game_id = reference.headers.get("BookGame")
        matches = [g for g in actual if g.headers.get("BookGame") == game_id] if game_id else actual[index:index+1]
        candidate = matches[0] if len(matches) == 1 else None
        required = list(reference.mainline_moves())
        found = list(candidate.mainline_moves()) if candidate else []
        same_start = candidate is not None and candidate.board().fen() == reference.board().fen()
        prefix = 0
        for a, b in zip(found, required):
            if not same_start or a != b:
                break
            prefix += 1
        exact = (same_start
                 and found == required and candidate.headers["Result"] == reference.headers["Result"])
        rows.append({"book_game": game_id, "expected_plies": len(required), "actual_plies": len(found),
                     "initial_position_matches": same_start,
                     "result_matches": candidate is not None and candidate.headers['Result'] == reference.headers['Result'],
                     "matching_prefix_plies": prefix, "exact_mainline_and_result": exact})
    return rows


def run(manifest_path, output):
    manifest_path, output = Path(manifest_path).resolve(), Path(output).resolve()
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    ids = [c["id"] for c in manifest["cases"]]
    if len(ids) != len(set(ids)) or not ids or not all(re.fullmatch(r"[a-zA-Z0-9_-]+", i) for i in ids):
        raise ValueError("Cases require unique nonempty IDs containing letters, digits, '_' or '-'.")
    results = {"schema_version": 2, "scope": "Mainlines, initial FEN and results are checked. "
               "Cases with compare_tree also check branches, comment placement and NAGs. Unreferenced games are not scored.", "cases": []}
    for case in manifest["cases"]:
        row = {"id": case["id"], "status": "failed"}
        results["cases"].append(row)
        try:
            source = manifest_path.parent / case["source"]
            reference = manifest_path.parent / case["reference"]
            first, last = case["pages"]
            expected = load_games(reference)
            destination = output / (case["id"] + ".pgn")
            print(f"Checking {case['id']} (pages {first}-{last})", flush=True)
            convert(source, destination, {"lang": case.get("lang", "auto"),
                                          "first_page": first, "last_page": last,
                                          "mainline_only": case.get("mainline_only", False),
                                          "figurine_ocr": case.get("figurine_ocr", "auto"),
                                          "coordinate_ocr": case.get("coordinate_ocr", "auto")},
                    lambda fraction, message: print(f"  {message}", flush=True))
            actual = load_games(destination)
            row["games"] = check_mainlines(actual, expected)
            report = json.loads(report_path_for(destination).read_text(encoding="utf-8"))
            row["conversion_quality"] = report["status"]
            row["status"] = "passed_mainlines" if all(g["exact_mainline_and_result"] for g in row["games"]) else "needs_work"
            if case.get("compare_tree"):
                ids = {g.headers.get("BookGame") for g in expected}
                selected = [g for g in actual if g.headers.get("BookGame") in ids] if None not in ids else actual
                row["tree"] = compare(selected,expected)
                row["status"] = "passed_tree" if (row["tree"]["exact_games"] == len(expected) == len(selected)) else "needs_work"
        except Exception as exc:
            row["error"] = f"{type(exc).__name__}: {exc}"
        write_json(output / "corpus-report.json", results)
        print(f"  {row['status']}", flush=True)
    return results


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("manifest", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = run(args.manifest, args.output)
    return 1 if any(c["status"] == "failed" for c in result["cases"]) else 2 if any(
        c["status"] == "needs_work" for c in result["cases"]) else 0


if __name__ == "__main__":
    raise SystemExit(main())
