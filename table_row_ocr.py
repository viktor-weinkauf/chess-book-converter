"""Read damaged black-only long-notation rows from their source pixels.

No board or reference moves are consulted. English OCR reads Latin coordinates;
the piece letter is retained from the primary reading, never inferred here.
"""
import math
import re

import fitz


TARGET = re.compile(r'^\s*(\d{1,3})\.\s+([^\s\d]{1,4})\s+'
                    r'([КФЛСKQRBN])([a-hасе][1-8])([—–:-])([a-hасе][1-8])([+!?-]*)\s*$')
LATIN = str.maketrans('асе', 'ace')


def targets(text):
    found = [(i, m) for i, line in enumerate(text.split('\n'))
             if (m := TARGET.fullmatch(line)) and re.search(r'\w', m[2])]
    return [(i, m) for i, m in found if sum(n[1] == m[1] for _, n in found) == 1]


def parse_reading(text):
    # Permit a misread Cyrillic piece glyph, but consume the complete row.
    match = re.fullmatch(r'\s*(\d{1,3})\s*\.\s*\.\s*\.\s*'
                         r'[^\s\d]{1,2}?([a-h][1-8])[—–-]([a-h][1-8])([+!?]*)\s*[|\]]?\s*',
                         text)
    return match.groups() if match else None


def agreed_reading(target, readings):
    parsed = [parse_reading(text) for text in readings]
    if len(parsed) != 2 or parsed[0] is None or parsed[0] != parsed[1]:
        return None
    number, origin, destination, marks = parsed[0]
    # The only coordinate correction allowed is an origin file. Everything
    # else, including the check/annotation, must already agree with the source.
    if (number != target[1] or origin[1] != target[4][1]
            or destination != target[6].translate(LATIN) or marks != target[7].replace('--', '+')):
        return None
    return f'{number}. ... {target[3]}{origin}{target[5]}{destination}{marks}'


def read_crop(page, box, tessdata):
    readings = []
    for dpi in (300, 400):
        pix = page.get_pixmap(clip=box, dpi=dpi, colorspace=fitz.csGRAY, alpha=False)
        with fitz.open('png', pix.tobytes('png')) as image:
            with fitz.open('pdf', image.convert_to_pdf()) as doc:
                crop = doc[0]
                tp = crop.get_textpage_ocr(language='eng', dpi=dpi, full=True, tessdata=tessdata)
                readings.append(crop.get_text(textpage=tp))
    return readings


def refine_page(page, text, lang, tessdata, visual_lines, column_gap, records):
    pending = targets(text)
    if not pending:
        return text
    tp = page.get_textpage_ocr(language=lang, dpi=300, full=True, tessdata=tessdata)
    words = page.get_text('words', textpage=tp)
    gap = column_gap(words, page.rect.width)
    columns = ([words] if gap is None else
               [[w for w in words if w[2] <= gap], [w for w in words if w[0] >= gap]])
    rows = [row for col in columns for row in visual_lines(col)]
    lines = text.split('\n')
    for line, target in pending:
        matches = [row for row in rows if row[0][4] == target[1] + '.'
                   and len(row) == 3 and re.search(r'[—–-]', row[-1][4])]
        if len(matches) != 1:
            continue
        row = matches[0]
        number = row[0]
        # Use the number's vertical band: damaged dots often have a tall OCR
        # box extending into the next prose line. Frame the row with whitespace.
        box = fitz.Rect(max(0, math.floor(number[0]/5)*5-5), max(0, math.floor(number[1])),
                        min(page.rect.width, math.ceil(row[-1][2]/5)*5+5),
                        min(page.rect.height, math.ceil(number[3])+1))
        if any(fitz.Rect(w[:4]).intersects(box) and w not in row
               and fitz.Rect(w[:4]).get_area() > 0
               and (fitz.Rect(w[:4]) & box).get_area() > .2*fitz.Rect(w[:4]).get_area()
               for w in words):
            continue
        readings = read_crop(page, box, tessdata)
        replacement = agreed_reading(target, readings)
        if replacement is None:
            continue
        records.append(dict(code='source_black_row', severity='info', line=line,
                            bbox=list(box), original=lines[line], replacement=replacement,
                            readings=[dict(dpi=dpi, text=value) for dpi, value in zip((300, 400), readings)],
                            message='Two source-crop readings agree on the printed ellipsis and Latin coordinates; the original piece letter is retained.'))
        lines[line] = replacement
    return '\n'.join(lines)

