"""Targeted original-page layout retry; no chess position or game database."""
import re

from book_structure import GAME_HEADING, OPENING, section_events

LONG = re.compile(r'\w[—–:-]\w')


def defects(text):
    lines = text.split('\n')
    events = section_events(lines)
    orphaned = {m[1] for i, line in enumerate(lines)
                if (m := GAME_HEADING.match(line)) and i not in events
                and OPENING.search('\n'.join(lines[i + 1:]))}
    mixed = sum(len(re.findall(r'(?<!\w)\d{1,3}\.[ \t]+', line)) >= 2
                and len(LONG.findall(line)) >= 3 for line in lines)
    return orphaned, mixed


def needs_retry(text):
    orphaned, mixed = defects(text)
    return bool(orphaned) or mixed >= 2


def improved(before, after):
    old_orphans, old_mixed = defects(before)
    new_orphans, new_mixed = defects(after)
    headings = lambda t: {m[1] for s in t.split('\n') if (m := GAME_HEADING.match(s))}
    # Do not accept an apparently cleaner page merely because a heading vanished.
    if not headings(before) <= headings(after) or not new_orphans <= old_orphans:
        return False
    return len(new_orphans) < len(old_orphans) or old_mixed >= 2 and new_mixed < old_mixed


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
