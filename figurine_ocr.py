"""Local visual recognition of printed chess figurines, with explicit abstention.

Templates describe glyphs, never moves or positions. Unmatched fonts are left to
the normal OCR. No game database, engine or network is involved.
"""

import hashlib
import json
import re
from pathlib import Path

import fitz

PROFILE = Path(__file__).resolve().parent / "ocr_profiles" / "classic.json"
SYMBOLS = {"K": "♔", "Q": "♕", "R": "♖", "B": "♗", "N": "♘"}
INK = re.compile(b"[\x00-\x95]+")
SIZE = 32


def components(samples, width, height):
    """Eight-connected ink components using scanline runs (no extra dependency)."""
    runs, parents, previous = [], [], []

    def root(i):
        while parents[i] != i:
            parents[i] = parents[parents[i]]
            i = parents[i]
        return i

    for y in range(height):
        current, p = [], 0
        for match in INK.finditer(samples, y * width, (y + 1) * width):
            x0, x1 = match.start() - y * width, match.end() - y * width
            n = len(runs)
            runs.append((x0, y, x1, y + 1))
            parents.append(n)
            current.append(n)
            while p < len(previous) and runs[previous[p]][2] < x0:
                p += 1
            q = p
            while q < len(previous) and runs[previous[q]][0] <= x1:
                parents[root(previous[q])] = root(n)
                q += 1
        previous = current
    boxes = {}
    for i, (x0, y0, x1, y1) in enumerate(runs):
        key = root(i)
        if key not in boxes:
            boxes[key] = [x0, y0, x1, y1]
        else:
            b = boxes[key]
            b[:] = min(b[0], x0), min(b[1], y0), max(b[2], x1), max(b[3], y1)
    return list(boxes.values())


def bitmap(samples, stride, box):
    """A normalized bit mask; raster/threshold choices also apply to training."""
    x0, y0, x1, y1 = box
    xs = [min(x1 - 1, x0 + int((x + 0.5) * (x1 - x0) / SIZE)) for x in range(SIZE)]
    result = 0
    for y in range(SIZE):
        offset = min(y1 - 1, y0 + int((y + 0.5) * (y1 - y0) / SIZE)) * stride
        for x in xs:
            result = (result << 1) | (samples[offset + x] < 150)
    return result


def load_profile(path=PROFILE):
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if data["schema_version"] != 1 or data["bitmap_size"] != SIZE:
        raise ValueError("Unsupported figurine OCR profile")
    templates = [(t["piece"], t["aspect"], int(t["bitmap"], 16)) for t in data["templates"]]
    return data, templates


def profile_fingerprint():
    return hashlib.sha256(PROFILE.read_bytes()).hexdigest()[:16]


def classify(samples, stride, box, templates, minimum=0.86, margin=0.07):
    """Compare shapes, including a prefix when the next letter touches a glyph."""
    x0, y0, x1, y1 = box
    width, height = x1 - x0, y1 - y0
    masks, scores = {}, {}
    for piece, aspect, template in templates:
        ideal = round(height * aspect)
        for w in {min(width, ideal + delta) for delta in (-2, -1, 0, 1, 2)}:
            if not 0.55 <= w / height <= 1.3:
                continue
            if w not in masks:
                masks[w] = bitmap(samples, stride, (x0, y0, x0 + w, y1))
            mask = masks[w]
            denominator = mask.bit_count() + template.bit_count()
            score = 2 * (mask & template).bit_count() / denominator if denominator else 0
            if score > scores.get(piece, (0, 0))[0]:
                scores[piece] = score, w
    ranked = sorted(((s, piece, w) for piece, (s, w) in scores.items()), reverse=True)
    if not ranked:
        return None
    best, piece, width = ranked[0]
    runner_up = ranked[1][0] if len(ranked) > 1 else 0
    if best < minimum or best - runner_up < margin:
        return None
    return piece, best, width


def detect(page, dpi):
    """Return glyph boxes in original page points; large board pieces are excluded."""
    data, templates = load_profile()
    pix = page.get_pixmap(dpi=dpi, colorspace=fitz.csGRAY, alpha=False)
    samples, scale = pix.samples, 72 / dpi
    glyphs = []
    for box in components(samples, pix.width, pix.height):
        height, width = (box[3] - box[1]) * scale, (box[2] - box[0]) * scale
        if not 4.5 <= height <= 10 or width < 3.5 or width > 35:
            continue
        match = classify(samples, pix.width, box, templates,
                         data["minimum_similarity"], data["minimum_margin"])
        if match:
            piece, score, prefix = match
            x0, y0, _, y1 = box
            glyphs.append({"symbol": SYMBOLS[piece], "piece": piece,
                           "bbox": [round(v * scale, 3) for v in (x0, y0, x0 + prefix, y1)],
                           "similarity": round(score, 4), "profile": data["id"]})
    return glyphs


MOVE_PREFIX = re.compile(r"^([([]?(?:\d{1,3}[.,…]+)?)")
MOVE_END = re.compile(r"([!?+#=±∞.,;)\]]*)$")


def replace_prefix(word, symbol):
    """Replace only the OCR's piece prefix, retaining its square/capture reading.

    This does not infer a destination from legal moves. Fused prose and multiple
    move numbers are rejected; uncertain suffixes remain visible to the parser.
    """
    prefix = MOVE_PREFIX.match(word).group()
    core = word[len(prefix):]
    suffix = MOVE_END.search(core).group()
    core = core[:len(core) - len(suffix)] if suffix else core
    if not core or len(core) > 9 or re.search(r"\d[.,]\d", core):
        return None
    if re.search(r"[а-яА-Яa-zA-Z]{4}", core):
        return None
    if len(core) <= 2:
        # OCR sometimes drops the whole glyph, leaving only the square.
        tail = core if len(core) == 2 else ""
    else:
        tail = core[1:]
        # A knight outline is frequently read as two signs: "8\\f3" or "0)f3".
        tail = tail.lstrip("\\()|&%")
        # A speck on a visually identified figurine sometimes becomes a dot
        # before its capture colon: "£.:f3". The capture and square stay intact.
        if tail.startswith((".:",".x",".×")):
            tail = tail[1:]
    if tail and not re.fullmatch(r"[a-hасеЬьбд0-9ЗзIilSx:×@¢£#№?!+−—–-]{1,7}", tail):
        return None
    return prefix + symbol + tail + suffix


def restore_words(words, glyphs):
    """Correct piece prefixes in place, preserving the original OCR of the rest.

    Re-OCR after masking a figurine worsens neighbouring squares. The original
    word is retained in the audit. Ambiguous geometric assignments are skipped.
    """
    result = [list(w) for w in words]
    assigned = {}
    for g in glyphs:
        box = fitz.Rect(g["bbox"])
        candidates = []
        for i, word in enumerate(words):
            rect = fitz.Rect(word[:4])
            overlap = (rect & box).get_area() / box.get_area()
            if overlap >= 0.45 and rect.height < box.height * 1.8:
                candidates.append((overlap, -rect.get_area(), i))
        if candidates:
            i = max(candidates)[2]
            assigned.setdefault(i, []).append(g)
    for i, matches in assigned.items():
        if len(matches) != 1:
            continue
        g, word = matches[0], words[i]
        prefix = MOVE_PREFIX.match(word[4]).group()
        # A glyph must be at the start of a move, after its optional number.
        estimated = word[0] + (word[2] - word[0]) * len(prefix) / max(1, len(word[4]))
        if abs(g["bbox"][0] - estimated) > (g["bbox"][2] - g["bbox"][0]) * 0.85:
            continue
        replacement = replace_prefix(word[4], g["symbol"])
        if replacement is not None:
            result[i][4] = replacement
            g["original_word"] = word[4]
            g["replacement"] = replacement
            g["word_index"] = i
    return result


def readable_move(word):
    core = word[len(MOVE_PREFIX.match(word).group()):]
    return bool(re.fullmatch(r"[♔♕♖♗♘][a-hасеЬь]?[1-8]?[x:×]?[a-hасеЬь][1-8][!?+#=±∞.,;)\]]*", core))


def alternate_conflict(before, after):
    """Keep the clear parts of a damaged reading when the second OCR disagrees.

    For example, N:dS has a clear file and capture mark. A second reading Na5
    does not justify replacing either, even though Na5 has valid syntax.
    """
    def core(word):
        word = word[len(MOVE_PREFIX.match(word).group()):]
        suffix = MOVE_END.search(word).group()
        return word[:-len(suffix)] if suffix else word
    own, other = core(before), core(after)
    if any(c in own for c in ':x×') and not any(c in other for c in ':x×'):
        return 'lost_capture'
    if len(own) < 3 or len(other) < 3:
        return None
    normalize = str.maketrans({'а':'a','с':'c','е':'e','Ь':'b','ь':'b'})
    file, rank = own[-2:].translate(normalize)
    new_file, new_rank = other[-2:].translate(normalize)
    if file in 'abcdefgh' and file != new_file:
        return 'changed_file'
    if rank in '12345678' and rank != new_rank:
        return 'changed_rank'
    return None


def read_with_letters(page, glyphs, language, tessdata, dpi):
    """A second visual reading with ordinary letters in place of known glyphs.

    Only the disposable image is changed. The second reading may repair a
    malformed square, never replace a syntactically clear original square.
    """
    with fitz.open() as doc:
        clean = doc.new_page(width=page.rect.width, height=page.rect.height)
        clean.show_pdf_page(clean.rect, page.parent, page.number)
        for g in glyphs:
            box = fitz.Rect(g["bbox"])
            clean.draw_rect(box, color=None, fill=(1, 1, 1), overlay=True)
            size = min(box.height / 0.73, box.width / fitz.get_text_length(g["piece"], fontname="hebo", fontsize=1))
            clean.insert_text((box.x0, box.y1), g["piece"], fontname="hebo", fontsize=size)
        tp = clean.get_textpage_ocr(language=language, dpi=dpi, full=True, tessdata=tessdata)
        return clean.get_text("words", textpage=tp)


def repair_words(page, words, glyphs, language, tessdata, dpi):
    result = restore_words(words, glyphs)
    if not any("replacement" in g and not readable_move(g["replacement"]) for g in glyphs):
        return result
    alternatives = [{k: v for k, v in g.items() if k not in ("word_index", "replacement", "original_word")}
                    for g in glyphs]
    alt_words = read_with_letters(page, alternatives, language, tessdata, dpi)
    restore_words(alt_words, alternatives)
    consumed = set()
    for g, alt in zip(glyphs, alternatives):
        if "replacement" not in g or "replacement" not in alt:
            continue
        before, after = g["replacement"], alt["replacement"]
        if not readable_move(after):
            continue
        original_prefix = MOVE_PREFIX.match(before).group()
        after = original_prefix + after[len(MOVE_PREFIX.match(after).group()):]
        if after == before:
            continue
        g["alternate_word"] = alt["original_word"]
        g["alternate_reading"] = after
        conflict = alternate_conflict(before, after)
        if not readable_move(before) and conflict is None:
            index = g["word_index"]
            alternate = alt_words[alt["word_index"]]
            neighbours = alternate_word_span(words, index, alternate, after, glyphs, g)
            if neighbours is None or index in consumed or any(j in consumed for j in neighbours):
                g["alternate_rejected"] = "ambiguous_word_span"
                g["conflicting_readings"] = True
                continue
            result[g["word_index"]][4] = after
            if neighbours:
                # The second OCR can join a piece and a square which the first
                # OCR split into separate words. Consume exactly those pixels'
                # old words once, otherwise e.g. "24.Nc4 c4" breaks the branch.
                result[index][:4] = list(alternate[:4])
                g["consumed_words"] = [{"word": words[j][4], "bbox": list(words[j][:4])} for j in neighbours]
                consumed.update(neighbours)
            g["replacement"] = after
            g["used_alternate"] = True
        else:
            g["conflicting_readings"] = True
            if conflict:
                g["alternate_rejected"] = conflict
    return [word for i,word in enumerate(result) if i not in consumed]


def alternate_word_span(words, index, alternate, reading, glyphs, owner):
    """Identify a second reading's square fragment; never consume other moves.

    Geometry alone is insufficient: the neighbouring fragment must also be the
    unchanged printed suffix of this move, and no other figurine may lie inside.
    None means that the proposed reading overlaps text it cannot safely own.
    """
    box = fitz.Rect(alternate[:4])
    neighbours = []
    for j,word in enumerate(words):
        if j == index:
            continue
        rect = fitz.Rect(word[:4])
        overlap = (rect & box).get_area()/max(rect.get_area(),.001)
        if overlap < .15:
            continue
        fragment = word[4].translate(str.maketrans({"а":"a","с":"c","е":"e","Ь":"b","ь":"b"}))
        if (overlap < .70 or rect.x0 < words[index][2] - 1
                or not re.fullmatch(r"[a-h][1-8][!?+#.,;)]*",fragment)
                or not reading.endswith(fragment)):
            return None
        neighbours.append(j)
    if len(neighbours) > 1:
        return None
    if neighbours and any(g is not owner and box.contains(fitz.Rect(g['bbox']).tl +
                         (fitz.Rect(g['bbox']).br-fitz.Rect(g['bbox']).tl)/2) for g in glyphs):
        return None
    return neighbours


def join_symbols(text):
    # Works with disambiguation/captures and attached move numbers: "12. ♘ f3".
    return re.sub(r"([♔♕♖♗♘]) +(?=[a-hасеЬь1-8:x×])", r"\1", text)
