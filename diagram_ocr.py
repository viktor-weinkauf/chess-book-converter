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


def starting_positions(placement, number, black, first_move, parse_move, langs):
    """Both orientations must compete. Never guess castling rights from an image.

    This initial implementation accepts setups only when neither king is on
    its original square and no en-passant capture could exist. Other positions
    remain in the report for review, even if their piece placement is clear.
    """
    plain = chess.Board(placement + " w - - 0 1")
    candidates = []
    for rotated in (False,True):
        board = chess.Board(None)
        for square,piece in plain.piece_map().items():
            board.set_piece_at(63-square if rotated else square,piece)
        board.turn = not black
        board.fullmove_number = number
        if board.king(chess.WHITE) == chess.E1 or board.king(chess.BLACK) == chess.E8:
            continue
        # Potential en-passant history cannot be recovered from piece placement.
        ep_rank = 4 if board.turn else 3
        if any(board.piece_at(chess.square(f,ep_rank)) == chess.Piece(chess.PAWN,board.turn)
               for f in range(8)):
            continue
        if not board.is_valid():
            continue
        move,_ = parse_move(board,first_move,langs)
        if move is not None:
            candidates.append((board.fen(), "black_bottom" if rotated else "white_bottom"))
    return candidates


def valid_evidence(record):
    if (record.get("kind") != "board" or record.get("status") not in
            {"recognized","unresolved_squares","initial_position","unresolved_context","position_marker"}
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
