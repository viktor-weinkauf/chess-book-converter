"""Targeted original-page layout retry; no chess position or game database."""
import re
import math
from statistics import median

import fitz

from book_structure import GAME_HEADING, OPENING, PLAYER_PAIR, section_events

LONG = re.compile(r'\w[—–:-]\w')


def repair_word_boxes(page, words, dpi, records):
    """Tighten double-height OCR boxes using peer lines and actual ink.

    OCR line ids only locate a candidate band. Pixels must independently
    confirm a single text band there. Text and horizontal order never change.
    """
    if not words:
        return words
    height = median(w[3] - w[1] for w in words if w[3] > w[1])
    suspects = [i for i, w in enumerate(words) if w[3] - w[1] > 1.65 * height]
    if not suspects:
        return words
    rows = {}
    for w in words:
        rows.setdefault((w[5], w[6]), []).append(w)
    centres = {}
    for key, row in rows.items():
        peers = [(w[1] + w[3]) / 2 for w in row if .65 * height <= w[3] - w[1] <= 1.4 * height]
        if len(peers) >= 2 and max(peers) - min(peers) < .5 * height:
            centres[key] = median(peers)
    pix = page.get_pixmap(dpi=dpi, colorspace=fitz.csGRAY, alpha=False)
    samples, scale = pix.samples, dpi / 72
    result = list(words)
    for i in suspects:
        word = words[i]
        block, line = word[5:7]
        centre = centres.get((block, line))
        if centre is None:
            before, after = centres.get((block, line - 1)), centres.get((block, line + 1))
            if before is None or after is None or not 1.6 * height < after - before < 3.4 * height:
                continue
            centre = (before + after) / 2
        if not word[1] <= centre <= word[3]:
            continue
        x0, x1 = max(0, math.floor(word[0]*scale)), min(pix.width, math.ceil(word[2]*scale))
        y0, y1 = max(0, math.floor(word[1]*scale)), min(pix.height, math.ceil(word[3]*scale))
        ink = [y for y in range(y0, y1)
               if sum(samples[y*pix.stride+x] < 150 for x in range(x0, x1)) >= 2]
        bands = []
        for y in ink:
            if not bands or y - bands[-1][1] > max(1, round(scale*.5)):
                bands.append([y, y+1])
            else:
                bands[-1][1] = y+1
        candidates = [(a, b) for a, b in bands
                      if .6*height <= (b-a)/scale <= 1.4*height
                      and abs((a+b)/(2*scale)-centre) < .45*height]
        if len(candidates) != 1:
            continue
        a, b = candidates[0]
        box = [word[0], a/scale, word[2], b/scale]
        result[i] = (*box, *word[4:])
        records.append(dict(kind='word_geometry', bbox=box, original_bbox=list(word[:4]),
                            word=word[4], block=block, source_line=line,
                            method='peer-lines-and-ink-v1'))
    return result


def valid_geometry(record):
    return (isinstance(record, dict) and record.get('kind') == 'word_geometry'
            and record.get('method') == 'peer-lines-and-ink-v1'
            and isinstance(record.get('word'), str)
            and all(isinstance(record.get(k), list) and len(record[k]) == 4
                    and all(isinstance(v, (int, float)) and math.isfinite(v) for v in record[k])
                    for k in ('bbox', 'original_bbox')))


def embedded_headings(text):
    """A merged-column heading needs independent names and opening below it.

    This only requests a geometrical reread; it never splits the text itself.
    """
    lines = text.split('\n')
    found = set()
    for i, line in enumerate(lines):
        if GAME_HEADING.match(line):
            continue
        for match in re.finditer(r'(?<=\S)\s+№\s*(\d{1,4})(?=\s|\.|$)', line):
            following = lines[i + 1:i + 25]
            names = next((j for j, value in enumerate(following) if PLAYER_PAIR.match(value)), None)
            if names is not None and OPENING.search('\n'.join(following[names + 1:])):
                found.add(match[1])
    return found


def defects(text):
    lines = text.split('\n')
    events = section_events(lines)
    orphaned = {m[1] for i, line in enumerate(lines)
                if (m := GAME_HEADING.match(line)) and i not in events
                and OPENING.search('\n'.join(lines[i + 1:]))}
    orphaned |= embedded_headings(text)
    mixed = sum(len(re.findall(r'(?<!\w)\d{1,3}\.[ \t]+', line)) >= 2
                and len(LONG.findall(line)) >= 3 for line in lines)
    return orphaned, mixed


def needs_retry(text):
    orphaned, mixed = defects(text)
    return bool(orphaned) or mixed >= 2 or table_backtracks(text) > 0


def table_backtracks(text):
    """A backwards jump requests geometry inspection, never move sorting.

    Count only isolated long-notation rows. Prose and short-notation analysis
    cannot vote. A game heading starts a new sequence.
    """
    previous, count = None, 0
    move = r'[A-Za-zА-Яа-яЁё♔-♟]?[a-hа-н][1-8][—–:-][a-hа-н][1-8][+!?]*'
    for line in text.split('\n'):
        if GAME_HEADING.match(line):
            previous = None
        match = re.match(r'^\s*(\d{1,3})\.\s+(.*)$', line)
        if not match or not re.search(move, match[2]):
            continue
        rest = re.sub(move, '', match[2])
        if re.search(r'[^\s.!?…—–-]', rest):
            continue
        number = int(match[1])
        count += previous is not None and number < previous - 1
        previous = number
    return count


def improved(before, after):
    old_orphans, old_mixed = defects(before)
    new_orphans, new_mixed = defects(after)
    headings = lambda t: {m[1] for s in t.split('\n') if (m := GAME_HEADING.match(s))} | embedded_headings(t)
    # Do not accept an apparently cleaner page merely because a heading vanished.
    if not headings(before) <= headings(after) or not new_orphans <= old_orphans:
        return False
    return (len(new_orphans) < len(old_orphans) or old_mixed >= 2 and new_mixed < old_mixed
            or new_mixed <= old_mixed and table_backtracks(after) < table_backtracks(before))


def refined_gap(words, width, column_gap, visual_lines, crosses):
    """Keep the seam outside ink; find a short table block above an illustration."""
    initial = column_gap(words, width)
    def adjust(gap, subset):
        return min(range(gap - 4, gap + 5),
                   key=lambda x: (sum(crosses(w, x) for w in subset), abs(x - gap)))
    if initial is not None:
        return adjust(initial, words)
    lines = visual_lines(words)
    candidates = []
    for size in (8, 12, 16):
        for start in range(0, max(1, len(lines) - size + 1), 4):
            block = lines[start:start + size]
            subset = [w for line in block for w in line]
            gap = column_gap(subset, width)
            if gap is None:
                continue
            gap = adjust(gap, subset)
            # Require actual numbered long-notation rows in both columns.
            # Random symbols in a drawing and ordinary prose cannot vote.
            counts = []
            for left in (True, False):
                count = 0
                for line in block:
                    side = [w for w in line if (w[2] <= gap if left else w[0] >= gap)]
                    text = ' '.join(w[4] for w in side)
                    count += bool(re.match(r'^\d{1,3}\.[ \t]+', text) and LONG.search(text))
                counts.append(count)
            if min(counts) >= 2:
                candidates.append((min(counts), sum(counts), -abs(gap - width/2), gap))
    return max(candidates)[-1] if candidates else None
