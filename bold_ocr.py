"""Refine incompletely read bold words on the original, unstacked page.

The regular OCR cache remains immutable. Templates are character shapes, not
moves. No board, game identifier or reference database participates here.
"""
import json
import math
import re
from pathlib import Path

import coordinate_ocr as co

PROFILE = Path(__file__).resolve().parent / 'ocr_profiles' / 'bold-extra.json'
NUMBER = re.compile(r'^\d{1,3}\.{1,3}(?=[^.\s])')
SPLIT_DOT = re.compile(r'(?<!\S)(\d{1,3})[ \t]+\.([KQRBNWКФЛСC♔♕♖♗♘]?[a-hасе][1-8][!?+#.,]*)(?!\S)')
FIGURE_WORD = re.compile(r'(?<!\S)(\d{1,3}\.{1,3})([♔♕♖♗♘])([^\s]{1,8})(?!\S)')
PAWN_WORD = re.compile(r'(?<!\S)(\d{1,3}\.{1,3})([a-hасепьіlI])([1-8ЗзбОо])([!?+#.,]*)(?!\S)')


def unaudited_pawns(text, records):
    targets = []
    for line, value in enumerate(text.split('\n')):
        for match in PAWN_WORD.finditer(value):
            if co.valid_notation(match[0].translate(str.maketrans('асе', 'ace'))):
                continue
            if len(re.findall(r'(?<!\S)' + re.escape(match[1]) + r'(?!\.)', text)) != 1:
                continue
            if any(r.get('kind') == 'coordinate' and r.get('line') == line
                   and r.get('replacement', '').startswith(match[1]) for r in records):
                continue
            targets.append((line, match))
    return targets


def refine_unaudited_pawns(page, text, records, lang, tessdata, dpi=300):
    """Read a short damaged pawn word in full, never infer it from the board."""
    targets = unaudited_pawns(text, records)
    if not targets:
        return text
    tp = page.get_textpage_ocr(language=lang, dpi=dpi, full=True, tessdata=tessdata)
    words = page.get_text('words', textpage=tp)
    pix = page.get_pixmap(dpi=dpi, colorspace=co.fitz.csGRAY, alpha=False)
    scale, samples = dpi / 72, pix.samples
    glyphs = [r for r in records if r.get('symbol')]
    profile = load_profile(bool(glyphs))
    lines = text.split('\n')
    for line, target in targets:
        candidates = {}
        for word in words:
            if not word[4].startswith(target[1]):
                continue
            box = (max(0, math.floor(word[0]*scale)-1), max(0, math.floor(word[1]*scale)-1),
                   min(pix.width, math.ceil(word[2]*scale)+1), min(pix.height, math.ceil(word[3]*scale)+1))
            box = co.isolate_text_band(samples, pix.stride, box, dpi)
            box = co.complete_ink_bounds(samples, pix.stride, pix.width, box, dpi)
            decoded = co.decode(samples, pix.stride, box, glyphs, dpi, profile)
            if decoded is None:
                continue
            match = re.fullmatch(re.escape(target[1]) + r'([a-h])([1-8])([!?+#.,]*)', decoded[0])
            if not match or not co.valid_notation(decoded[0]):
                continue
            known_file = target[2].translate(str.maketrans('асе', 'ace'))
            if (known_file in 'abcdefgh' and known_file != match[1]
                    or target[3] in '12345678' and target[3] != match[2]
                    or target[4] != match[3]):
                continue
            key = (decoded[0], tuple(tuple(c['bbox']) for c in decoded[2]))
            candidates[key] = decoded, box
        if len(candidates) != 1:
            continue
        decoded, box = next(iter(candidates.values()))
        lines[line] = lines[line].replace(target[0], decoded[0], 1)
        records.append(dict(kind='coordinate', reading='whole_word', style='bold',
                            original_word=target[0], primary_reading=target[0], replacement=decoded[0],
                            changed=True, source_refined=True, located_pawn=True,
                            bbox=[v / scale for v in box], characters=decoded[2],
                            line=line, profile=profile[0]['id']))
    return '\n'.join(lines)


def wrapped_figures(text):
    lines = text.split('\n')
    targets = []
    for line, (before, after) in enumerate(zip(lines, lines[1:])):
        number = re.search(r'(?<!\S)(\d{1,3}\.{1,3})[ \t]*$', before)
        move = re.match(r'([♔♕♖♗♘])[^\s]{1,8}(?!\S)', after)
        if number and move:
            targets.append((line, number, move))
    return targets


def refine_wrapped_figures(page, text, records, lang, tessdata, dpi=300):
    """Join a move across lines only when both printed fragments read in bold."""
    targets = wrapped_figures(text)
    if not targets:
        return text
    tp = page.get_textpage_ocr(language=lang, dpi=dpi, full=True, tessdata=tessdata)
    words = page.get_text('words', textpage=tp)
    pix = page.get_pixmap(dpi=dpi, colorspace=co.fitz.csGRAY, alpha=False)
    scale, samples = dpi / 72, pix.samples
    glyphs = [r for r in records if r.get('symbol')]
    if not glyphs:
        return text
    profile = load_profile(True)
    lines = text.split('\n')

    def read(group, **kwargs):
        box = (max(0, math.floor(min(w[0] for w in group)*scale)-1),
               max(0, math.floor(min(w[1] for w in group)*scale)-1),
               min(pix.width, math.ceil(max(w[2] for w in group)*scale)+1),
               min(pix.height, math.ceil(max(w[3] for w in group)*scale)+1))
        box = co.isolate_text_band(samples, pix.stride, box, dpi)
        box = co.complete_ink_bounds(samples, pix.stride, pix.width, box, dpi)
        return co.decode(samples, pix.stride, box, glyphs, dpi, profile, **kwargs)

    for line, number, move in targets:
        if len(re.findall(r'(?<!\S)' + re.escape(number[1]) + r'(?!\.)', text)) != 1:
            continue
        candidates = {}
        for nw in words:
            if nw[4] != number[1]:
                continue
            nr = read([nw], number_prefix=number[1], standalone_number=True)
            if nr is None or nr[0] != number[1]:
                continue
            for i, word in enumerate(words):
                # A wrap stays in its column and starts on the immediately
                # following line. Prose between the pieces is never skipped.
                if not (0 <= word[1]-nw[3] <= 5 and 0 < nw[0]-word[0] < 170):
                    continue
                anchors = [g for g in glyphs if g['symbol'] == move[1]
                           and abs(g['bbox'][0]-word[0]) <= 2
                           and abs(g['bbox'][1]-word[1]) <= 2]
                if len(anchors) != 1:
                    continue
                if any(w[2] < word[0] and word[0]-w[2] < 170 and abs(w[1]-word[1]) < 2 for w in words):
                    continue
                for count in (1, 2, 3):
                    group = words[i:i+count]
                    if len(group) != count or any(not 0 <= b[0]-a[2] <= 6 or abs(b[1]-a[1]) > 2
                                                  for a,b in zip(group, group[1:])):
                        continue
                    mr = read(group)
                    if mr is None or not mr[0].startswith(move[1]) or not co.valid_notation(number[1]+mr[0]):
                        continue
                    chars = nr[2]+mr[2]
                    key = (number[1]+mr[0], tuple(tuple(c['bbox']) for c in chars))
                    candidates[key] = chars
        if len(candidates) != 1:
            continue
        (replacement, _), chars = next(iter(candidates.items()))
        lines[line] = lines[line][:number.start()] + replacement
        lines[line+1] = lines[line+1][move.end():]
        boxes = [c['bbox'] for c in chars]
        records.append(dict(kind='coordinate', reading='whole_word', style='bold',
                            original_word=number[1]+'\n'+move[0], primary_reading=number[1]+'\n'+move[0],
                            replacement=replacement, changed=True, source_refined=True, joined_line_wrap=True,
                            source_lines=[line, line+1], characters=chars, line=line, profile=profile[0]['id'],
                            bbox=[min(v[0] for v in boxes), min(v[1] for v in boxes),
                                  max(v[2] for v in boxes), max(v[3] for v in boxes)]))
    return '\n'.join(lines)


def unaudited_figures(text, records):
    """Require one textual occurrence and one independently located figurine."""
    matches = [(line, m) for line, value in enumerate(text.split('\n'))
               for m in FIGURE_WORD.finditer(value)]
    targets = []
    for line, match in matches:
        if co.valid_notation(match[0].translate(str.maketrans('асе', 'ace'))):
            continue
        prefix = match[1] + match[2]
        if sum(m[1] + m[2] == prefix for _, m in matches) != 1:
            continue
        if any(r.get('kind') == 'coordinate' and r.get('line') == line
               and r.get('replacement', '').startswith(prefix) for r in records):
            continue
        anchors = [r for r in records if r.get('symbol') == match[2]
                   and r.get('replacement', '').startswith(prefix)
                   and NUMBER.match(r.get('replacement', ''))
                   and NUMBER.match(r['replacement']).group() == match[1]]
        if len(anchors) == 1:
            targets.append((line, match, anchors[0]))
    return targets


def refine_unaudited_figures(page, text, records, lang, tessdata, dpi=300):
    """Decode original-page words missed by the stacked-column OCR audit.

    A figurine fixes the source location, but does not supply the move. Every
    character, including the number and destination, must be read from pixels.
    """
    targets = unaudited_figures(text, records)
    if not targets:
        return text
    tp = page.get_textpage_ocr(language=lang, dpi=dpi, full=True, tessdata=tessdata)
    words = page.get_text('words', textpage=tp)
    pix = page.get_pixmap(dpi=dpi, colorspace=co.fitz.csGRAY, alpha=False)
    scale, samples = dpi / 72, pix.samples
    glyphs = [r for r in records if r.get('symbol')]
    profile = load_profile(True)
    lines = text.split('\n')
    for line, target, anchor in targets:
        ax0, ay0, ax1, ay1 = anchor['bbox']
        candidates = {}
        for i, word in enumerate(words):
            if not word[4].startswith(target[1]):
                continue
            for count in (1, 2, 3):
                group = words[i:i + count]
                if len(group) != count or any(
                        not 0 <= b[0] - a[2] <= 6
                        or min(a[3], b[3]) - max(a[1], b[1]) < .7 * min(a[3] - a[1], b[3] - b[1])
                        for a, b in zip(group, group[1:])):
                    continue
                x0, y0 = min(w[0] for w in group), min(w[1] for w in group)
                x1, y1 = max(w[2] for w in group), max(w[3] for w in group)
                if not (x0 - 1 <= ax0 < ax1 <= x1 + 1 and y0 - 1 <= ay0 < ay1 <= y1 + 1):
                    continue
                box = (max(0, math.floor(x0 * scale) - 1), max(0, math.floor(y0 * scale) - 1),
                       min(pix.width, math.ceil(x1 * scale) + 1), min(pix.height, math.ceil(y1 * scale) + 1))
                decoded = co.decode(samples, pix.stride, box, glyphs, dpi, profile)
                if (decoded is None or not co.valid_notation(decoded[0])
                        or not decoded[0].startswith(target[1] + target[2])):
                    continue
                key = (decoded[0], tuple(tuple(c['bbox']) for c in decoded[2]))
                candidates[key] = decoded, box
        if len(candidates) != 1 or lines[line].count(target[0]) != 1:
            continue
        decoded, box = next(iter(candidates.values()))
        lines[line] = lines[line].replace(target[0], decoded[0], 1)
        records.append(dict(kind='coordinate', reading='whole_word', style='bold',
                            original_word=target[0], primary_reading=target[0], replacement=decoded[0],
                            changed=target[0] != decoded[0], source_refined=True, located_figurine=True,
                            bbox=[v / scale for v in box], characters=decoded[2],
                            line=line, profile=profile[0]['id']))
    return '\n'.join(lines)


def number_fragment(record, text):
    lines = text.split('\n')
    line = record.get('line', -1)
    word = record.get('replacement', '')
    number = NUMBER.match(word)
    if number is None or not 0 <= line < len(lines):
        return None
    pattern = re.compile(r'(?<!\S)(\d{1,3})[ \t]+' + re.escape(word) + r'(?!\S)')
    matches = list(pattern.finditer(lines[line]))
    if len(matches) != 1:
        return None
    bare, tail = matches[0][1], number.group().rstrip('.')
    numbers = {bare + tail}
    if bare.endswith(tail):
        numbers.add(bare)  # overlapping OCR boxes: "17 7.Ne4"
    numbers = {n for n in numbers if len(tail) < len(n) <= 3}
    return (pattern, matches[0][0], numbers) if numbers else None


def pending(record, text=''):
    return (record.get('kind') == 'coordinate' and record.get('style') == 'bold'
            and NUMBER.match(record.get('replacement', '')) is not None
            and (record.get('reading') == 'number_only' or number_fragment(record, text) is not None))


def square_fragment(record, line, records):
    if record['reading'] != 'number_only':
        return None
    pattern = re.compile(r'(?<!\S)' + re.escape(record['replacement'])
                         + r'[ \t]+([a-hiасеЬь0-9@¢#!?+.-]{1,6})(?!\S)')
    matches = list(pattern.finditer(line))
    if len(matches) != 1 or not re.search('[1-8]', matches[0][1]):
        return None
    tail = matches[0][1]
    if any(r is not record and r.get('line') == record['line']
           and r.get('replacement') == tail and r.get('reading') == 'whole_word' for r in records):
        return None
    return pattern, matches[0][0], tail


def load_profile(figurines):
    data, templates = co.load_profile(include_figurines=figurines)
    extra = json.loads(PROFILE.read_text(encoding='utf-8'))
    if extra['schema_version'] != 1 or extra['bitmap_size'] != 32:
        raise ValueError('Unsupported bold source profile')
    templates.extend((r['char'], r['width_pt'], r['height_pt'], int(r['bitmap'], 16))
                     for r in extra['templates']
                     if figurines or not any(ch in r['char'] for ch in '♔♕♖♗♘'))
    return {**data, 'id': data['id'] + '+' + extra['id']}, templates


def unlocated(text):
    return [(line, m) for line, value in enumerate(text.split('\n')) for m in SPLIT_DOT.finditer(value)]


def refine_split_dot(page, text, records, lang, tessdata, dpi=300):
    """Locate split number/dot words independently, requiring their full pixels."""
    targets = unlocated(text)
    if not targets:
        return text
    tp = page.get_textpage_ocr(language=lang, dpi=dpi, full=True, tessdata=tessdata)
    words = page.get_text('words', textpage=tp)
    pix = page.get_pixmap(dpi=dpi, colorspace=co.fitz.csGRAY, alpha=False)
    scale, samples = dpi/72, pix.samples
    glyphs = [r for r in records if r.get('symbol')]
    profile = load_profile(bool(glyphs))
    lines = text.split('\n')
    for line, target in targets:
        candidates = {}
        square = re.search(r'[a-hасе][1-8]', target[2])[0].translate(str.maketrans('асе', 'ace'))
        for i, w in enumerate(words):
            if not re.match(re.escape(target[1])+r'(?:\.|$)', w[4]):
                continue
            for count in (1, 2, 3):
                group = words[i:i+count]
                if len(group) != count or any(
                        not 0 <= b[0]-a[2] <= 6 or min(a[3],b[3])-max(a[1],b[1]) < 0.7*min(a[3]-a[1],b[3]-b[1])
                        for a,b in zip(group, group[1:])):
                    continue
                box = (max(0, math.floor(min(w[0] for w in group)*scale)-1),
                       max(0, math.floor(min(w[1] for w in group)*scale)-1),
                       min(pix.width, math.ceil(max(w[2] for w in group)*scale)+1),
                       min(pix.height, math.ceil(max(w[3] for w in group)*scale)+1))
                decoded = co.decode(samples, pix.stride, box, glyphs, dpi, profile)
                number = NUMBER.match(decoded[0]) if decoded else None
                if number is None or number.group() != target[1]+'.':
                    continue
                if re.sub(r'[!?+#.,]+$', '', decoded[0]).endswith(square):
                    # Different overlapping word windows are one source only
                    # when the decoded character locations are identical.
                    key = (decoded[0], tuple(tuple(c['bbox']) for c in decoded[2]))
                    candidates[key] = decoded, box
        if len(candidates) != 1 or lines[line].count(target[0]) != 1:
            continue
        decoded, box = next(iter(candidates.values()))
        lines[line] = lines[line].replace(target[0], decoded[0], 1)
        records.append(dict(kind='coordinate', reading='whole_word', style='bold',
                            original_word=target[0], primary_reading=target[0], replacement=decoded[0],
                            changed=True, source_refined=True, located_split_dot=True,
                            bbox=[v/scale for v in box], characters=decoded[2],
                            line=line, profile=profile[0]['id']))
    return '\n'.join(lines)


def decode_rescaled(pix, rect, glyphs, dpi, profile, views=None):
    """Require two complete readings of the same source crop at fixed scales.

    Resampling is supporting evidence, not independent source material. Never
    lower character thresholds or use board legality to choose a reading.
    Character boxes are returned in the original page's point coordinates.
    """
    readings, audit = [], []
    scale = dpi / 72
    views = {} if views is None else views
    with co.fitz.open() as doc:
        page = doc.new_page(width=pix.width / scale, height=pix.height / scale)
        page.insert_image(page.rect, pixmap=pix)
        for target_dpi in (360, 450):
            # Render with the page's fixed origin: clipped rendering changes
            # MuPDF's resampling phase. Decode only the bounded word region.
            if target_dpi not in views:
                views[target_dpi] = page.get_pixmap(dpi=target_dpi, colorspace=co.fitz.csGRAY, alpha=False)
            view = views[target_dpi]
            factor = target_dpi / 72
            box = (max(0, math.floor(rect[0] / scale * factor)),
                   max(0, math.floor(rect[1] / scale * factor)),
                   min(view.width, math.ceil(rect[2] / scale * factor)),
                   min(view.height, math.ceil(rect[3] / scale * factor)))
            decoded = co.decode(view.samples, view.stride, box, glyphs, target_dpi, profile)
            if decoded is None:
                return None, []
            word, scores, characters = decoded
            readings.append((word, scores, characters))
            audit.append(dict(dpi=target_dpi, replacement=word, characters=characters))
    if readings[0][0] != readings[1][0]:
        return None, []
    return readings[0], audit


def refine_page(pix, text, records, dpi=300):
    """Replace a unique partial word on its audited line, retaining provenance."""
    lines = text.split('\n')
    glyphs = [r for r in records if r.get('symbol')]
    profile = load_profile(bool(glyphs))
    samples, scale = pix.samples, dpi / 72
    scaled_views = {}
    for record in records:
        if not pending(record, '\n'.join(lines)):
            continue
        line, original = record['line'], record['replacement']
        if not 0 <= line < len(lines):
            continue
        fragment = number_fragment(record, '\n'.join(lines))
        square = None if fragment else square_fragment(record, lines[line], records)
        pattern = (fragment or square)[0] if fragment or square else re.compile(r'(?<!\S)' + re.escape(original) + r'(?!\S)')
        if len(list(pattern.finditer(lines[line]))) != 1:
            continue
        # Two audits for the same word are ambiguous too.
        if sum(r.get('line') == line and r.get('replacement') == original
               and r.get('kind') == 'coordinate' for r in records) != 1:
            continue
        x0, y0, x1, y1 = record['bbox']
        rect = (max(0, math.floor(x0*scale)-1), max(0, math.floor(y0*scale)-1),
                min(pix.width, math.ceil(x1*scale)+1), min(pix.height, math.ceil(y1*scale)+1))
        if rect[0] >= rect[2] or rect[1] >= rect[3]:
            continue
        rect = co.isolate_text_band(samples, pix.stride, rect, dpi)
        rect = co.complete_ink_bounds(samples, pix.stride, pix.width, rect, dpi)
        boxes = [rect]
        if fragment:
            # Try only blank-column boundaries nearby, not arbitrary cuts
            # through a glyph. Full pixels must confirm the longer number.
            boxes = []
            ink = lambda x: any(samples[y*pix.stride+x] < 150 for y in range(rect[1], rect[3]))
            for x in range(max(1, rect[0] - round(18*scale)), rect[0]):
                if ink(x) and not ink(x-1):
                    boxes.append((x-1, *rect[1:]))
        elif square:
            boxes = []
            ink = lambda x: any(samples[y*pix.stride+x] < 150 for y in range(rect[1], rect[3]))
            for x in range(rect[2], min(pix.width, rect[2] + round(26*scale))):
                if not ink(x) and ink(x-1):
                    boxes.append((rect[0], rect[1], x+1, rect[3]))
        candidates = {}
        old_number = NUMBER.match(original).group()
        for box in boxes:
            decoded = co.decode(samples, pix.stride, box, glyphs, dpi, profile)
            reread = []
            if decoded is None:
                decoded, reread = decode_rescaled(pix, box, glyphs, dpi, profile, scaled_views)
            if decoded is None or not co.valid_notation(decoded[0]):
                continue
            number = NUMBER.match(decoded[0])
            if number is None:
                continue
            if fragment:
                if (number.group().rstrip('.') not in fragment[2]
                        or number.group().count('.') != old_number.count('.')
                        or record['reading'] == 'whole_word'
                        and decoded[0][len(number.group()):] != original[len(old_number):]):
                    continue
                digits = [ch for ch in decoded[2] if ch['char'].isdigit()]
                if any(b['bbox'][0] - a['bbox'][2] > 4 for a, b in zip(digits, digits[1:len(number.group().rstrip('.'))])):
                    continue
            elif number.group() != old_number:
                continue
            if square:
                move = decoded[0][len(number.group()):]
                marks = re.search(r'[!?+#.]*$', square[2])[0]
                if (re.findall('[1-8]', move) != re.findall('[1-8]', square[2])
                        or re.search(r'[!?+#.]*$', move)[0] != marks):
                    continue
            candidates[decoded[0]] = decoded, box, reread
        if len(candidates) != 1:
            continue
        decoded, rect, reread = next(iter(candidates.values()))
        replacement, _, characters = decoded
        lines[line] = pattern.sub(lambda _: replacement, lines[line])
        record.update(primary_reading=(fragment or square)[1] if fragment or square else original, primary_bbox=list(record['bbox']),
                      primary_characters=record['characters'], source_refined=True,
                      joined_number_fragment=bool(fragment),
                      joined_square_fragment=bool(square),
                      reading='whole_word', replacement=replacement,
                      changed=replacement != record['original_word'], characters=characters,
                      bbox=[v/scale for v in rect], profile=profile[0]['id'])
        if reread:
            record['rescaled_readings'] = reread
    return '\n'.join(lines)
