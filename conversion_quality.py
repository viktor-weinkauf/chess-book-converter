"""Traceable extraction reports and validated, atomic output.

A legal PGN is not proof of fidelity to a book. Reports deliberately make no
accuracy/confidence percentage without a human-checked reference.
"""

import hashlib
import io
import json
import os
import tempfile
from collections import Counter
from pathlib import Path

import chess
import chess.pgn


def sha256_file(path):
    digest = hashlib.sha256()
    with open(path, "rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def report_path_for(destination):
    return Path(str(destination) + ".report.json")


def atomic_text(path, text):
    """Replace only after the whole UTF-8 file is written, on the same volume."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", newline="\n",
                                         dir=path.parent, prefix=f".{path.name}.",
                                         suffix=".tmp", delete=False) as out:
            temporary = Path(out.name)
            out.write(text)
            out.flush()
            os.fsync(out.fileno())
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def write_json(path, data):
    atomic_text(path, json.dumps(data, ensure_ascii=False, indent=2) + "\n")


def tree_signature(game):
    """Ordered full tree, including comments, NAGs, and starting position."""
    nodes = []
    pending = [(game, ())]
    while pending:
        node, route = pending.pop()
        nodes.append((route, node.move.uci() if node.move else None,
                      node.comment.replace("}", "").strip(),
                      node.starting_comment.replace("}", "").strip(), tuple(sorted(node.nags))))
        pending.extend((child, route + (i,)) for i, child in reversed(list(enumerate(node.variations))))
    return game.board().fen(), nodes


def serialize_games(games):
    """Validate every branch, then re-read the exported PGN before writing it."""
    for game in games:
        if not game.board().is_valid():
            raise ValueError("Invalid starting position in extracted game")
        pending = [(game, game.board())]
        while pending:
            node, board = pending.pop()
            for child in node.variations:
                if child.move not in board.legal_moves:
                    raise ValueError(f"Illegal extracted move: {child.move.uci()}")
                after = board.copy(stack=False)
                after.push(child.move)
                pending.append((child, after))
    text = "".join(f"{game}\n\n" for game in games)
    stream = io.StringIO(text)
    for original in games:
        parsed = chess.pgn.read_game(stream)
        if (parsed is None or parsed.errors or dict(parsed.headers) != dict(original.headers)
                or tree_signature(parsed) != tree_signature(original)):
            raise ValueError("Exported PGN did not preserve the extracted game tree")
    if chess.pgn.read_game(stream) is not None:
        raise ValueError("Exported PGN contains unexpected extra games")
    return text


def write_pgn(path, games):
    text = serialize_games(games)
    atomic_text(path, text)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def build_report(finder, tokens, pages, source, destination, first, count, options,
                 exact_parser, looks_like_move):
    """Describe final surviving nodes, so backtracking cannot leave stale traces."""
    issues = []
    per_page = {first + n + 1: {"page": first + n + 1, "characters": len(text.strip()),
                              "ocr": first + n + 1 in finder.ocr_pages, "moves": 0}
                for n, text in enumerate(pages)}

    def issue(code, message, token=None, page=None, severity="warning", **extra):
        record = {"code": code, "severity": severity, "message": message, **extra}
        if token is not None and 0 <= token < len(tokens):
            _, value, page, line = tokens[token]
            record.update(token=token, printed=value, source_line=finder.lines[int(line)])
        if page is not None:
            record["page"] = page
        issues.append(record)

    games, consumed = [], set()
    for number, game in enumerate(finder.games, 1):
        moves = []
        pending = [(game, game.board(), ())]
        while pending:
            parent, board, route = pending.pop()
            for variation, node in enumerate(parent.variations):
                location = route + (variation,)
                token = finder.token_of.get(id(node))
                record = {"path": list(location), "ply": board.ply() + 1,
                          "san": board.san(node.move), "uci": node.move.uci()}
                if token is not None:
                    consumed.add(token)
                    _, printed, page, line = tokens[token]
                    record.update(token=token, page=page, printed=printed,
                                  source_line=finder.lines[int(line)])
                    source_number = next((tokens[k][1][0] for k in range(token - 1, max(-1, token - 8), -1)
                                          if tokens[k][3] == line and tokens[k][0] == "num"), None)
                    if source_number is not None and source_number != board.fullmove_number:
                        issue("move_number_mismatch", "Printed and exported move numbers differ.",
                              token=token, game=number, printed_number=source_number,
                              exported_number=board.fullmove_number)
                    per_page[page]["moves"] += 1
                    langs = finder.ocr_langs[:1] if page in finder.ocr_pages else finder.langs
                    readings = finder.readings(tokens, token)
                    exact = [exact_parser(board, word, langs)[0] == node.move for word in readings]
                    if not exact[0]:
                        code = "alternate_ocr" if any(exact) else "corrected_move"
                        record["correction"] = code
                        issue(code, "The exported move differs from the primary text reading.",
                              token=token, game=number, san=record["san"], readings=readings)
                    # parse_san accepts check suffixes permissively; check the printed claim.
                    if ("+" in printed or "#" in printed) and not board.gives_check(node.move):
                        issue("check_mismatch", "Printed check sign does not match the position.",
                              token=token, game=number)
                else:
                    issue("missing_provenance", "A move has no source token.", game=number)
                moves.append(record)
                after = board.copy(stack=False)
                after.push(node.move)
                pending.append((node, after, location))
        moves.sort(key=lambda move: move["path"])
        games.append({"index": number, "headers": dict(game.headers),
                      "mainline_plies": sum(1 for _ in game.mainline_moves()),
                      "total_plies": len(moves),
                      "variations": sum(1 for move in moves if move["path"][-1] > 0),
                      "moves": moves})
        if game.headers.get("Result") == "*":
            issue("unknown_result", "No final result was recovered; this may be a fragment.",
                  game=number, page=int(game.headers["BookPage"]))

    # Conservative coverage warning, including moves preserved only as prose.
    # These are candidates, not a claimed loss/accuracy metric.
    for index, (kind, value, page, _) in enumerate(tokens):
        in_game = any(first <= index <= last for first, last in finder.game_ranges)
        if kind == "word" and index not in consumed and in_game and looks_like_move(value):
            issue("unparsed_move_candidate", "Move-like text was not exported as a move.", token=index)
    for diagnostic in finder.diagnostics:
        record = dict(diagnostic)
        if record["code"] == "move_sequence_gap":
            first_token = record["token"]
            last_token = min(record.get("end_token", first_token), len(tokens) - 1)
            source_lines = []
            seen = set()
            for _, _, page, line in tokens[first_token:last_token + 1]:
                if (page, int(line)) not in seen:
                    seen.add((page, int(line)))
                    source_lines.append({"page": page, "line": int(line), "text": finder.lines[int(line)]})
            record["unparsed_source"] = source_lines
        issue(**record)
    exported_numbers = {game.headers.get("BookGame") for game in finder.games}
    for index, (kind, value, page, _) in enumerate(tokens):
        if kind == "game_boundary" and value not in exported_numbers:
            issue("missing_game_section", "A numbered game heading has no exported game.",
                  token=index, book_game=value)
    for page, error in finder.problems:
        issue("parser_exception", error, page=page, severity="error")
    for page in per_page.values():
        if not page["characters"]:
            issue("empty_page", "No text was recovered on this page.", page=page["page"])
    if finder.ocr_pages:
        issue("ocr_unverified", "OCR text has not been compared to a checked reference.")
    if not games:
        issue("no_games", "No exportable games were found.", severity="error")
    needs_review = any(issue["severity"] != "info" for issue in issues)
    return {"schema_version": 1, "status": "needs_review" if needs_review else "completed",
            "extraction_mode": "mainline_with_text" if getattr(finder, "mainline_only", False) else "variation_tree",
            "source": {"path": str(Path(source).resolve()), "sha256": sha256_file(source),
                       "total_pages": count, "first_page": first + 1, "last_page": first + len(pages)},
            "output": {"path": str(Path(destination).resolve()), "written": False},
            "options": {k: v for k, v in options.items() if k != "report"},
            "summary": {"games": len(games),
                        "incomplete_games": sum(g["headers"].get("ExtractionStatus") == "incomplete" for g in games),
                        "detected_game_headings": sum(t[0] == "game_boundary" for t in tokens),
                        "mainline_plies": sum(g["mainline_plies"] for g in games),
                        "total_plies": sum(g["total_plies"] for g in games),
                        "issues": len(issues), "issue_counts": dict(Counter(i["code"] for i in issues))},
            "limitations": ["Legality and PGN round-trip checks do not establish fidelity to the book.",
                            "Diagram recognition is limited to a calibrated print style on OCR pages; uncertain cells, orientation or history remain unresolved.",
                            "Game boundaries use heading/result/credit heuristics, not semantic understanding.",
                            "Some analytical variations remain as comment text rather than PGN branches.",
                            "Unparsed move candidates may also be prose, headings, or examples.",
                            "Source tokens are normalized; source_line preserves the primary extracted text."],
            "pages": list(per_page.values()), "games": games, "issues": issues}
