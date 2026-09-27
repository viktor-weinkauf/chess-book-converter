"""Compare extracted PGNs to checked references; never infer accuracy from legality.

Usage: python benchmark.py corpus.json --output benchmark-report.json
Manifest: {"cases": [{"id": "sample", "actual": "actual.pgn", "expected": "checked.pgn"}]}
Paths are relative to the manifest. Games are paired by BookGame if ALL games
in both files have unique BookGame tags, otherwise strictly by file order.
"""

import argparse
import io
import json
from pathlib import Path

import chess.pgn

from conversion_quality import tree_signature, write_json


def load_games(path):
    stream = io.StringIO(Path(path).read_text(encoding="utf-8-sig"))
    games = []
    while (game := chess.pgn.read_game(stream)) is not None:
        if game.errors:
            raise ValueError(f"Invalid reference or candidate PGN: {path}: {game.errors}")
        games.append(game)
    if not games:
        raise ValueError(f"Empty PGN: {path}")
    return games


def keyed(games, numbered):
    return {g.headers["BookGame"] if numbered else str(i): g for i, g in enumerate(games)}


def move_paths(game):
    paths = set()
    if game is None:
        return paths
    pending = [(game, ())]
    while pending:
        node, path = pending.pop()
        for child in node.variations:
            extended = path + (child.move.uci(),)
            paths.add((game.board().fen(), extended))
            pending.append((child, extended))
    return paths


def indexed_nodes(game):
    """Index by complete move history, never by a transposed board position."""
    if game is None:
        return {}
    nodes, pending = {}, [(game, (), "", game.board())]
    while pending:
        node, path, line, board = pending.pop()
        nodes[path] = (node, line)
        for child in node.variations:
            label = f"{board.fullmove_number}{'.' if board.turn else '...'}{board.san(child.move)}"
            after = board.copy(stack=False)
            after.push(child.move)
            pending.append((child,path+(child.move.uci(),),(line+" "+label).strip(),after))
    return nodes


def tree_differences(candidate, reference):
    """Locate first missing branch moves and compare notes only at matched nodes.

    The report describes differences from a checked reference. It does not
    infer whether OCR or parsing caused them, and is not used by conversion.
    """
    found, required = indexed_nodes(candidate), indexed_nodes(reference)
    same_start = (candidate is not None and reference is not None
                  and candidate.board().fen() == reference.board().fen())
    common = found.keys() & required.keys() if same_start else set()
    missing = {path for path in required if path and path not in common}
    unexpected = {path for path in found if path and path not in common}

    def frontiers(paths, nodes):
        counts = {}
        for path in paths:
            root = path
            while root[:-1] in paths:
                root = root[:-1]
            counts[root] = counts.get(root, 0) + 1
        return [{"line": nodes[path][1], "uci_path": list(path),
                 "first_move": nodes[path][0].san(), "affected_moves": count}
                for path,count in sorted(counts.items())]

    comments, nags, orders = [], [], []
    for path in sorted(common):
        actual, line = found[path]
        expected, _ = required[path]
        for field in ("comment","starting_comment"):
            a, e = (getattr(node,field).replace("}","").strip() for node in (actual,expected))
            if a != e:
                comments.append({"line":line,"uci_path":list(path),"field":field,"actual":a,"expected":e})
        if actual.nags != expected.nags:
            nags.append({"line":line,"uci_path":list(path),"actual":sorted(actual.nags),"expected":sorted(expected.nags)})
        a = [n.move.uci() for n in actual.variations]
        e = [n.move.uci() for n in expected.variations]
        shared = set(a)&set(e)
        if [v for v in a if v in shared] != [v for v in e if v in shared]:
            orders.append({"line":line,"uci_path":list(path),"actual":a,"expected":e})
    return {"initial_position_matches":same_start,
            "missing_moves":len(missing),"unexpected_moves":len(unexpected),
            "missing_branch_roots":frontiers(missing,required),
            "unexpected_branch_roots":frontiers(unexpected,found),
            "comment_differences_on_matched_nodes":comments,
            "nag_differences_on_matched_nodes":nags,"branch_order_differences":orders}


def compare(actual, expected):
    def numbered(games):
        ids = [g.headers.get("BookGame") for g in games]
        return all(ids) and len(ids) == len(set(ids))
    by_number = numbered(actual) and numbered(expected)
    candidates, references = keyed(actual, by_number), keyed(expected, by_number)
    correct, predicted, required, exact = 0, 0, 0, 0
    rows = []
    for key in dict.fromkeys([*references, *candidates]):
        candidate, reference = candidates.get(key), references.get(key)
        candidate_paths, reference_paths = move_paths(candidate), move_paths(reference)
        matched = len(candidate_paths & reference_paths)
        correct += matched
        predicted += len(candidate_paths)
        required += len(reference_paths)
        is_exact = (candidate is not None and reference is not None
                    and candidate.headers.get("Result") == reference.headers.get("Result")
                    and tree_signature(candidate) == tree_signature(reference))
        exact += is_exact
        rows.append({"game": key, "matched_move_paths": matched, "actual_move_paths": len(candidate_paths),
                     "expected_move_paths": len(reference_paths), "exact_tree_comments_nags": is_exact,
                     "differences": tree_differences(candidate,reference)})
    return {"pairing": "BookGame" if by_number else "file_order", "actual_games": len(actual),
            "expected_games": len(expected), "exact_games": exact,
            "move_path_precision": correct / predicted if predicted else 0.0,
            "move_path_recall": correct / required if required else 0.0,
            "notes": "Each path includes all preceding moves and the initial FEN; later legal moves cannot hide an earlier wrong move.",
            "games": rows}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("manifest", type=Path)
    parser.add_argument("--output", type=Path, default=Path("benchmark-report.json"))
    args = parser.parse_args()
    manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
    results = []
    for case in manifest["cases"]:
        result = {"id": case["id"]}
        try:
            result.update(compare(load_games(args.manifest.parent / case["actual"]),
                                  load_games(args.manifest.parent / case["expected"])))
        except (ValueError, OSError) as exc:
            result["error"] = str(exc)
        results.append(result)
    write_json(args.output, {"schema_version": 1, "cases": results})
    print(args.output)
    return 0 if all("error" not in r and r["exact_games"] == r["actual_games"] == r["expected_games"]
                    for r in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
