"""Local board recognition for calibrated print styles. Never infer missing pieces.

The profile contains small labelled square images, not game positions. A board
is accepted only if all 64 squares match; orientation and history are resolved
separately from the printed moves. Scores are distances, not probabilities.
"""

import hashlib
import json
import math
import re
from functools import lru_cache
from pathlib import Path

import chess
import fitz

PROFILE = Path(__file__).resolve().parent / "ocr_profiles" / "boards.json"
INK = re.compile(b"[\x00-\x95]+")
SIZE = 16


def profile_fingerprint():
    return hashlib.sha256(PROFILE.read_bytes()).hexdigest()[:16]


@lru_cache(maxsize=2)
def load_profile(fingerprint=None):
    data = json.loads(PROFILE.read_text(encoding="utf-8"))
    if data["schema_version"] != 1 or data["size"] != SIZE:
        raise ValueError("Unsupported board profile")
    return data


def rectangles(samples, width, height, dpi=300):
    """Find square frames with four continuous borders, in raster coordinates."""
    minimum = max(100, round(65 * dpi / 72))
    edges = []
    for y in range(height):
        for match in INK.finditer(samples, y * width, (y + 1) * width):
            x, z = match.start() - y * width, match.end() - y * width
            if minimum <= z - x < .88 * width:
                edges.append((x, y, z))
    found = []
    for x, y, z in edges:
        for xx, yy, zz in edges:
            if (yy - y < minimum or abs(xx - x) > 5 or abs(zz - z) > 5
                    or abs(yy - y - (z - x)) > .035 * (z - x)):
                continue
            if any(abs(y - b[1]) < 15 and abs(x - b[0]) < 15 for b in found):
                continue
            def coverage(edge):
                return sum(any(samples[row * width + col] < 150
                               for col in range(max(0, edge-3), min(width, edge+4)))
                           for row in range(y, yy)) / (yy-y)
            if min(coverage(x), coverage(z-1)) >= .93:
                found.append((x, y, z, yy))
    return found


def features(samples, stride, box):
    """Area averages suppress scan noise and diagonal square hatching."""
    x, y, z, t = box
    result = []
    for row in range(SIZE):
        y0, y1 = round(y + row*(t-y)/SIZE), round(y + (row+1)*(t-y)/SIZE)
        for col in range(SIZE):
            x0, x1 = round(x + col*(z-x)/SIZE), round(x + (col+1)*(z-x)/SIZE)
            values = [samples[j*stride+i] for j in range(y0, max(y0+1,y1))
                      for i in range(x0,max(x0+1,x1))]
            result.append(round(sum(values)/len(values)))
    return result


def squares(samples, stride, box):
    x, y, z, t = box
    w, h = (z-x)/8, (t-y)/8
    inset = max(2, round(min(w,h)*.05))
    for row in range(8):
        for col in range(8):
            cell = [round(x+col*w+inset),round(y+row*h+inset),
                    round(x+(col+1)*w-inset),round(y+(row+1)*h-inset)]
            yield (row+col)%2, cell, features(samples,stride,cell)


def classify(values, dark, profile):
    scores = {}
    for template in profile["templates"]:
        if template["dark"] != dark:
            continue
        other = template["values"]
        score = min(sum((values[y*SIZE+x]-other[(y+dy)*SIZE+x+dx])**2
                        for y in range(1,SIZE-1) for x in range(1,SIZE-1))
                    / ((SIZE-2)**2 * 255**2)
                    for dx,dy in ((0,0),(-1,0),(1,0),(0,-1),(0,1)))
        label = template["piece"]
        scores[label] = min(score, scores.get(label,1))
    ranked = sorted((score,label) for label,score in scores.items())
    best, label = ranked[0]
    margin = ranked[1][0]-best
    accepted = best <= profile["maximum_distance"] and margin >= profile["minimum_margin"]
    return label if accepted else None, round(best,5), round(margin,5)


def detect(page, dpi=300):
    pix = page.get_pixmap(dpi=dpi, colorspace=fitz.csGRAY, alpha=False)
    samples = pix.samples
    profile = load_profile(profile_fingerprint())
    boards = []
    for box in rectangles(samples,pix.width,pix.height,dpi):
        readings = list(squares(samples,pix.width,box))
        # A square table or picture frame is not a chessboard. Compare corners
        # of alternating cells, where printed pieces rarely cover the texture.
        corners = [y*SIZE+x for y in (0,1,SIZE-2,SIZE-1) for x in (0,1,SIZE-2,SIZE-1)]
        light = [sum(255-values[k] for k in corners)/(255*len(corners))
                 for dark,_,values in readings if not dark]
        dark = [sum(255-values[k] for k in corners)/(255*len(corners))
                for dark,_,values in readings if dark]
        if sum(dark)/32-sum(light)/32 < .10 or sum(v>.15 for v in dark)<24:
            continue
        cells = []
        for i,(dark,cell,values) in enumerate(readings):
            piece, distance, margin = classify(values,dark,profile)
            cells.append({"square":chess.square_name(chess.square(i%8,7-i//8)),
                          "piece":piece,"distance":distance,"margin":margin,
                          "bbox":[round(v*72/dpi,3) for v in cell]})
        board = chess.Board(None)
        complete = all(c["piece"] is not None for c in cells)
        if complete:
            for cell in cells:
                if cell["piece"] != ".":
                    board.set_piece_at(chess.parse_square(cell["square"]), chess.Piece.from_symbol(cell["piece"]))
        boards.append({"kind":"board", "bbox":[round(v*72/dpi,3) for v in box],
                       "profile":profile["id"],"cells":cells,
                       "placement":board.board_fen() if complete else None,
                       "status":"recognized" if complete else "unresolved_squares"})
    return boards


def possible_en_passant(board):
    """Legally capturable targets whose preceding double pawn push is possible.

    An irrelevant historical target is equivalent to '-' in python-chess's
    canonical FEN. Neither a pawn on the fifth rank nor an adjacent enemy pawn
    alone establishes a legal en-passant capture.
    """
    targets = [None]
    last_color = not board.turn
    landing_rank, start_rank, target_rank = (3, 1, 2) if last_color else (4, 6, 5)
    for file in range(8):
        landing = chess.square(file, landing_rank)
        start = chess.square(file, start_rank)
        target = chess.square(file, target_rank)
        pawn = chess.Piece(chess.PAWN, last_color)
        if (board.piece_at(landing) != pawn or board.piece_at(start) is not None
                or board.piece_at(target) is not None):
            continue
        trial = board.copy(stack=False)
        trial.ep_square = target
        if not trial.is_valid() or not trial.has_legal_en_passant():
            continue
        previous = board.copy(stack=False)
        previous.turn = last_color
        previous.ep_square = None
        previous.remove_piece_at(landing)
        previous.set_piece_at(start, pawn)
        if previous.is_valid() and chess.Move(start, landing) in previous.legal_moves:
            targets.append(target)
    return targets


def possible_castling_rights(board):
    """Keep every compatible history; a home-square king without a rook is safe."""
    eligible = []
    for color, king, rooks in ((chess.WHITE, chess.E1, (chess.A1, chess.H1)),
                               (chess.BLACK, chess.E8, (chess.A8, chess.H8))):
        if board.king(color) == king:
            eligible.extend(square for square in rooks
                            if board.piece_at(square) == chess.Piece(chess.ROOK, color))
    rights = [0]
    for square in eligible:
        rights += [value | chess.BB_SQUARES[square] for value in rights]
    return rights


def starting_position_analysis(placement, number, black, first_move, parse_move, langs):
    """Separate unread cells, orientation, notation and historical FEN uncertainty."""
    plain = chess.Board(placement + " w - - 0 1")
    candidates = []
    for rotated in (False,True):
        board = chess.Board(None)
        for square,piece in plain.piece_map().items():
            board.set_piece_at(63-square if rotated else square,piece)
        board.turn = not black
        board.fullmove_number = number
        if not board.is_valid():
            continue
        for rights in possible_castling_rights(board):
            for target in possible_en_passant(board):
                trial = board.copy(stack=False)
                trial.castling_rights, trial.ep_square = rights, target
                if not trial.is_valid():
                    continue
                move,_ = parse_move(trial,first_move,langs)
                if move is not None:
                    candidates.append({"fen":trial.fen(),
                                       "orientation":"black_bottom" if rotated else "white_bottom"})
    unknown = []
    for field, index in (("castling_rights",2),("en_passant",3)):
        if len({c["fen"].split()[index] for c in candidates}) > 1:
            unknown.append(field)
    orientations = {c["orientation"] for c in candidates}
    status = ("incompatible_notation_or_position" if not candidates else
              "ambiguous_orientation" if len(orientations)>1 else
              "unresolved_history" if len(candidates)>1 else "resolved")
    return {"status":status, "candidates":candidates, "unknown_fields":unknown,
            "halfmove_clock":"unknown; encoded as 0"}


def starting_positions(placement, number, black, first_move, parse_move, langs):
    result = starting_position_analysis(placement,number,black,first_move,parse_move,langs)
    return [(c["fen"],c["orientation"]) for c in result["candidates"]] if result["status"] == "resolved" else []


def continuation_equivalent_setup(assessment, first_move, parse_move, langs):
    """Normalize irrelevant en-passant history, with explicit uncertainty.

    The first printed move must be identical and lead to exactly the same
    state for EVERY possible setup. This does not resolve the earlier history
    and must never be presented as a fully recovered historical FEN.
    """
    if (assessment["status"] != "unresolved_history"
            or assessment["unknown_fields"] != ["en_passant"]):
        return None
    states = set()
    canonical = None
    for candidate in assessment["candidates"]:
        board = chess.Board(candidate["fen"])
        move, _ = parse_move(board, first_move, langs)
        if move is None or board.is_en_passant(move):
            return None
        if board.ep_square is None:
            canonical = candidate
        board.push(move)
        states.add((candidate["orientation"], move.uci(), board.fen(en_passant="fen")))
    return canonical if len(states) == 1 else None


def valid_evidence(record):
    if (record.get("kind") != "board" or record.get("status") not in
            {"recognized","unresolved_squares","initial_position","unresolved_context","unresolved_history","position_marker"}
            or not isinstance(record.get("cells"),list) or len(record["cells"]) != 64
            or not re.fullmatch(r"__BOARD_\d+__",str(record.get("marker","")))):
        return False
    seen, board, complete = set(), chess.Board(None), True
    for cell in record["cells"]:
        if (not isinstance(cell,dict) or cell.get("square") not in chess.SQUARE_NAMES
                or cell["square"] in seen or cell.get("piece") not in [None,".",*"KQRBNPkqrbnp"]):
            return False
        box = cell.get("bbox")
        if (not isinstance(box,list) or len(box)!=4
                or not all(isinstance(v,(float,int)) and math.isfinite(v) for v in box)
                or box[2]<=box[0] or box[3]<=box[1]):
            return False
        seen.add(cell["square"])
        if cell["piece"] is None:
            complete = False
        elif cell["piece"] != ".":
            board.set_piece_at(chess.parse_square(cell["square"]),chess.Piece.from_symbol(cell["piece"]))
    return record.get("placement") == (board.board_fen() if complete else None)
