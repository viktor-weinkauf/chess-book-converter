"""Recheck unknown diagram cells against additional labelled print samples.

This stage runs after cached OCR and reads original pixels again. It never
stores the refined result in the OCR cache or uses chess moves to pick a piece.
"""

import hashlib
import json
from functools import lru_cache
from pathlib import Path

import chess

import diagram_ocr as d

PROFILE = Path(__file__).resolve().parent / "ocr_profiles" / "boards-extra.json"


def profile_fingerprint():
    return hashlib.sha256(PROFILE.read_bytes()).hexdigest()[:16]


@lru_cache(maxsize=2)
def load_profile(base_fingerprint, supplement_fingerprint):
    extra = json.loads(PROFILE.read_text(encoding="utf-8"))
    if extra["schema_version"] != 1 or extra["size"] != d.SIZE:
        raise ValueError("Unsupported supplemental diagram profile")
    base = d.load_profile(base_fingerprint)
    return {**base, "id": extra["id"],
            "templates": base["templates"] + extra["templates"]}


def refine(record, pix, dpi):
    """Keep established labels; accept unknown cells only at original thresholds."""
    if not d.valid_evidence(record) or record["placement"] is not None:
        return
    if pix.n != 1:
        raise ValueError("Diagram refinement requires grayscale pixels")
    fingerprint = profile_fingerprint()
    profile = load_profile(d.profile_fingerprint(), fingerprint)
    audit = {"profile": profile["id"], "fingerprint": fingerprint,
             "attempted": 0, "accepted": 0}
    samples = pix.samples
    for cell in record["cells"]:
        if cell["piece"] is not None:
            continue
        box = [round(v*dpi/72) for v in cell["bbox"]]
        if not (0 <= box[0] < box[2] <= pix.width and 0 <= box[1] < box[3] <= pix.height):
            continue
        square = chess.parse_square(cell["square"])
        dark = (chess.square_file(square)+7-chess.square_rank(square)) % 2
        piece, distance, margin = d.classify(d.features(samples,pix.stride,box), dark, profile)
        audit["attempted"] += 1
        cell["refinement"] = {"piece": piece, "distance": distance, "margin": margin}
        if piece is not None:
            cell["original_reading"] = {k: cell[k] for k in ("piece","distance","margin")}
            cell.update(piece=piece, distance=distance, margin=margin)
            audit["accepted"] += 1
    record["refinement"] = audit
    if all(cell["piece"] is not None for cell in record["cells"]):
        board = chess.Board(None)
        for cell in record["cells"]:
            if cell["piece"] != ".":
                board.set_piece_at(chess.parse_square(cell["square"]), chess.Piece.from_symbol(cell["piece"]))
        record.update(placement=board.board_fen(), status="recognized")
