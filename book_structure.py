"""Conservative structural cues for book games, not chess-shaped prose."""

import re
from collections import Counter

GAME_HEADING = re.compile(r"^\s*(?:№|N[oеe°º]?\.?|No\.)\s*(\d{1,4})(?:\.(?=\s|$)|(?=\s|$))", re.I)
OCR_GAME_HEADING = re.compile(r"^\s*[MМ]\s+(\d{1,4})\.?(?:\s+\S|\s*$)")
CREDIT = re.compile(r"^\s*(?:Примечания|Комментарии)\s+(?:[А-ЯЁA-Z]|автора\b)")
CHAPTER = re.compile(r"^\s*(?:Глава\s+(?:[IVXLC\d]+|первая|вторая|третья|четвертая|пятая|шестая|седьмая|восьмая|девятая|десятая)\b|CHAPTER\s+\w+)", re.I)
OPENING = re.compile(r"(?<!\d)1\s*\.\s*(?:[a-hа-с][1-8]|[КKСCBNQR♔-♟])")
SURNAME = r"[A-ZА-ЯЁ][A-Za-zА-Яа-яЁё'’\-]{1,30}"
PLAYER = rf"(?:[A-ZА-ЯЁ]\.\s*){{0,2}}{SURNAME}"
PLAYER_PAIR = re.compile(rf"^\s*({PLAYER})\s+(?:[—–-]\s+)?({PLAYER})\s*$")
DRAW_LINE = re.compile(r"^\s*(?:Ничья|Согласились на ничью|Draw|Draw agreed|Remis)[.!]?\s*$", re.I)


def clean_running_headers(pages):
    """Remove repeated top/bottom furniture only; keep page and line counts stable."""
    split = [text.split("\n") for text in pages]
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
            if (len(value) < 80 and not GAME_HEADING.match(value) and not re.search(r"\d+\s*\.", value)
                    and not re.search(r"сдал|resign|ничья|draw", value, re.I)
                    and (CHAPTER.match(value) or (counts[value] >= 2 and len(value.split()) >= 2))):
                lines[i] = ""
    return ["\n".join(lines) for lines in split]


def section_events(lines):
    events = {}
    for i, line in enumerate(lines):
        heading = GAME_HEADING.match(line)
        if not heading:
            possible = OCR_GAME_HEADING.match(line)
            # № -> M is common on the supplied scans. Require the independent
            # player-name line too; a bare M/number in prose is not a boundary.
            if possible and any(PLAYER_PAIR.match(next_line) for next_line in lines[i + 1:i + 5]):
                heading = possible
        # Headings must be followed by an opening, not a table of contents entry.
        following = " ".join(lines[i + 1:i + 25])
        diagram_game = (("__BOARD_" in following or re.search(r"(?:[rnbqkpRNBQKP1-8]+/){7}\S+ [wb] ", following)) and
                        any(PLAYER_PAIR.match(s) for s in lines[i+1:i+5]))
        if heading and (OPENING.search(following) or diagram_game):
            events[i] = ("game_boundary", heading.group(1))
        elif DRAW_LINE.match(line):
            events[i] = ("game_result", "1/2-1/2")
        elif CREDIT.match(line) or CHAPTER.match(line):
            events[i] = ("game_end", line)
    return events
