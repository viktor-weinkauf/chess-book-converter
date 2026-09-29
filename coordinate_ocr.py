"""Visual reading of supported algebraic fonts, without chess positions.

Read every character of a short word from its pixels before changing it. This
keeps ambiguous squares and unsupported typography in the original OCR.
"""

import hashlib
import json
import math
import re
from pathlib import Path

import fitz

import figurine_ocr
from figurine_ocr import bitmap

PROFILE = Path(__file__).resolve().parent / "ocr_profiles" / "coordinates.json"
REGULAR_PROFILE = PROFILE.with_name("regular.json")
NOTATION = re.compile(r"^(?:\d{1,3}\.{1,3})?(?P<move>[♔♕♖♗♘][a-h]?[1-8]?[:x-]?[a-h][1-8]"
                      r"|(?:[a-h][18]|[a-h][:x]?[a-h][18]?)=?[♕♖♗♘]"
                      r"|[a-h][1-8]|[a-h]{2}|0-0(?:-0)?)[!?+#]*(?:\+-|-\+)?[.,;]?$", re.ASCII)


def valid_notation(text):
    text = text.removeprefix('(').removesuffix(')')
    match = NOTATION.fullmatch(text)
    if not match:
        return False
    core = match['move']
    capture_promotion = re.fullmatch(r"([a-h])[:x]?([a-h])[18]?=?[♕♖♗♘]", core)
    if capture_promotion:
        return abs(ord(capture_promotion[1])-ord(capture_promotion[2])) == 1
    return not re.fullmatch("[a-h]{2}", core) or abs(ord(core[0]) - ord(core[1])) == 1


def load_profile(include_figurines=False, style="bold"):
    data = json.loads((REGULAR_PROFILE if style == "regular" else PROFILE).read_text(encoding="utf-8"))
    if data["schema_version"] != 1 or data["bitmap_size"] != 32:
        raise ValueError("Unsupported coordinate OCR profile")
    templates = [(t["char"], t["width_pt"], t["height_pt"], int(t["bitmap"], 16)) for t in data["templates"]
                 if include_figurines or not any(ch in t['char'] for ch in '♔♕♖♗♘')]
    if include_figurines:
        figures, _ = figurine_ocr.load_profile()
        for template in figures["templates"]:
            x0,y0,x1,y1 = template["source"]["bbox_pixels"]
            scale = 72 / template["source"]["dpi"]
            templates.append((figurine_ocr.SYMBOLS[template["piece"]], (x1-x0)*scale,
                              (y1-y0)*scale, int(template["bitmap"],16)))
    return data, templates


def profile_fingerprint():
    return hashlib.sha256(PROFILE.read_bytes() + REGULAR_PROFILE.read_bytes()).hexdigest()[:16]


def decode(samples, stride, rect, glyphs, dpi, profile, debug=None, number_prefix=None, standalone_number=False):
    """Segment touching characters with a bounded search; reject incomplete words."""
    data, templates = profile
    left, top, right, bottom = rect
    scale = dpi / 72
    columns = {}
    for x in range(left, right):
        ys = [y for y in range(top, bottom) if samples[y * stride + x] < 150]
        if ys:
            columns[x] = min(ys), max(ys) + 1
    if not columns:
        return None
    start, end = min(columns), max(columns) + 1
    anchors = []
    for g in glyphs:
        x0, y0, x1, y1 = [round(v * scale) for v in g["bbox"]]
        if left - 1 <= x0 and x1 <= right + 1 and top - 2 <= y0 and y1 <= bottom + 2:
            anchors.append((x0, x1, g))
    sizes = sorted({max(1, round(w * scale) + d) for _, w, _, _ in templates for d in (-2,-1,0,1,2)})
    states = {start: [("", [], [])]}

    def next_ink(x):
        while x < end and x not in columns:
            x += 1
        return x

    def add(x, text, scores, boxes):
        x = next_ink(x)
        bucket = states.setdefault(x, [])
        same = next((i for i, row in enumerate(bucket) if row[0] == text), None)
        item = (text, scores, boxes)
        if same is None:
            bucket.append(item)
        elif min(scores) > min(bucket[same][1]):
            bucket[same] = item
        bucket.sort(key=lambda row: (min(row[1]), sum(row[1]) / len(row[1])), reverse=True)
        del bucket[4:]

    for x in range(start, end):
        if x not in states:
            continue
        anchor = next((a for a in anchors if abs(a[0] - x) <= 1), None)
        if anchor:
            _, x1, g = anchor
            for text, scores, boxes in states[x]:
                add(x1, text + g["symbol"], scores + [g["similarity"]],
                    boxes + [{"char": g["symbol"], "bbox": g["bbox"], "similarity": g["similarity"]}])
        for width in sizes:
            x1 = x + width
            if x1 > end or x1 - 1 not in columns:
                continue
            if any(x < a[0] < x1 for a in anchors):
                continue
            occupied = [columns[c] for c in range(x, x1) if c in columns]
            y0, y1 = min(a[0] for a in occupied), max(a[1] for a in occupied)
            h = y1 - y0
            # Never manufacture two dots/digits by cutting a thick stroke in
            # half. Touching typeset letters usually share only a small bridge.
            if x1 < end:
                bridge = sum(samples[y*stride+x1-1] < 150 and samples[y*stride+x1] < 150
                             for y in range(y0,y1))
                if bridge > max(2, h * 0.25):
                    continue
            plausible = [(ch, mask) for ch, w, height, mask in templates
                         if abs(width / scale - w) <= max(0.6, w * 0.16)
                         and abs(h / scale - height) <= max(0.5, height * 0.16)
                         and (not anchor or ch.startswith(anchor[2]['symbol']))]
            if not plausible:
                continue
            mask = bitmap(samples, stride, (x,y0,x1,y1))
            count = mask.bit_count()
            ranked = {}
            for ch, template in plausible:
                score = 2 * (mask & template).bit_count() / (count + template.bit_count())
                ranked[ch] = max(ranked.get(ch, 0), score)
            choices = sorted(((score,ch) for ch,score in ranked.items()), reverse=True)
            score, ch = choices[0]
            if score < data["minimum_similarity"]:
                continue
            margin = max(0.07, data["minimum_margin"]) if any(c in ch for c in "♔♕♖♗♘") else data["minimum_margin"]
            close = [(s,c) for s,c in choices if score - s < margin]
            if len(close) > 1 and not {c for _,c in close} <= {".", "-"}:
                continue
            # A low-resolution dot and a short dash may have the same shape.
            # Keep both until notation syntax resolves them; never do this for
            # ambiguous files/ranks. No chess position participates in decoding.
            for score,ch in close:
                detail = {"char": ch, "bbox": [round(v / scale,3) for v in (x,y0,x1,y1)], "similarity": round(score,4)}
                for text, scores, boxes in states[x]:
                    if len(text) < 18:
                        add(x1, text + ch, scores + [score], boxes + [detail])
    candidates = [row for row in states.get(end, []) if valid_notation(row[0])]
    if debug is not None:
        debug.update(start=start,end=end,reached=max(states),
                     last=[row[0] for row in states[max(states)]],
                     candidates=[row[0] for row in candidates])
    unique = {row[0] for row in candidates}
    if len(unique) != 1:
        if number_prefix:
            # A clear printed move number can identify typography even when
            # the move itself is unreadable. It must agree with the OCR number;
            # never use a partial reading to change letters/squares.
            prefixes = [row for x,rows in states.items()
                        if (x == end if standalone_number else x < end)
                        for row in rows if row[0] == number_prefix]
            if prefixes:
                return max(prefixes, key=lambda row: min(row[1]))
        return None
    return candidates[0]


def complete_ink_bounds(samples, stride, width, rect, dpi):
    """Recover a clipped edge stroke up to the next blank column, with a cap.

    Tesseract's word boxes sometimes cut through a digit. Expansion is allowed
    only from an inked edge, never across whitespace into a neighbouring word.
    """
    left, top, right, bottom = rect
    limit = max(2, round(6*dpi/72))
    def ink(x):
        return any(samples[y*stride+x] < 150 for y in range(top,bottom))
    original = left, right
    while left > 0 and original[0]-left < limit and ink(left):
        left -= 1
    while right < width and right-original[1] < limit and ink(right-1):
        right += 1
    if ink(left) or ink(right-1):
        return rect  # a long connected shape is not evidence for a word boundary
    return left, top, right, bottom


def isolate_text_band(samples, stride, rect, dpi):
    """Exclude a few edge pixels of the next line across a clear horizontal gap.

    Preserve ambiguous multiple bands. This only proposes a crop: the remaining
    pixels must still yield a complete, unambiguous notation word to be used.
    """
    left,top,right,bottom = rect
    counts = {y: sum(samples[y*stride+x] < 150 for x in range(left,right))
              for y in range(top,bottom)}
    ink = [y for y,n in counts.items() if n]
    if not ink:
        return rect
    bands = [[ink[0]]]
    for y in ink[1:]:
        if y-bands[-1][-1] > max(2,round(.5*dpi/72)):
            bands.append([])
        bands[-1].append(y)
    main = max(bands,key=lambda band:sum(counts[y] for y in band))
    if (len(bands) == 1 or main[-1]-main[0]+1 < 4*dpi/72
            or sum(counts[y] for y in main) < .9*sum(counts.values())):
        return rect
    return left,max(top,main[0]-1),right,min(bottom,main[-1]+2)


def read_words(page, words, glyphs, dpi, evidence=None):
    """Return corrected words. Unchanged successful readings also retain font evidence."""
    # Only search for a missed, touching figurine when the independent detector
    # already found this glyph family on the page. With figurine OCR disabled
    # or on unsupported fonts this extra alphabet remains unavailable.
    profiles = {style: load_profile(include_figurines=bool(glyphs), style=style)
                for style in ("bold", "regular")}
    pix = page.get_pixmap(dpi=dpi, colorspace=fitz.csGRAY, alpha=False)
    samples, scale = pix.samples, dpi / 72
    result = [list(w) for w in words]
    consumed = set()
    for i, w in enumerate(words):
        if i in consumed:
            continue
        joined_number = None
        if w[4].isdigit() and len(w[4]) <= 3 and i+1 < len(words):
            following = words[i+1]
            overlap = min(w[3],following[3])-max(w[1],following[1])
            if (0 <= following[0]-w[2] <= 6 and overlap >= .7*max(w[3]-w[1],following[3]-following[1])
                    and any(ch in following[4] for ch in '♔♕♖♗♘')):
                joined_number = w[4]
                w = [w[0],min(w[1],following[1]),following[2],max(w[3],following[3]),
                     w[4]+' '+following[4],*w[5:]]
        if len(w[4]) > 20 or w[3] - w[1] > 16 or w[2] - w[0] > 65:
            continue
        # Long ordinary words and page numbers are outside the notation channel.
        has_figure = any(fitz.Rect(w[:4]).intersects(fitz.Rect(g["bbox"])) for g in glyphs)
        numeric = w[4].isdigit()
        if (re.search(r"[а-яА-Яa-zA-Z]{4}", w[4]) and not has_figure
                or numeric and (not glyphs or len(w[4]) < 3)):
            continue
        rect = (max(0,math.floor(w[0]*scale)-1), max(0,math.floor(w[1]*scale)-1),
                min(pix.width,math.ceil(w[2]*scale)+1), min(pix.height,math.ceil(w[3]*scale)+1))
        original_rect = rect
        rect = isolate_text_band(samples,pix.stride,rect,dpi)
        isolated_rect = rect
        if has_figure or re.match(r"\d{1,3}\.",w[4]):
            rect = complete_ink_bounds(samples,pix.stride,pix.width,rect,dpi)
        number = re.match(r"\d{1,3}\.{1,3}(?=\S)", w[4])
        readings = {style: decode(samples,pix.width,rect,glyphs,dpi,profile,
                                 number_prefix=number.group() if number and style == "bold" else None)
                    for style,profile in profiles.items()}
        whole = {style: row for style,row in readings.items() if row and valid_notation(row[0])}
        if joined_number is not None:
            whole = {style: row for style,row in whole.items()
                     if re.match(re.escape(joined_number)+r'\.{1,3}[♔♕♖♗♘]',row[0])}
        if numeric:
            # OCR may collapse a whole figurine move to digits (e.g. 27.Kh2).
            # Page numbers cannot become pawn moves or typography evidence.
            whole = {style: row for style,row in whole.items() if any(ch in row[0] for ch in '♔♕♖♗♘')}
        # Incompatible complete visual readings are uncertainty, not a vote.
        if len({row[0] for row in whole.values()}) > 1:
            continue
        if whole:
            style = max(whole, key=lambda s: min(whole[s][1]))
            # Equal readings with uncertain weight must not reserve a mainline.
            if len(whole) > 1 and abs(min(whole["bold"][1])-min(whole["regular"][1])) < .025:
                style = "regular"
            decoded = whole[style]
        elif readings["bold"] and not numeric and joined_number is None:
            style, decoded = "bold", readings["bold"]
        else:
            continue
        text, _, characters = decoded
        number_only = not valid_notation(text)
        result[i][4] = w[4] if number_only else text
        if joined_number is not None:
            result[i][:4] = w[:4]
            consumed.add(i+1)
        if rect != original_rect:
            result[i][:4] = [v/scale for v in rect]
        if evidence is not None:
            evidence.append({"kind": "coordinate", "reading": "number_only" if number_only else "whole_word",
                             "original_word": w[4], "replacement": result[i][4], "changed": result[i][4] != w[4],
                             "bbox": list(result[i][:4]), "characters": characters, "style": style,
                             "expanded_clipped_edge": rect != isolated_rect,
                             "isolated_text_band": isolated_rect != original_rect,
                             "joined_number_fragment": joined_number is not None,
                             "profile": profiles[style][0]["id"]})
    return [word for i,word in enumerate(result) if i not in consumed]


def valid_evidence(record):
    return (record.get("kind") == "coordinate" and record.get("style") in {"bold", "regular"}
            and record.get("reading") in {"whole_word", "number_only"}
            and isinstance(record.get("original_word"), str)
            and isinstance(record.get("replacement"), str)
            and (record.get('reading') != 'whole_word' or valid_notation(record['replacement']))
            and isinstance(record.get("changed"), bool)
            and isinstance(record.get("line"), int) and record["line"] >= 0
            and isinstance(record.get("characters"), list) and bool(record["characters"])
            and all(isinstance(c, dict) and isinstance(c.get("char"), str)
                    and isinstance(c.get("bbox"), list) and len(c["bbox"]) == 4
                    and all(isinstance(v, (int, float)) and math.isfinite(v) for v in c["bbox"])
                    and isinstance(c.get("similarity"), (int, float))
                    and 0 <= c["similarity"] <= 1 for c in record["characters"]))


def restore_coordinates(readings, offsets):
    """Undo column stacking for word and character boxes in the audit report."""
    for record in readings:
        box = record["bbox"]
        cx, cy = (box[0] + box[2]) / 2, (box[1] + box[3]) / 2
        matches = [dy for x0,x1,y0,y1,dy in offsets if x0 <= cx <= x1 and y0 <= cy <= y1]
        if len(matches) != 1:
            raise ValueError("Cannot map visual reading back to its source column")
        dy = matches[0]
        for item in [record, *record["characters"]]:
            x0,y0,x1,y1 = item["bbox"]
            item["bbox"] = [x0,y0+dy,x1,y1+dy]
