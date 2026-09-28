"""Conservative structural cues for book games, not chess-shaped prose."""

import re
from collections import Counter

GAME_HEADING = re.compile(r"^\s*(?:№|N[oеe°º]?\.?|No\.)\s*(\d{1,4})(?:\.(?=\s|$)|(?=\s|$))", re.I)
OCR_GAME_HEADING = re.compile(r"^\s*[MМ]\s+(\d{1,4})\.?(?:\s+\S|\s*$)")
CREDIT = re.compile(r"^\s*(?:Примечания|Комментарии)\s+(?:[А-ЯЁA-Z]|автора\b)")
CHAPTER = re.compile(r"^\s*(?:Глава\s+(?:[IVXLC\d]+|первая|вторая|третья|четвертая|пятая|шестая|седьмая|восьмая|девятая|десятая)\b|CHAPTER\s+\w+)", re.I)
OPENING = re.compile(r"(?<!\d)1\s*\.\s*(?:[a-hа-с][1-8]|[КKСCBNQR♔-♟])")
SURNAME = r"[A-ZА-ЯЁ][A-Za-zА-Яа-яЁё'’\-]{1,30}"
PLAYER = rf"(?:[A-ZА-ЯЁ]\.\s*){{0,2}}(?:(?:[Vv]an|[Vv]on|[Dd]e|[Вв]ан|[Дд]е|[Фф]он)\s+)?{SURNAME}"
PLAYER_PAIR = re.compile(rf"^\s*({PLAYER})\s+(?:[—–-]\s+)?({PLAYER})\s*$")
DRAW_LINE = re.compile(r"^\s*(?:Ничья|Согласились на ничью|Draw|Draw agreed|Remis)[.!]?\s*$", re.I)
PLAIN_GAME_HEADING = re.compile(r"^\s*(\d{1,3})\.\s+[A-ZА-ЯЁ][^\d\n]{3,90}$")


def plain_game_heading(lines, index):
    """An opening title without № needs an independent player-name line.

    Requiring the very next nonempty line avoids mistaking numbered analysis
    or a contents entry for a game. The first moves may follow a long preface.
    """
    match = PLAIN_GAME_HEADING.match(lines[index])
    following = next((s for s in lines[index + 1:index + 4] if s.strip()), "")
    return match if match and PLAYER_PAIR.match(following) else None


def clean_running_headers(pages):
    """Remove repeated top/bottom furniture only; keep page and line counts stable."""
    split = [text.split("\n") for text in pages]
    markers = []
    for page, lines in enumerate(split):
        for line, value in enumerate(lines[:2]):
            marker = re.fullmatch(r'\s*(?:(\d{1,4})\s+[–—-]\s+(.+)|(.+?)\s+[–—-]\s+(\d{1,4}))\s*', value)
            if marker:
                number, title = (int(marker[1]), marker[2]) if marker[1] else (int(marker[4]), marker[3])
                if (len(title.split()) >= 2 and not PLAYER_PAIR.match(title)
                        and not re.search(r'\d|сдал|resign|ничья|draw', title, re.I)):
                    markers.append((page, line, number))
    # Alternating chapter titles need not repeat. The printed page counter
    # and wide-gap separator must agree with a nearby page's counter.
    for page, line, number in markers:
        if any(0 < abs(other - page) <= 2 and other_number - number == other - page
               for other, _, other_number in markers):
            split[page][line] = ''
    def key(value):
        return re.sub(r"^\d+\s*[–—-]?\s*|\s*[–—-]?\s*\d+$", "", value).strip()
    candidates = []
    for lines in split:
        positions = list(range(min(2, len(lines))))
        candidates.append({key(lines[i].strip()) for i in positions if lines[i].strip()})
    counts = Counter(value for values in candidates for value in values)
    for lines in split:
        positions = list(range(min(2, len(lines))))
        for i in positions:
            value = key(lines[i].strip())
            # Never remove notation or game headings just because they repeat.
            if (len(value) < 80 and not GAME_HEADING.match(value) and not PLAIN_GAME_HEADING.match(lines[i])
                    and not PLAYER_PAIR.match(value)
                    and not re.search(r"\d+\s*\.", value)
                    and not re.search(r"сдал|resign|ничья|draw", value, re.I)
                    and (CHAPTER.match(value) or (counts[value] >= 2 and len(value.split()) >= 2))):
                lines[i] = ""
    return ["\n".join(lines) for lines in split]


def section_events(lines):
    events = {}
    plain = {i: heading for i in range(len(lines)) if (heading := plain_game_heading(lines, i))}
    for i, line in enumerate(lines):
        heading = GAME_HEADING.match(line)
        if not heading:
            possible = OCR_GAME_HEADING.match(line)
            # № -> M is common on the supplied scans. Require the independent
            # player-name line too; a bare M/number in prose is not a boundary.
            if possible and any(PLAYER_PAIR.match(next_line) for next_line in lines[i + 1:i + 5]):
                heading = possible
        if not heading:
            heading = plain.get(i)
        # A confirmed title/name block may be separated from the opening by
        # introductory prose or a page break. Do not cross the next title.
        end = min(i + (160 if i in plain else 25), len(lines))
        end = next((j for j in range(i + 1, end)
                    if j in plain or GAME_HEADING.match(lines[j]) or CHAPTER.match(lines[j])), end)
        following = " ".join(lines[i + 1:end])
        diagram_game = (("__BOARD_" in following or re.search(r"(?:[rnbqkpRNBQKP1-8]+/){7}\S+ [wb] ", following)) and
                        any(PLAYER_PAIR.match(s) for s in lines[i+1:i+5]))
        opening = OPENING.search(following)
        if i in plain:
            # OCR often reads the row number 1 as a capital I and leaves a dash.
            opening = opening or re.search(r"(?:^|\s)[1IІl]\s*\.\s*(?:[—–-]\s*)?(?:[a-hа-с][1-8]|[КKСCBNQR])", following)
            # Damaged coordinates ("1. 42—04 47—45") or an excerpt starting
            # later still identify a game. Require several separate long rows.
            long_rows = sum(bool(re.match(r"^\s*\d{1,3}\.\s+.*\w[—–:-]\w", s))
                            for s in lines[i + 1:end])
            opening = opening or long_rows >= 2
        if heading and (opening or diagram_game):
            events[i] = ("game_boundary", heading.group(1))
        elif DRAW_LINE.match(line):
            events[i] = ("game_result", "1/2-1/2")
        elif CREDIT.match(line) or CHAPTER.match(line):
            events[i] = ("game_end", line)
    return events
