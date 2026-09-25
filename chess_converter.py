"""Chess Book Converter: PDF/DjVu chess books <-> PGN.

Book -> PGN reads the text layer of the book and finds games in it.
Pages without text (scans) are read with OCR (Tesseract inside PyMuPDF).
PGN -> Book writes a PDF (or DjVu) with moves, comments and diagrams.

Run without arguments for the window, or from the command line:
    python chess_converter.py INPUT OUTPUT [--lang auto|en|ru|de] [--ocr auto|always|off]
                                           [--pieces letters|figurines|russian]
"""

import argparse
import hashlib
import html
import io
import os
import queue
import re
import shutil
import subprocess
import sys
import tempfile
import threading
from concurrent.futures import FIRST_COMPLETED, ProcessPoolExecutor, wait
from functools import lru_cache
from pathlib import Path

import chess
import chess.pgn
import chess.svg
import fitz  # PyMuPDF

APP_TITLE = "Chess Book Converter"
DJVU_DIRS = [r"C:\Program Files (x86)\DjVuLibre", r"C:\Program Files\DjVuLibre"]
FONT_DIR = r"C:\Windows\Fonts"
BOOK_TYPES = (".pdf", ".djvu", ".djv")
MIN_PLIES = 6          # shorter "games" are usually move mentions in the text
GAME_TIMEOUT = 400     # tokens without a move before a game counts as finished
DJVU_DPI = 300
TESSDATA_DIRS = [Path(__file__).resolve().parent / "tessdata",
                 Path(r"C:\Program Files\Tesseract-OCR\tessdata")]
OCR_DPI = 300
OCR_CACHE_VERSION = 4  # raise when OCR or page layout changes, so old cached pages are not used
OCR_LANGS = {"en": "eng", "ru": "rus+eng", "de": "deu+eng"}
# Pages are read in parallel, one thread each: more workers than this did not help
OCR_WORKERS = max(1, min(6, (os.cpu_count() or 3) // 3))


class UserError(Exception):
    """An error the user can fix. Shown without a stack trace."""


class Stopped(UserError):
    """The user pressed Stop."""


# ---------------------------------------------------------------- DjVuLibre

def djvu_tool(name):
    found = shutil.which(name)
    if found:
        return found
    for folder in DJVU_DIRS:
        exe = Path(folder) / f"{name}.exe"
        if exe.exists():
            return str(exe)
    raise UserError("DjVuLibre is not installed.\n"
                    "Install it with: winget install DjVuLibre.DjView")


def run_tool(name, *args, cwd=None):
    result = subprocess.run([djvu_tool(name), *args], cwd=cwd, capture_output=True,
                            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    if result.returncode != 0:
        message = result.stderr.decode("utf-8", "replace").strip()
        raise RuntimeError(f"{name} failed: {message}")
    return result.stdout


# ---------------------------------------------------------------- Book text

def read_book_pages(path, tmp, progress):
    """Return the text of every page ("" for a page without text).
    A DjVu book must already be copied to tmp/book.djvu: DjVuLibre cannot
    open every Unicode path on Windows."""
    if path.suffix.lower() == ".pdf":
        pages = []
        with fitz.open(path) as doc:
            for i, page in enumerate(doc):
                pages.append(page.get_text())
                progress(0.05 * (i + 1) / len(doc), f"Reading page {i + 1} of {len(doc)}")
        return pages
    progress(0.02, "Reading the DjVu text layer")
    count = int(run_tool("djvused", "-e", "n", "book.djvu", cwd=tmp).decode().strip())
    raw = run_tool("djvutxt", "book.djvu", cwd=tmp)
    pages = raw.decode("utf-8", "replace").replace("\r\n", "\n").split("\f")[:count]
    return pages + [""] * (count - len(pages))


# ---------------------------------------------------------------- OCR

def find_tessdata(lang):
    for folder in TESSDATA_DIRS:
        if all((folder / f"{code}.traineddata").exists() for code in lang.split("+")):
            return str(folder)
    raise UserError(f"OCR language files are missing ({lang}).\n"
                    f"Put the .traineddata files in: {TESSDATA_DIRS[0]}\n"
                    "Download: https://github.com/tesseract-ocr/tessdata_best")


def visual_lines(words):
    """Group OCR words into lines by their height on the page, left to right.
    A word joins a line when its middle is close to the line's middle; tall
    letters (Ф) must not stretch a line over the next one."""
    lines = []  # [sum of middles, sum of heights, words]
    for w in sorted(words, key=lambda w: (w[1] + w[3]) / 2):
        middle, height = (w[1] + w[3]) / 2, w[3] - w[1]
        for line in reversed(lines[-4:]):
            count = len(line[2])
            if abs(middle - line[0] / count) <= 0.45 * line[1] / count:
                line[0] += middle
                line[1] += height
                line[2].append(w)
                break
        else:
            lines.append([middle, height, [w]])
    # second pass: every word to the line with the nearest middle (table rows
    # are close together; a word between two rows must not go to the wrong one)
    centres = [line[0] / len(line[2]) for line in lines]
    regrouped = [[] for _ in lines]
    for w in words:
        middle = (w[1] + w[3]) / 2
        regrouped[min(range(len(centres)), key=lambda n: abs(centres[n] - middle))].append(w)
    return [sorted(line, key=lambda w: w[0]) for line in regrouped if line]


def column_gap(words, width):
    """The x of the gap between two text columns, or None for one column.
    A column gap is an empty band about 8 points wide; spaces between words
    are narrower, so they cannot fake a gap."""
    best = None
    for x in range(int(width * 0.35), int(width * 0.65)):
        left = sum(1 for w in words if w[2] <= x - 4)
        right = sum(1 for w in words if w[0] >= x + 4)
        if min(left, right) < 0.2 * len(words):
            continue
        key = (len(words) - left - right, abs(x - width / 2))  # words in the band, then centre
        if best is None or key < best[0]:
            best = (key, x)
    if best is None or best[0][0] > 0.05 * len(words):
        return None
    gap = best[1]
    lines = visual_lines(words)
    both = sum(1 for line in lines if line[0][2] <= gap - 4 and line[-1][0] >= gap + 4
               and not any(w[0] < gap + 4 and w[2] > gap - 4 for w in line))
    if both < max(5, 0.3 * len(lines)):
        return None  # most lines must have text on both sides of the gap
    return gap


def word_regions(words, gap, width):
    """Rectangles of the page in reading order, from OCR word boxes: lines
    across the gap stay in place; in two-column parts the left column comes
    before the right one."""
    regions, column = [], []

    def flush():
        if len(column) == 1:  # one line alone, like "White        Black": keep it whole
            regions.append(fitz.Rect(0, column[0][0], width, column[0][1]))
        elif column:
            top, bottom = column[0][0], column[-1][1]
            regions.append(fitz.Rect(0, top, gap, bottom))
            regions.append(fitz.Rect(gap, top, width, bottom))
        column.clear()

    for line in visual_lines(words):
        top = max(0, min(w[1] for w in line) - 1)
        bottom = max(w[3] for w in line) + 1
        if any(w[0] < gap < w[2] for w in line):
            flush()
            regions.append(fitz.Rect(0, top, width, bottom))
        else:
            column.append((top, bottom))
    flush()
    return regions


def ocr_text(page, lang, tessdata, dpi):
    """OCR a page. OCR mixes two columns line by line, so a two-column page is
    cut up, stacked into one column and read again."""
    textpage = page.get_textpage_ocr(language=lang, dpi=dpi, full=True, tessdata=tessdata)
    words = page.get_text("words", textpage=textpage)
    width = page.rect.width
    gap = column_gap(words, width)
    if gap is None:
        return page.get_text(textpage=textpage)  # one column: OCR's own line order
    regions = word_regions(words, gap, width)
    spacing = 8
    with fitz.open() as doc:
        stacked = doc.new_page(width=width, height=sum(r.height + spacing for r in regions))
        y = 0
        for region in regions:
            stacked.show_pdf_page(fitz.Rect(region.x0, y, region.x1, y + region.height),
                                  page.parent, page.number, clip=region)
            y += region.height + spacing
        textpage = stacked.get_textpage_ocr(language=lang, dpi=dpi, full=True, tessdata=tessdata)
        return lines_text(stacked.get_text("words", textpage=textpage), width)


def ocr_page(job):
    """OCR one page. Runs in a worker process, so it must be a top-level function."""
    kind, path, index, lang, tessdata, tmp = job
    if kind == "pdf":
        with fitz.open(path) as doc:
            return index, ocr_text(doc[index], lang, tessdata, OCR_DPI)
    image = Path(tmp) / f"ocr{index}.pgm"
    run_tool("ddjvu", "-format=pgm", f"-page={index + 1}", f"-scale={OCR_DPI}",
             "book.djvu", image.name, cwd=tmp)
    pix = fitz.Pixmap(str(image))
    with fitz.open() as doc:  # page in points, like a PDF page: the layout rules use points
        scale = 72 / OCR_DPI
        page = doc.new_page(width=pix.width * scale, height=pix.height * scale)
        page.insert_image(page.rect, pixmap=pix)
        text = ocr_text(page, lang, tessdata, OCR_DPI)
    image.unlink()
    return index, text


def lines_text(words, width):
    """Text of OCR words, line by line. A wide gap inside a line (the "White
    Black" heading of a game) becomes " – ", so the player names are found."""
    out = []
    for line in visual_lines(words):
        parts = [line[0][4]]
        for before, word in zip(line, line[1:]):
            parts.append((" – " if word[0] - before[2] > 0.25 * width else " ") + word[4])
        out.append("".join(parts))
    return "\n".join(out)


def detect_ocr_language(kind, path, indexes, tmp, progress):
    """OCR up to 3 sample pages with all languages and guess the book language."""
    step = max(1, len(indexes) // 3)
    samples = indexes[step // 2::step][:3]
    texts = ocr_book(kind, path, samples, "eng+rus+deu", tmp,
                     lambda value, text: progress(0.06, "OCR: finding the book language"))
    text = "\n".join(texts.values())
    # the move notation decides first: a Russian book may have English comments
    notation = {
        # OCR often gives Latin look-alikes: C = С, Kp = Кр, J1 or JT = Л, ® = Ф
        "ru": len(re.findall(r"(?<!\w)(?:Кр|Kp|Ф|®|Л|J1|JT|С|C|К)[a-h]?[1-8]?[:x—–-]?[a-h][1-8]",
                             text)),
        "en": len(re.findall(r"(?<!\w)[QRBN][a-h]?[1-8]?x?[a-h][1-8]", text)),
        "de": len(re.findall(r"(?<!\w)[DTLS][a-h]?[1-8]?x?[a-h][1-8]", text)),
    }
    top = max(notation, key=notation.get)
    if notation[top] >= 5 and notation[top] > 2 * sorted(notation.values())[-2]:
        return top
    letters = [c for c in text if c.isalpha()]
    cyrillic = sum("а" <= c.lower() <= "я" or c in "ёЁ" for c in letters)
    if letters and cyrillic / len(letters) > 0.2:
        return "ru"
    german = sum(text.count(w) for w in ("ä", "ö", "ü", "ß", " und ", " der ", " die ", " nicht "))
    return "de" if german >= 3 else "en"


def ocr_cache(path, lang):
    """Folder and file name start for cached OCR pages of this book: pages read
    once are not read again (after Stop, an error, or a second run)."""
    digest = hashlib.sha1(Path(path).read_bytes()).hexdigest()[:16]
    folder = Path(os.environ.get("LOCALAPPDATA", tempfile.gettempdir())) / "ChessConverter" / "ocr-cache"
    folder.mkdir(parents=True, exist_ok=True)
    return folder, f"v{OCR_CACHE_VERSION}-{digest}-{lang.replace('+', '_')}-{OCR_DPI}"


def ocr_book(kind, path, indexes, lang, tmp, progress):
    """OCR many pages at once in worker processes. Returns {index: text}.
    progress() raises Stopped when the user presses Stop."""
    tessdata = find_tessdata(lang)
    folder, name = ocr_cache(path, lang)
    cached = lambda i: folder / f"{name}-p{i + 1}.txt"
    texts = {i: cached(i).read_text(encoding="utf-8") for i in indexes if cached(i).exists()}
    done_before = len(texts)
    indexes = [i for i in indexes if i not in texts]
    if not indexes:
        return texts
    os.environ.setdefault("OMP_THREAD_LIMIT", "1")  # workers inherit it: no thread fights
    pool = ProcessPoolExecutor(max_workers=min(OCR_WORKERS, len(indexes)))
    try:
        pending = {pool.submit(ocr_page, (kind, str(path), i, lang, tessdata, tmp)) for i in indexes}
        while pending:
            # wake up twice a second, so that Stop works at once
            done, pending = wait(pending, timeout=0.5, return_when=FIRST_COMPLETED)
            for future in done:
                index, text = future.result()
                texts[index] = text
                cached(index).write_text(text, encoding="utf-8")
            total = len(indexes) + done_before
            progress(0.1 + 0.75 * len(texts) / total, f"OCR: page {len(texts)} of {total}")
    except BaseException:
        for process in list(getattr(pool, "_processes", {}).values()):
            process.terminate()  # do not wait for pages that are half read
        pool.shutdown(wait=False, cancel_futures=True)
        raise
    pool.shutdown()
    return texts


SPACES = str.maketrans({"\xa0": " ", "\u2007": " ", "\u2009": " ", "\u202f": " ", "\t": " "})
DASHES = str.maketrans({"—": "-", "–": "-", "−": "-", "‑": "-", "‐": "-", "\xad": "-"})
FEN_RE = re.compile(r"(?:[rnbqkpRNBQKP1-8]{1,8}/){7}[rnbqkpRNBQKP1-8]{1,8}"
                    r" [wb] (?:-|[KQkq]{1,4}) (?:-|[a-h][36])(?: \d+ \d+)?")
NUM_RE = re.compile(r"^(\d{1,3})(\.{1,3}|…)(.*)$")
DOTS = {".", "..", "...", "…"}
DIAGRAM_MARKS = {"[#]", "(D)", "{#}"}


OCR_NUM_RE = re.compile(r"^([0-9ЗзбОоOlI|И]{1,3})([.,]{1,3}|…)(.*)$")
OCR_DIGITS = str.maketrans({"З": "3", "з": "3", "б": "6", "О": "0", "о": "0", "O": "0",
                            "l": "1", "I": "1", "|": "1", "И": "11"})  # "И." is a misread "11."
# a dash touching one side only: "e2— e4", "e2 —e4" (" — " between two moves stays)
OCR_DASH_GAP_RE = re.compile(r"(?<=[\w:])([—–-]) +(?=[\w#{])|(?<=[\w:]) +([—–])(?=[\w#{])")


# "d5 : e4", "7 : gb", "Cd3 :h7?": a space on either side of the colon
OCR_SPACED_CAPTURE_RE = re.compile(r"(?<=\S)(?: +: *| *: +)(?=\S{1,3}[+#!?]*(?:\s|$))")
SPACED_CAPTURE_RE = re.compile(r"([a-hасе][1-8]|[KQRBNКФЛСКрCp]) *: +(?=[a-hасе][1-8])")


def normalize_text(text):
    text = text.replace("\xad\n", "")  # soft hyphen at a line break
    text = SPACED_CAPTURE_RE.sub(r"\1:", text)  # Russian books: "d5 : e4" is one move
    return text.replace("\xad", "-").replace("ꞏ", "·").translate(SPACES)


OCR_SQUARE = r"[a-hасе¢][1-8]"
OCR_GLUED = [  # OCR loses spaces: "dxe56.2c4" is "dxe5 6.2c4", "c4e6" is "c4 e6"
    re.compile(rf"({OCR_SQUARE}[+#!?]*)(\d{{1,3}}[.,]+)"),
    re.compile(rf"({OCR_SQUARE})(0-0)"),
    re.compile(r"(0-0(?:-0)?)(\d{1,3}[.,]+)"),
    re.compile(rf"({OCR_SQUARE}[+#!?]*)(?={OCR_SQUARE}(?![-—–:x]))"),
]
LONE_FIGURINE = set("&%£¢$@§AWZEH29")
SQUARE_START_RE = re.compile(rf"^[x:×]?{OCR_SQUARE}")


def normalize_ocr(text):
    """Undo typical OCR damage in move text."""
    text = OCR_DASH_GAP_RE.sub(lambda m: m.group(1) or m.group(2), text)  # "e2— e4"
    text = OCR_SPACED_CAPTURE_RE.sub(":", text)  # "45 : е4", "Ке4: f6+"
    for _ in range(3):
        for pattern in OCR_GLUED:
            text = pattern.sub(r"\1 \2" if pattern.groups == 2 else r"\1 ", text)
    words = text.split(" ")
    joined = []
    for word in words:  # "& g4": a figurine OCR could not read, split from its square
        if joined and joined[-1] in LONE_FIGURINE and SQUARE_START_RE.match(word):
            joined[-1] += word
        else:
            joined.append(word)
    return " ".join(joined)


REAL_WORD_RE = re.compile(r"[a-zа-яäöüß]{4,}")


def ocr_junk(line):
    """OCR reads a diagram as short junk lines ("{Fel", "[4", "EH"). A line is
    junk if it has no move, no move number, no result and no real word."""
    words = line.split()
    if not words or len(words) > 4:
        return False
    for word in words:
        plain = word.strip("()[]{},;")
        if (REAL_WORD_RE.search(word) or plain.translate(DASHES) in RESULTS
                or OCR_NUM_RE.match(ocr_number(plain)) or NUM_RE.match(plain)
                or (len(plain) >= 2 and move_code(plain))):
            return False
    return True


# "c67?!" is c6?!, but in "Cd3:57?" the 7 is the rank (57 = h7): a square must come before
OCR_QUESTION_RE = re.compile(r"(?:(?<=[a-hасе][1-8])|(?<=[?!]))7(?=[?!7]*[?!]$)")
OCR_CHECK_RE = re.compile(r"(?<=[\w?!])(?:--|-\+|\+\+|-\|-)$")
OCR_EXCLAIM_RE = re.compile(r"(?<=[a-h9][1-8])1(?=[?!]$)|(?<=\?)1$")  # "g31?" = g3!?
OCR_ROOK_RE = re.compile(r"^(?:J[1lIT7|]{0,2}|1[1lI])(?=[a-hx:])")  # "J1d1", "11f3" = Лd1, Лf3


def ocr_number(word):
    """Read a move number with OCR mistakes, like "l2." or "З." (Cyrillic З for 3).
    Also: OCR reads "?" as "7" next to other marks: "c67?!" is "c6?!"."""
    while OCR_QUESTION_RE.search(word):
        word = OCR_QUESTION_RE.sub("?", word, count=1)
    word = OCR_EXCLAIM_RE.sub("!", OCR_ROOK_RE.sub("Л", word))
    word = OCR_CHECK_RE.sub("+", word)  # the check sign read as "--": "f6-+", "Лd7--"
    match = OCR_NUM_RE.match(word)
    if not match:
        return word
    raw = match.group(1)
    if not any(c.isdigit() for c in raw) and raw not in ("З", "з", "б", "И"):
        return word
    return raw.translate(OCR_DIGITS) + match.group(2).replace(",", ".") + match.group(3)


def tokenize(pages, ocr_pages=frozenset(), start=1):
    """Split the book into tokens: (kind, value, page, line).
    Kinds: num (value = (number, black)), word, open, close, fen, diagram.
    ocr_pages holds the page numbers whose text came from OCR; `start` is the
    page number of pages[0] (when only part of the book is converted)."""
    lines, tokens = [], []

    def add_words(chunk, page, line):
        ocr = page in ocr_pages
        for word in chunk.split():
            if ocr:  # books use ( and [ for side lines; OCR makes { } from diagrams
                # "{" at the ends of a word is diagram junk, inside it a misread f ("C:{3")
                word = ocr_number(word.strip("{}").replace("{", "f").replace("}", ""))
                if not word:
                    continue
            if word in DIAGRAM_MARKS:
                tokens.append(("diagram", word, page, line))
                continue
            if word in DOTS and tokens and tokens[-1][0] == "num":
                kind, (num, _), p, l = tokens[-1]
                tokens[-1] = (kind, (num, True), p, l)
                continue
            while word and word[0] in "([{":
                tokens.append(("open", word[0], page, line))
                word = word[1:]
            closes = 0
            while word and word[-1] in ")]},;":
                closes += word[-1] in ")]}"
                word = word[:-1]
            match = NUM_RE.match(word)
            if match:
                tokens.append(("num", (int(match.group(1)), match.group(2) != "."), page, line))
                word = match.group(3)
            if word:
                tokens.append(("word", word, page, line))
            tokens.extend(("close", ")", page, line) for _ in range(closes))

    for page_no, page in enumerate(pages, start):
        for raw in normalize_text(page).split("\n"):
            line_no = len(lines)
            lines.append(raw.strip())  # unchanged, for player names
            if raw.strip().isdigit():  # page number
                continue
            if page_no in ocr_pages:
                if ocr_junk(raw):
                    continue
                raw = normalize_ocr(raw)
            pos = 0
            for match in FEN_RE.finditer(raw):
                add_words(raw[pos:match.start()], page_no, line_no)
                tokens.append(("fen", match.group(0), page_no, line_no))
                pos = match.end()
            add_words(raw[pos:], page_no, line_no)
    return tokens, lines


# ---------------------------------------------------------------- Move reading

FIGURINES = str.maketrans({"♔": "K", "♚": "K", "♕": "Q", "♛": "Q", "♖": "R", "♜": "R",
                           "♗": "B", "♝": "B", "♘": "N", "♞": "N", "♙": "", "♟": ""})
LOOKALIKES = str.maketrans({"а": "a", "с": "c", "е": "e", "Ь": "b", "ь": "b", "х": "x",
                            "×": "x", ":": "x"})
PIECES = {
    "en": [("K", "K"), ("Q", "Q"), ("R", "R"), ("B", "B"), ("N", "N")],
    "ru": [("Кр", "K"), ("Kp", "K"), ("Ф", "Q"), ("Л", "R"), ("С", "B"), ("К", "N")],
    "de": [("K", "K"), ("D", "Q"), ("T", "R"), ("L", "B"), ("S", "N")],
    # OCR reads Cyrillic piece letters as Latin ones: K = knight, C = bishop, ® = queen
    "ru_ocr": [("Кр", "K"), ("Kp", "K"), ("Кp", "K"), ("Kр", "K"), ("Ф", "Q"), ("®", "Q"),
               ("Л", "R"), ("С", "B"), ("C", "B"), ("К", "N"), ("K", "N")],
}
LANG_HINTS = {
    "en": re.compile(r"^[QRBN][a-h]?[1-8]?x?[a-h][1-8]"),
    "ru": re.compile(r"^(Кр|Kp|Ф|Л|С|К)[a-hасе]?[1-8]?[-x:х]?[a-hасе][1-8]"),
    "de": re.compile(r"^[DTLS][a-h]?[1-8]?x?[a-h][1-8]"),
}
CASTLE_RE = re.compile(r"^[0OОо]-[0OОо](-[0OОо])?$")
SAN_RE = re.compile(r"^(?:[KQRBN]?[a-h]?[1-8]?[-x]?[a-h][1-8](?:=?[QRBN])?|O-O(?:-O)?)$")
PAWN_FILES_RE = re.compile(r"^([a-h])x?([a-h])$")
SUFFIX_RE = re.compile(r"^(.*?)([!?+#‡†]*)$")
MOVE_NAGS = {"!": 1, "?": 2, "!!": 3, "??": 4, "!?": 5, "?!": 6}
NAG_TOKENS = {**MOVE_NAGS, "=": 10, "∞": 13, "⩲": 14, "+=": 14, "+/=": 14, "⩱": 15, "=+": 15,
              "=/+": 15, "±": 16, "+/-": 16, "∓": 17, "-/+": 17, "+-": 18, "-+": 19}
RESULTS = {"1-0": "1-0", "0-1": "0-1", "1/2-1/2": "1/2-1/2", "½-½": "1/2-1/2",
           "1:0": "1-0", "0:1": "0-1", "½:½": "1/2-1/2", "½": "1/2-1/2", "*": "*",
           "%:%": "1/2-1/2", "%-%": "1/2-1/2"}  # OCR reads ½ as %


def detect_languages(tokens):
    """Order the notation languages by how often their piece letters appear."""
    score = {lang: 0 for lang in LANG_HINTS}
    for kind, value, *_ in tokens:
        if kind == "word":
            for lang, hint in LANG_HINTS.items():
                if hint.match(value):
                    score[lang] += 1
    return sorted(score, key=lambda lang: -score[lang])


def to_english(core, lang):
    text = core.translate(FIGURINES)
    for src, dst in PIECES[lang]:
        if text.startswith(src):
            text = dst + text[len(src):]
            break
    for src, dst in PIECES[lang]:
        if dst != "K" and re.search(rf"[18]=?{src}$", text):
            text = text[:-len(src)] + dst
            break
    return text.translate(LOOKALIKES).translate(DASHES)


def parse_move(board, word, langs, fuzzy=False):
    """Return (move, nags) if the word is a legal move on this board.
    fuzzy: the word came from OCR, so also accept the closest legal move."""
    core, suffix = SUFFIX_RE.match(word.rstrip(".,")).groups()
    nag = MOVE_NAGS.get(suffix.replace("+", "").replace("#", "").replace("‡", "").replace("†", ""))
    nags = [nag] if nag else []
    dashed = core.translate(DASHES)
    if CASTLE_RE.match(dashed):
        core = "O-O-O" if dashed.count("-") == 2 else "O-O"
    # OCR: exact reading only in the book's own notation (K is a knight in Russian OCR)
    for lang in langs[:1] if fuzzy else langs:
        san = to_english(core, lang)
        if SAN_RE.match(san):
            try:
                return board.parse_san(san), nags
            except ValueError:
                continue
        match = PAWN_FILES_RE.match(san)
        if match:  # short pawn capture like "ed" in older books
            moves = [m for m in board.legal_moves
                     if board.piece_type_at(m.from_square) == chess.PAWN
                     and chess.square_file(m.from_square) == "abcdefgh".index(match.group(1))
                     and chess.square_file(m.to_square) == "abcdefgh".index(match.group(2))
                     and board.is_capture(m)]
            if len(moves) == 1:
                return moves[0], nags
    if fuzzy:
        move, _ = closest_move(board, core, langs, says_check(suffix))
        if move is not None:
            return move, nags
    return None, []


# OCR mix-ups: characters that look alike get the same code. Unknown symbols
# (often a figurine the OCR could not read) become "?", which matches any piece.
OCR_SHAPES = str.maketrans({
    "К": "K", "k": "K", "к": "K", "р": "p", "С": "c", "C": "c", "с": "c",
    "Ф": "F", "ф": "F", "®": "F", "Ø": "F", "ø": "F", "Л": "L", "л": "L", "В": "B", "Н": "h", "н": "h", "i": "f",
    "а": "a", "Ь": "b", "ь": "b", "е": "e", "ё": "e", "һ": "h",
    "O": "0", "o": "0", "О": "0", "о": "0", "l": "1", "I": "1", "|": "1", "і": "1",
    "З": "3", "з": "3", "б": "6", "т": "7", "t": "f", "q": "g", "¢": "c",
    "Б": "b", "Т": "7", "в": "g", "J": "L", "д": "g",
    "х": "x", "Х": "x", "X": "x", "×": "x", ":": "x",
    "—": "-", "–": "-", "‑": "-", "−": "-", "_": "-", "~": "-",
})
TARGET_PIECES = {
    "en": {"K": "K", "Q": "Q", "R": "R", "B": "B", "N": "N"},
    "ru": {"K": "Кр", "Q": "Ф", "R": "Л", "B": "С", "N": "К"},
    "de": {"K": "K", "Q": "D", "R": "T", "B": "L", "N": "S"},
}
TARGET_PIECES["ru_ocr"] = TARGET_PIECES["ru"]
# Characters OCR often swaps even after OCR_SHAPES: cheaper to change.
OTHER_NOTATION_PENALTY = 0.4
FIGURINE_GUESS_PENALTY = 0.3
UNSURE_PENALTY = 0.2  # a look-alike reading taken although another move looks almost as close
CHECK_PENALTY = 0.6  # the book prints "+", but the move gives no check
# Characters OCR often swaps, and what a swap costs (a full change costs 1).
CONFUSION_COST = {frozenset(pair): 0.4 for pair in (
    "0d", "6b", "68", "38", "56", "17", "14", "08", "06", "ec", "hb", "B8", "S5", "ad", "8b",
    "9g", "1f", "4d", "eg",
    "Fd")}  # Russian OCR: "dg5" = Фg5
# rarer swaps from old Russian print ("Кео" = Кe5, "c7—ch" = c7—c5, "в2—83" = g2—g3):
# a bit dearer, so that "gb" still reads as g6 before g5
CONFUSION_COST.update({frozenset(pair): 0.55 for pair in ("05", "5h", "5b", "35", "8g")})
CONFUSABLE = set(CONFUSION_COST)
LOST_EASILY = {"f": 0.5}  # letters OCR often drops completely ("f5" read as "5")


def shape(text):
    return "".join(c if c.isalnum() or c in "-x" else "?" for c in text.translate(OCR_SHAPES))


# OCR misreads: "0" or "4" for d ("47—45" = d7—d5), "9" for g, "1" for f
# ("2—14" = f2—f4), a final "b" for 6 ("Nab" = Na6), "d" for 4, "g" (в) for 8 ("Лав")
SQUARE_SHAPE_RE = re.compile(r"[a-h?0149][1-8?]|[a-h][bdg]$|0-0")


def move_code(word):
    """The OCR shape of a word if it may be a move (it has something like a
    square in it), else None."""
    code = shape(SUFFIX_RE.match(word.rstrip(".,;")).group(1))
    return code if 2 <= len(code) <= 9 and SQUARE_SHAPE_RE.search(code) else None


def digits_confusable(a, b):
    """Could OCR have read move number a as b? (like 18 -> 16)"""
    a, b = str(a), str(b)
    return len(a) == len(b) and all(x == y or frozenset((x, y)) in CONFUSABLE for x, y in zip(a, b))


@lru_cache(maxsize=500_000)
def distance(token, target):
    """Edit distance; a "?" in the token and OCR look-alikes cost less."""
    lost = [LOST_EASILY.get(b, 1) for b in target]  # cost of a target letter missing in the token
    previous = [0]
    for cost in lost:
        previous.append(previous[-1] + cost)
    for i, a in enumerate(token, 1):
        current = [i]
        for j, b in enumerate(target, 1):
            change = (0 if a == b else 0.3 if a == "?"
                      else CONFUSION_COST.get(frozenset((a, b)), 1))
            current.append(min(previous[j] + 1, current[j - 1] + lost[j - 1],
                               previous[j - 1] + change))
        previous = current
    return previous[-1]


def move_forms(board, move):
    """A move in English short and long notation, without + or #."""
    try:  # the public san() also tests for check, which is slow and not needed here
        san = board._algebraic_without_suffix(move)
    except AttributeError:
        san = board.san(move).rstrip("+#")
    if san.startswith("O-O"):
        return (san,)
    piece = board.piece_at(move.from_square).symbol().upper()
    long = (("" if piece == "P" else piece) + chess.square_name(move.from_square)
            + ("x" if board.is_capture(move) else "-") + chess.square_name(move.to_square)
            + (chess.piece_symbol(move.promotion).upper() if move.promotion else ""))
    return san, long


@lru_cache(maxsize=100_000)
def form_shape(form, lang):
    """The OCR shape of an English move text, printed in a book's notation."""
    letters = TARGET_PIECES.get(lang, TARGET_PIECES["en"])
    return shape(re.sub(r"[KQRBN]", lambda m: letters[m.group(0)], form))


def move_shapes(board, move, lang):
    """How a book may print this move: short and long notation."""
    return {form_shape(form, lang) for form in move_forms(board, move)}


def looks_like(board, move, code, langs):
    """Distance from an OCR word to a move. The book's own notation (langs[0])
    wins ties: in Russian OCR "K" is a knight, in English a king."""
    codes = [(code, 0)]
    if len(code) >= 3 and code[0] not in "abcdefgh" and code[1] in "abcdefghx?":
        # a figurine OCR read as a digit or letter ("2f3", "Wh3"): maybe any piece
        codes.append(("?" + code[1:], FIGURINE_GUESS_PENALTY))
    forms = move_forms(board, move)
    return min(distance(c, form_shape(form, lang)) + extra
               + (0 if n == 0 else OTHER_NOTATION_PENALTY)
               for c, extra in codes for n, lang in enumerate(langs) for form in forms)


def says_check(word):
    return "+" in word or "#" in word[1:]


def ocr_score(board, move, code, langs, check):
    """looks_like, plus a penalty when the book prints a check and the move gives none."""
    score = looks_like(board, move, code, langs)
    if check and not board.gives_check(move):
        score += CHECK_PENALTY
    return score


def candidate_moves(board, word, langs, top=4):
    """The few legal moves an OCR word may stand for, best first: (distance, move)."""
    code = move_code(word)
    if code is None:
        return []
    check = says_check(word)
    scores = sorted((ocr_score(board, move, code, langs, check), i, move)
                    for i, move in enumerate(board.legal_moves))
    return [(d, m) for d, _, m in scores[:top] if d <= max(2, len(code) / 2)]


def closest_move(board, core, langs, check=False):
    """The legal move that looks most like an OCR word, if one clearly wins:
    (move, distance), or (None, None). check: the book prints a + after it."""
    code = move_code(core)
    if code is None:
        return None, None
    scores = sorted((ocr_score(board, move, code, langs, check), i, move)
                    for i, move in enumerate(board.legal_moves))
    if not scores:
        return None, None
    # two characters: allow one look-alike ("93" = g3), not a free change
    limit = 0.45 if len(code) <= 2 else 1 if len(code) <= 4 else 2
    best = scores[0][0]
    if best <= limit and (len(scores) == 1 or scores[1][0] - best >= 0.35):
        return scores[0][2], best
    return None, None


# ---------------------------------------------------------------- Game finder

OCR_GUESS = "(OCR guess: check this move)"
HEADING_NUMBER_RE = re.compile(r"^(?:№|N[eoо°º]?\.?|Nr\.?)$")
TABLE_RESTART_IDLE = 30  # tokens without a move before a table book may start a new game
RESIGNED = {"сдались", "сдался", "resigned", "resigns", "gab", "aufgegeben"}
WHITE_WORDS = {"белые", "white", "weiß", "weiss"}
BLACK_WORDS = {"черные", "чёрные", "black", "schwarz"}
NAMES_RE = re.compile(r"^(.{2,50}?)\s+[-–—]\s+(.{2,60}?)$")
MOVE_TEXT_RE = re.compile(r"\b\d{1,3}\.+\s*[a-hKQRBNOКФЛС♔-♟]")
DATE_RE = re.compile(r"^\d{4}(?:\.(?:\d\d|\?\?)){0,2}$")


class Frame:
    """One line of play: the main line, or a variation in brackets."""

    def __init__(self, base, board):
        self.base = base      # node this line starts from (None: text only)
        self.node = None      # last move played in this line
        self.board = board
        self.expect = True    # a move may come next without a move number
        self.text = []        # book text since the last move


class GameFinder:
    def __init__(self, book_name, langs, keep_text, lines, ocr_pages=frozenset(), ocr_lang="en",
                 table=False):
        self.book_name = book_name
        self.langs = langs
        self.keep_text = keep_text
        self.lines = lines
        self.ocr_pages = ocr_pages
        own = "ru_ocr" if ocr_lang == "ru" else ocr_lang
        self.ocr_langs = [own] + [lang for lang in ("en", "ru", "de") if lang not in (ocr_lang, own)]
        self.guessed = False  # the last repaired move was a guess
        self.repair_fit = 0   # fit of the last repair attempt
        self.token_of = {}    # id(node) -> index of the token the move was read from
        self.position = 0     # index of the token being read
        self.games = []
        self.game = None
        self.stack = []
        self.idle = 0
        self.from_fen = False
        # the book prints games in rows ("12.  Фd1—c2  Крg8—h8"): indexes of row tokens
        self.rows = table or set()
        self.table = bool(table)
        self.after_number = 0  # junk words that may still be skipped after a move number
        self.problems = []     # (page, error) of spots that could not be read
        self.skip_tokens = set()  # words already read as moves out of order

    # -- helpers
    def parse(self, board, word, page):
        if page in self.ocr_pages:
            return parse_move(board, word, self.ocr_langs, fuzzy=True)
        return parse_move(board, word, self.langs)

    def read_ocr(self, board, word, lenient=False):
        """(move, distance) for an OCR word; distance 0 for an exact reading.
        lenient: when two moves look alike (g5 or g6 for "gb"), take the closer
        one with a small extra cost instead of giving up."""
        move, _ = parse_move(board, word, self.ocr_langs[:1])
        if move is not None:
            return move, 0
        core, suffix = SUFFIX_RE.match(word.rstrip(".,")).groups()
        move, d = closest_move(board, core, self.ocr_langs, says_check(suffix))
        if move is None and lenient:
            code = move_code(core)
            best = candidate_moves(board, word, self.ocr_langs, top=1)
            if code and best and best[0][0] <= (0.45 if len(code) <= 2 else 1 if len(code) <= 4 else 2):
                return best[0][1], best[0][0] + UNSURE_PENALTY
        return move, d

    def fitting_moves(self, board, tokens, start, limit=10):
        """How many of the next moves in the book fit after this position,
        and how different from the book text they look: (count, distance)."""
        board = board.copy(stack=False)
        got, cost, depth = 0, 0, 0
        # table books: long comments between the rows do not use up the window
        window = limit * (60 if self.table else 8)
        for index, (kind, value, page, _) in enumerate(tokens[start:start + window], start):
            if kind in ("open", "close"):  # skip side lines
                depth = max(0, depth + (1 if kind == "open" else -1))
                continue
            if depth or kind in ("num", "junk") or (kind == "word" and value.translate(DASHES) in NAG_TOKENS):
                continue
            if self.table and index not in self.rows:
                continue  # table books: comments between the rows
            if kind != "word":
                break
            if move_code(value) is None:
                continue  # a comment word
            if page in self.ocr_pages:
                move, d = self.read_ocr(board, value, lenient=True)
            else:
                (move, _), d = self.parse(board, value, page), 0
            if move is None:
                break
            board.push(move)
            got, cost = got + 1, cost + d
            if got >= limit:
                break
        return got, cost

    def repair(self, frame, tokens, i):
        """OCR garbled a move beyond recognition. Try every legal move and keep
        the one after which the most following book moves make sense."""
        self.repair_fit = 0  # how well the best repair fits; backtrack must beat it
        code = shape(SUFFIX_RE.match(tokens[i][1].rstrip(".,;")).group(1))
        if not 1 <= len(code) <= 8 or not re.search(r"[1-8?]", code):
            return None  # the next moves are checked anyway, so any short word with a digit
        scored = [(*self.fitting_moves(self.after(frame.board, m), tokens, i + 1), m)
                  for m in frame.board.legal_moves]
        best = max((s for s, _, _ in scored), default=0)
        self.repair_fit = best
        if best < 2:
            return None
        # most following moves fit; among those, the following moves need the
        # fewest OCR corrections (after 4.b3 "Сc1—b2" reads exactly, after 4.h3 not)
        cheapest = min(c for s, c, _ in scored if s == best)
        fits = [m for s, c, m in scored if s == best and c <= cheapest + 0.3]
        if len(fits) == 1:
            return fits[0]
        # several fit equally well: take the one that looks most like the word
        ranked = sorted((looks_like(frame.board, m, code, self.ocr_langs), n, m)
                        for n, m in enumerate(fits))
        if ranked[1][0] - ranked[0][0] >= 0.15:  # they fit equally: any clear look decides
            return ranked[0][2]
        closest = [m for d, _, m in ranked if d == ranked[0][0]]
        # same look: take the one as long as the word ("Фаб" is a piece move to a6, not a6)
        lang = self.ocr_langs[0]
        gaps = [(min(abs(len(s) - len(code)) for s in move_shapes(frame.board, m, lang)), n, m)
                for n, m in enumerate(closest)]
        gaps.sort()
        if len(gaps) == 1 or gaps[0][0] < gaps[1][0]:
            return gaps[0][2]
        if best >= 4 and len(fits) <= 3:
            # still a tie, but the game goes on well after any of them: guess
            # like a player would (the move toward the centre, Nf3 before Nh3),
            # so the rest of the game is not lost, and mark the guess
            self.guessed = True
            return max(closest, key=lambda m: -self.off_centre(m.to_square))
        return None

    @staticmethod
    def off_centre(square):
        return abs(chess.square_file(square) - 3.5) + abs(chess.square_rank(square) - 3.5)

    @staticmethod
    def after(board, move):
        board = board.copy(stack=False)
        board.push(move)
        return board

    def backtrack(self, frame, tokens, i, depth=10):
        """OCR read an earlier move as another legal move (like e5 as "еб" = e6),
        so the mistake only shows now. Try another reading for one of the last
        moves, read the moves after it again, and keep the version after which
        the most book moves fit. Returns True if the line was changed."""
        line, node = [], frame.node
        while (node is not None and node is not frame.base and len(line) < depth
               and id(node) in self.token_of):
            line.insert(0, node)
            node = node.parent
        best = None
        for start in range(len(line)):
            part = line[start:]
            board = part[0].parent.board()
            words = [tokens[self.token_of[id(n)]][1] for n in part]
            for cost, first in candidate_moves(board, words[0], self.ocr_langs):
                if first == part[0].move:
                    continue
                after = board.copy(stack=False)
                after.push(first)
                moves = [first]
                for word in words[1:]:
                    move, d = self.read_ocr(after, word)
                    if move is None:
                        break
                    after.push(move)
                    moves.append(move)
                    cost += d
                else:
                    fit, fit_cost = self.fitting_moves(after, tokens, i)
                    # most moves fitting, then closest to the book text, then smallest change
                    key = (fit, -(cost + fit_cost), start)
                    # only change old moves if that fits clearly better than
                    # the best repair of the current move alone
                    if fit > self.repair_fit + 1 and (best is None or key > best[0]):
                        best = (key, part, moves)
        if best is None:
            return False
        self.replace(frame, best[1], best[2])
        return True

    def replace(self, frame, part, moves):
        """Swap the moves of `part` (the end of the frame's line) for `moves`."""
        parent = part[0].parent
        was_main = parent.variations[0] is part[0]
        kept = [(n.comment, set(n.nags), self.token_of[id(n)]) for n in part]
        pending, frame.text = frame.text, []
        parent.remove_variation(part[0])
        frame.node = None if parent is frame.base else parent
        frame.board = parent.board()
        for move, (comment, nags, token) in zip(moves, kept):
            self.play(frame, move, nags)
            frame.node.comment = comment
            self.token_of[id(frame.node)] = token
        first = parent.variation(moves[0])
        if was_main:
            parent.promote_to_main(first)
        frame.text = pending

    def lookahead(self, tokens, start, board, need):
        board = board.copy()
        got = 0
        for kind, value, page, _ in tokens[start:start + need * 3 + 12]:
            if kind != "word":
                continue
            move, _ = self.parse(board, value, page)
            if move is None:
                continue  # comment words between the first moves
            board.push(move)
            got += 1
            if got >= need:
                return True
        return False

    def add_text(self, text, force=False):
        if self.game is not None and (self.keep_text or force):
            self.stack[-1].text.append(text)

    @staticmethod
    def matches(board, num, black):
        return num == board.fullmove_number and black == (board.turn == chess.BLACK)

    # -- game start and end
    def start_game(self, board, page, line):
        self.close()
        self.game = chess.pgn.Game()
        self.from_fen = board != chess.Board()
        if self.from_fen:
            self.game.setup(board)
        self.game.headers["Event"] = self.book_name
        self.game.headers["BookPage"] = str(page)
        self.read_headers(line)
        self.stack = [Frame(self.game, board.copy())]
        self.idle = 0

    def read_headers(self, line):
        """Look for "White - Black" and our "Event · Site · Date" line above the moves."""
        found_names = False
        checked = 0
        for i in range(line - 1, max(line - 12, -1), -1):
            text = self.lines[i]
            if not text or text.isdigit() or FEN_RE.search(text):
                continue  # empty line, page number or diagram caption
            checked += 1
            if checked > 4:
                break
            if MOVE_TEXT_RE.search(text) and "·" not in text:
                break
            if "·" in text:
                parts = [p.strip() for p in text.split("·")]
                if DATE_RE.match(parts[-1]):
                    self.game.headers["Date"] = parts.pop()
                if parts:
                    self.game.headers["Event"] = parts.pop(0)
                if parts:
                    self.game.headers["Site"] = parts.pop(0)
                break
            names = NAMES_RE.match(text)
            if names and len(text.split()) <= 12 and not found_names:
                self.game.headers["White"], self.game.headers["Black"] = names.groups()
                found_names = True
                continue
            if found_names:
                break

    def close(self, result=None):
        if self.game is None:
            return
        root = self.stack[0]
        if result and root.text:
            self.attach(root, root.node)
        plies = sum(1 for _ in self.game.mainline_moves())
        if plies >= MIN_PLIES or (self.from_fen and plies >= 1):
            self.game.headers["Round"] = str(len(self.games) + 1)
            self.game.headers["Result"] = result or "*"
            self.games.append(self.game)
        self.game = None
        self.stack = []

    # -- moves and comments
    def attach(self, frame, node):
        text = " ".join(frame.text).strip()
        frame.text = []
        if not text:
            return
        if node is None:
            node = self.game
        node.comment = f"{node.comment} {text}".strip()

    def play(self, frame, move, nags):
        parent = frame.node if frame.node is not None else frame.base
        pending = " ".join(frame.text).strip()
        frame.text = []
        if frame.node is not None and pending:
            frame.node.comment = f"{frame.node.comment} {pending}".strip()
        new = parent.variation(move) if parent.has_variation(move) else parent.add_variation(move)
        if frame.node is None and pending:
            if parent is self.game and frame is self.stack[0]:
                self.game.comment = f"{self.game.comment} {pending}".strip()
            else:
                new.starting_comment = pending
        new.nags.update(nags)
        self.token_of[id(new)] = self.position
        frame.board.push(move)
        frame.node = new
        frame.expect = frame.board.turn == chess.BLACK
        self.idle = 0

    def run(self, tokens, progress=None):
        total = len(tokens)
        for i in range(total):
            if progress and i % 5000 == 0:
                progress(0.86 + 0.1 * i / max(total, 1), "Looking for games")
            try:
                self.step(tokens, i)
            except Exception as exc:  # one bad spot must not lose the whole book
                self.problems.append((tokens[i][2], repr(exc)))
        self.close()
        return self.games

    def step(self, tokens, i):
        """Read one token."""
        kind, value, page, line = tokens[i]
        self.position = i
        if kind == "junk" or i in self.skip_tokens:
            return
        if self.game is not None:
            self.idle += 1
            if self.idle > GAME_TIMEOUT:
                self.close()
        frame = self.stack[-1] if self.game is not None else None

        if kind == "fen":
            try:
                board = chess.Board(value if value.count(" ") == 5 else value + " 0 1")
            except ValueError:
                self.add_text(value)
                return
            if (frame is not None and len(self.stack) == 1
                    and frame.board.board_fen() == board.board_fen()
                    and frame.board.turn == board.turn):
                self.add_text("[#]", force=True)
            else:
                self.start_game(board, page, line)
            return

        if kind == "num":
            num, black = value
            depth0 = frame is None or (frame.node is not None and len(self.stack) == 1)
            if frame is not None and self.stack[0].node is not None and self.idle >= 20:
                depth0 = True  # a bracket was never closed, but the game has gone quiet
            # "№ 1. Французская защита" is a heading (OCR reads № as "Ne" or "No")
            heading = (i and tokens[i - 1][0] == "word" and tokens[i - 1][3] == line
                       and HEADING_NUMBER_RE.match(tokens[i - 1][1]))
            if self.table and frame is not None and self.idle < TABLE_RESTART_IDLE:
                depth0 = False  # in a table book "1." inside a game is a misread "11."
            if num == 1 and not black and depth0 and not heading and self.lookahead(
                    tokens, i + 1, chess.Board(), 1 if frame is None else 3):
                intro = []  # text before "1." on the same line
                for back in range(i - 1, -1, -1):
                    if tokens[back][0] != "word" or tokens[back][3] != line:
                        break
                    intro.insert(0, tokens[back][1])
                self.start_game(chess.Board(), page, line)
                if intro and self.keep_text:
                    self.game.comment = " ".join(intro)
                return
            if frame is None:
                return
            self.after_number = 0
            mainline = len(self.stack) == 1 and frame.node is not None
            if self.table and mainline and i not in self.rows:
                # table books print the game in rows; a number inside a
                # line belongs to a comment ("лучше 14...Кf8")
                frame.expect = False
                self.add_text(f"{num}{'...' if black else '.'}")
                return
            if self.matches(frame.board, num, black):
                frame.expect = True
            elif (self.table and mainline and not black and num == frame.board.fullmove_number + 1
                  and frame.board.turn == chess.BLACK and self.shifted_black_move(frame, tokens, i)):
                frame.expect = True  # black's move came from this row; now white's move
            elif (self.table and mainline and not black and num == frame.board.fullmove_number
                  and frame.board.turn == chess.BLACK):
                frame.expect = True  # "13.  ...  Лf8—e8": OCR lost the dots
            elif self.table and mainline and self.next_move_fits(frame.board, tokens, i + 1):
                frame.expect = True  # a row start: OCR misread the number ("19." for "12.")
            elif frame.node is None and frame is self.stack[0] and self.from_fen \
                    and black == (frame.board.turn == chess.BLACK):
                frame.board.fullmove_number = num  # book numbering after a diagram
                frame.expect = True
            elif frame.node is None and frame.base is not None and frame is not self.stack[0]:
                frame.expect = self.rebase(frame, num, black)
            elif (page in self.ocr_pages and frame.node is not None
                  and black == (frame.board.turn == chess.BLACK)
                  and digits_confusable(num, frame.board.fullmove_number)):
                frame.expect = True  # OCR misread a digit of the move number
            else:
                frame.expect = False
            if not frame.expect:
                self.add_text(f"{num}{'...' if black else '.'}")
            else:
                self.after_number = 2  # junk OCR may leave for "...": skip up to 2 words
            return

        if frame is None:
            return

        if kind == "open":
            if frame.node is None:
                self.stack.append(Frame(None, frame.board.copy()))
            else:
                board = frame.board.copy()
                board.pop()
                self.stack.append(Frame(frame.node.parent, board))
            return

        if kind == "close":
            if len(self.stack) > 1:
                done = self.stack.pop()
                if done.node is not None:
                    self.attach(done, done.node)
                elif len(" ".join(done.text)) > 2:
                    self.add_text("(" + " ".join(done.text) + ")")
            return

        if kind == "diagram":
            self.add_text("[#]", force=True)
            return

        # kind == "word"
        plain = value.translate(DASHES)
        side = tokens[i - 1][1].lower() if i and tokens[i - 1][0] == "word" else ""
        if plain.lower().strip(".!") in RESIGNED and side in WHITE_WORDS | BLACK_WORDS:
            if frame.text and frame.text[-1].lower() == side:
                frame.text.pop()
            self.close("0-1" if side in WHITE_WORDS else "1-0")  # "Белые сдались."
            return
        if (plain in RESULTS and all(f.node is None for f in self.stack[1:])
                and not (plain == "*" and page in self.ocr_pages)):  # OCR junk has "*"
            self.close(RESULTS[plain])
            return
        if plain in NAG_TOKENS and frame.node is not None and not frame.text:
            frame.node.nags.add(NAG_TOKENS[plain])
            return
        if page in self.ocr_pages and len(plain) == 1 and not plain.isalnum():
            return  # OCR junk like "~" or "«" between moves
        if self.table and len(self.stack) == 1 and i not in self.rows:
            frame.expect = False  # table books: words outside the rows are comments
            self.add_text(value)
            return
        if frame.expect and frame.base is not None:
            move, nags = self.parse(frame.board, value, page)
            if (move is None and self.table and self.after_number and page in self.ocr_pages
                    and len(plain.strip(".")) <= 3 and move_code(value) is None
                    and not re.search(r"[\dЗзбОо]", plain)):  # "КЗ" is a bad move, not junk
                self.after_number -= 1  # "8.  ce  Сf8—e7": OCR junk for "..."
                return
            if move is None and page in self.ocr_pages:
                move = self.repair(frame, tokens, i)
                if (move is None and frame.node is not None and move_code(value)
                        and self.backtrack(frame, tokens, i)):
                    move, nags = self.parse(frame.board, value, page)
                    if move is None:
                        move = self.repair(frame, tokens, i)
            if move is not None:
                self.play(frame, move, nags)
                if self.guessed:
                    frame.node.comment = f"{frame.node.comment} {OCR_GUESS}".strip()
                    self.guessed = False
                return
        frame.expect = False
        self.add_text(value)

    def shifted_black_move(self, frame, tokens, i):
        """Table books: OCR sometimes puts black's move of row 7 into the line
        of row 8 ("7. 0—0" / "8. Сc1—g5 Кg8—f6"). If black's move is missing and
        this row holds a word that fits for black, followed by one that then
        fits for white, play black's move now and skip its word later."""
        row = [k for k in range(i + 1, min(i + 6, len(tokens)))
               if k in self.rows and tokens[k][0] == "word" and tokens[k][3] == tokens[i][3]]
        for k in row:
            move, nags = self.parse(frame.board, tokens[k][1], tokens[k][2])
            if move is None:
                continue
            after = self.after(frame.board, move)
            rest = [n for n in row if n != k]
            if rest and self.parse(after, tokens[rest[0]][1], tokens[rest[0]][2])[0] is not None:
                self.play(frame, move, nags)
                self.token_of[id(frame.node)] = k
                self.skip_tokens.add(k)
                return True
        return False

    def next_move_fits(self, board, tokens, start):
        """Is the next move-like word after a move number a legal move here?"""
        for kind, value, page, _ in tokens[start:start + 5]:
            if kind == "junk":
                continue
            if kind != "word":
                return False
            move, _ = self.parse(board, value, page)
            if move is not None:
                return True
            if move_code(value) is not None or len(value.strip(".")) > 3:
                return False  # a real word, or a move that does not fit
        return False

    def rebase(self, frame, num, black):
        """A variation may branch off one or two moves earlier than the last move."""
        node = frame.base
        for _ in range(3):
            if node.parent is None:
                return False
            node = node.parent
            board = node.board()
            if self.matches(board, num, black):
                frame.base, frame.board = node, board
                return True
        return False


def page_range(options, count):
    """The pages to convert, as 0-based (first, end) for range(), from the 1-based
    first_page / last_page options (empty = from the start / to the end)."""
    first = options.get("first_page") or 1
    last = options.get("last_page") or count
    if first > count:
        raise UserError(f"The book has only {count} pages.")
    if first > last:
        raise UserError("The first page must not be after the last page.")
    return first - 1, min(last, count)


def range_text(first, end, count):
    if (first, end) == (0, count):
        return ""
    pages = f"page {end}" if end - first == 1 else f"pages {first + 1}–{end}"
    return f" ({pages} of {count})"


def plural(n, word):
    return f"{n} {word}" if n == 1 else f"{n} {word}s"


def token_lines(tokens):
    """Index ranges (start, end) of the tokens of each text line."""
    start = 0
    for i in range(1, len(tokens) + 1):
        if i == len(tokens) or tokens[i][3] != tokens[start][3]:
            yield start, i
            start = i


def is_row(tokens, start, end):
    """A table row: a move number first, then one or two moves ("12. Фd1—c2 Крg8—h8")."""
    rest = [t for t in tokens[start + 1:end] if t[0] != "junk"]
    # only moves, or short OCR junk for "..." ("ce", "не."); a comment line that
    # starts with a number ("28. ..bc с неотразимыми угро-") is not a row
    return (tokens[start][0] == "num" and 1 <= len(rest) <= 4
            and all(t[0] == "word" and (row_move(t[1]) or len(t[1].strip(".")) <= 3) for t in rest)
            and any(row_move(t[1]) for t in rest))


def row_move(word):
    """Looks like a move, even a badly read one in long notation ("52—55" = b2—b3)."""
    return bool(move_code(word) or re.search(r"\w[—–:x-]\w", word))


def table_layout(tokens):
    """Does the book print its games in rows, one move number per line, as older
    Russian books do? Then return the token indexes of the rows, else None.
    A row number that lost its dot in OCR ("3" for "8.") becomes a number again."""
    rows = numbers = 0
    for start, end in token_lines(tokens):
        numbers += sum(1 for t in tokens[start:end] if t[0] == "num")
        rows += is_row(tokens, start, end)
    if rows < 5 or rows < 0.3 * numbers:
        return None
    members = set()
    for start, end in token_lines(tokens):
        kind, value, page, line = tokens[start]
        bare = value.translate(OCR_DIGITS) if kind == "word" else ""
        if bare.isdigit() and len(bare) <= 3 and 2 <= end - start <= 3 and \
                any(move_code(t[1]) for t in tokens[start + 1:end]):
            tokens[start] = ("num", (int(bare), False), page, line)
        if tokens[start][0] == "num" and end - start <= 6:
            for k in range(start + 1, end):  # "21. (Ce3—d2": a bracket in a row is OCR junk
                if tokens[k][0] in ("open", "close"):
                    tokens[k] = ("junk",) + tokens[k][1:]
        if is_row(tokens, start, end):
            members.update(range(start, end))
    return members


def book_to_pgn(src, dst, options, progress):
    lang = options.get("lang", "auto")
    ocr = options.get("ocr", "auto")
    kind = "pdf" if src.suffix.lower() == ".pdf" else "djvu"
    with tempfile.TemporaryDirectory() as tmp:
        if kind == "djvu":
            shutil.copyfile(src, Path(tmp) / "book.djvu")
        pages = read_book_pages(src, tmp, progress)
        count = len(pages)
        first, end = page_range(options, count)
        if ocr == "always":
            todo = list(range(first, end))
        elif ocr == "auto":
            todo = [i for i in range(first, end) if not pages[i].strip()]
        else:
            todo = []
        ocr_lang = lang
        if todo:
            if ocr_lang == "auto":
                progress(0.06, "OCR: finding the book language")
                ocr_lang = detect_ocr_language(kind, src, todo, tmp, progress)
            for index, text in ocr_book(kind, src, todo, OCR_LANGS[ocr_lang], tmp, progress).items():
                pages[index] = text
    pages = pages[first:end]
    if not any(page.strip() for page in pages):
        raise UserError("No text found in these pages." +
                        ("" if todo else "\nTurn on OCR to read scanned pages."))
    ocr_pages = frozenset(i + 1 for i in todo)  # real page numbers, from 1
    tokens, lines = tokenize(pages, ocr_pages, start=first + 1)
    langs = detect_languages(tokens) if lang == "auto" else [lang]
    finder = GameFinder(src.stem, langs, options.get("keep_text", True), lines,
                        ocr_pages, ocr_lang if todo else "en", table_layout(tokens))
    try:
        games = finder.run(tokens, progress)
    except Stopped:
        raise
    except Exception as exc:  # keep what was found, instead of losing everything
        finder.problems.append((None, repr(exc)))
        games = finder.games
    if not games:
        raise UserError("No games found in these pages.")
    progress(0.97, "Writing PGN")
    with open(dst, "w", encoding="utf-8", newline="\n") as out:
        for game in games:
            out.write(f"{game}\n\n")
    message = (f"Found {plural(len(games), 'game')} in {plural(len(pages), 'page')}"
               f"{range_text(first, end, count)}.")
    if todo:
        message += (f"\n{len(todo)} pages were read with OCR ({ocr_lang}). "
                    "OCR makes mistakes: check these games in ChessBase.")
    empty = sum(1 for page in pages if not page.strip())
    if empty and not todo:
        message += f"\n{empty} pages have no text (scanned pictures). They were skipped."
    if finder.problems:
        page, error = finder.problems[0]
        where = f" on page {page}" if page else ""
        message += (f"\n{plural(len(finder.problems), 'spot')} could not be read and "
                    f"{'was' if len(finder.problems) == 1 else 'were'} skipped "
                    f"(first{where}: {error}).")
    return message


# ---------------------------------------------------------------- PGN -> book

NAG_TEXT = {1: "!", 2: "?", 3: "!!", 4: "??", 5: "!?", 6: "?!", 10: "=", 13: "∞",
            14: "+=", 15: "=+", 16: "+/-", 17: "-/+", 18: "+-", 19: "-+"}
FIGURINE_HTML = {"K": "♔", "Q": "♕", "R": "♖", "B": "♗", "N": "♘"}
RUSSIAN_LETTERS = {"K": "Кр", "Q": "Ф", "R": "Л", "B": "С", "N": "К"}
DIAGRAM_SPACE = 225     # points a diagram needs: picture, caption and margins
GAME_START_SPACE = 150  # keep a game title together with its first moves
MIN_BLOCK_SPACE = 40    # any text block needs room for at least one line
BOARD_COLORS = {"square light": "#eeeeee", "square dark": "#b4b4b4",
                "margin": "#ffffff", "coord": "#333333"}
CSS = """
@font-face {font-family: Body; src: url(arial.ttf);}
@font-face {font-family: Body; src: url(arialbd.ttf); font-weight: bold;}
@font-face {font-family: Body; src: url(ariali.ttf); font-style: italic;}
@font-face {font-family: Fig; src: url(seguisym.ttf);}
body {font-family: Body; font-size: 10.5pt; line-height: 1.4;}
h2 {font-size: 15pt; margin: 20pt 0 2pt 0;}
p.info {color: #555555; margin: 0;}
p.players {font-weight: bold; font-size: 12pt; margin: 2pt 0 6pt 0;}
p.moves {margin: 4pt 0;}
span.v {color: #444444;}
span.c {color: #1d4f91;}
span.fig {font-family: Fig;}
.n {white-space: nowrap;}
div.diag {text-align: center; margin: 8pt 0 4pt 0;}
p.fen {font-size: 6.5pt; color: #888888; text-align: center; margin: 0 0 6pt 0;}
p.result {font-weight: bold; margin: 4pt 0 0 0;}
"""


def read_pgn(path, progress):
    data = Path(path).read_bytes()
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError:
        text = data.decode("cp1252", "replace")
    stream, games = io.StringIO(text), []
    while True:
        game = chess.pgn.read_game(stream)
        if game is None:
            break
        games.append(game)
        if len(games) % 200 == 0:
            progress(0.05, f"Reading PGN: {len(games)} games")
    if not games:
        raise UserError("No games found in this PGN file.")
    return games


class BookWriter:
    def __init__(self, options):
        self.pieces = options.get("pieces", "letters")
        self.final_diagram = options.get("final_diagram", True)
        self.archive = fitz.Archive(FONT_DIR)
        self.images = 0

    def san(self, san):
        if self.pieces == "figurines":
            return re.sub(r"[KQRBN]", lambda m: f'<span class="fig">{FIGURINE_HTML[m.group(0)]}</span>', san)
        if self.pieces == "russian":
            return re.sub(r"[KQRBN]", lambda m: RUSSIAN_LETTERS[m.group(0)], san)
        return san

    def diagram(self, board, caption):
        svg = chess.svg.board(board, size=300, colors=BOARD_COLORS).encode()
        with fitz.open("svg", svg) as doc:
            png = doc[0].get_pixmap(dpi=150).tobytes("png")
        self.images += 1
        name = f"diagram{self.images}.png"
        self.archive.add(png, name)
        fen = f'<p class="fen">{board.fen()}</p>' if caption else ""
        return f'<div class="diag"><img src="{name}" width="190"/></div>{fen}', DIAGRAM_SPACE

    @staticmethod
    def comment(text):
        text = re.sub(r"\[%[^\]]*\]", "", text).replace("[#]", "").strip()
        return f' <span class="c">{html.escape(text)}</span>' if text else ""

    def line(self, board, node, depth, parts):
        """Write a line starting with `node`; `board` is the position before it."""
        need_number = True
        while True:
            if node.starting_comment:
                parts.append(self.comment(node.starting_comment))
            san = board.san(node.move)
            if board.turn == chess.WHITE:
                number = f"{board.fullmove_number}."
            else:
                number = f"{board.fullmove_number}..." if need_number else ""
            move_nags = "".join(NAG_TEXT[n] for n in sorted(node.nags) if n <= 6 and n in NAG_TEXT)
            # Arial hyphens read back as soft hyphens, so chess text uses en dashes.
            # "nowrap" keeps symbols like "–+" and "O–O–O" on one line.
            move = f"{number}{self.san(san)}{move_nags}".replace("-", "–")
            evals = "".join(f' <span class="n">{NAG_TEXT[n].replace("-", "–")}</span>'
                            for n in sorted(node.nags) if n > 6 and n in NAG_TEXT)
            tag = "b" if depth == 0 else "span"
            parts.append(f' <{tag} class="n">{move}</{tag}>{evals}')
            need_number = False
            alternatives = node.parent.variations[1:] if node.parent.variations[0] is node else []
            board.push(node.move)
            if node.comment:
                parts.append(self.comment(node.comment))
                need_number = True
                if depth == 0 and "[#]" in node.comment:
                    parts.append(self.diagram(board, True))
            if alternatives:
                board.pop()
                for alt in alternatives:
                    sub = []
                    self.line(board.copy(), alt, depth + 1, sub)
                    parts.append(' <span class="v">(' + "".join(sub).strip() + ")</span>")
                board.push(node.move)
                need_number = True
            if not node.variations:
                return
            node = node.variations[0]

    def game_blocks(self, game, index):
        """The game as (html, space needed at the top) blocks. Diagrams are
        separate blocks, because MuPDF shrinks a picture that does not fit."""
        h = game.headers
        info = [h.get(k, "") for k in ("Event", "Site", "Date")]
        info = [x for x in info if x and x != "?" and not x.startswith("????")]
        head = [f"<h2>Game {index}</h2>"]
        if info:
            head.append(f'<p class="info">{html.escape(" · ".join(info))}</p>')
        head.append(f'<p class="players">{html.escape(h.get("White", "?"))} – '
                    f'{html.escape(h.get("Black", "?"))}</p>')
        blocks = [("\n".join(head), GAME_START_SPACE)]
        board = game.board()
        if board != chess.Board():
            blocks.append(self.diagram(board, True))
        moves = []
        if game.comment:
            moves.append(self.comment(game.comment))
        if game.variations:
            self.line(board.copy(), game.variations[0], 0, moves)
        text = []
        for part in moves + [None]:
            if isinstance(part, str):
                text.append(part)
                continue
            if text:
                blocks.append(('<p class="moves">' + "".join(text).strip() + "</p>", 0))
                text = []
            if part is not None:
                blocks.append(part)
        end = game.end()
        if self.final_diagram and end is not game and "[#]" not in end.comment:
            blocks.append(self.diagram(end.board(), False))
        result = h.get("Result", "*")
        blocks.append((f'<p class="result">{result.replace("-", "–")}</p>', 0))
        return blocks

    def write_pdf(self, games, dst, progress):
        blocks = []
        for i, game in enumerate(games, 1):
            blocks += self.game_blocks(game, i)
            if i % 20 == 0 or i == len(games):
                progress(0.6 * i / len(games), f"Laying out game {i} of {len(games)}")
        buffer = io.BytesIO()
        writer = fitz.DocumentWriter(buffer)
        page_rect = fitz.paper_rect("a4")
        area = page_rect + (56, 56, -56, -56)
        device = writer.begin_page(page_rect)
        y = area.y0
        for block_html, space in blocks:
            # MuPDF loses a block that starts where not even one line fits
            if y + max(space, MIN_BLOCK_SPACE) > area.y1 and y > area.y0:
                writer.end_page()
                device = writer.begin_page(page_rect)
                y = area.y0
            story = fitz.Story(html=block_html, user_css=CSS, archive=self.archive)
            while True:
                more, filled = story.place(fitz.Rect(area.x0, y, area.x1, area.y1))
                story.draw(device)
                if not more:
                    y = max(y, fitz.Rect(filled).y1)
                    break
                writer.end_page()
                device = writer.begin_page(page_rect)
                y = area.y0
        writer.end_page()
        writer.close()
        progress(0.65, "Adding page numbers")
        with fitz.open("pdf", buffer.getvalue()) as doc:
            for number, page in enumerate(doc, 1):
                page.insert_text((page_rect.width / 2 - 6, page_rect.height - 28), str(number),
                                 fontsize=9, fontname="helv", color=(0.4, 0.4, 0.4))
            doc.save(dst, garbage=3, deflate=True)


def djvu_text_layer(page, width, height, zoom):
    """Hidden text for one DjVu page, so the DjVu can be searched and read back."""
    def quote(s):
        return '"' + s.replace("\\", "\\\\").replace('"', '\\"') + '"'

    lines = {}
    for w in page.get_text("words"):
        lines.setdefault((w[5], w[6]), []).append(w)
    out = [f"(page 0 0 {width} {height}"]
    for words in lines.values():
        boxes = [(int(w[0] * zoom), int(height - w[3] * zoom),
                  int(w[2] * zoom), int(height - w[1] * zoom)) for w in words]
        out.append(f" (line {min(b[0] for b in boxes)} {min(b[1] for b in boxes)} "
                   f"{max(b[2] for b in boxes)} {max(b[3] for b in boxes)}")
        for w, b in zip(words, boxes):
            out.append(f"  (word {b[0]} {b[1]} {b[2]} {b[3]} {quote(w[4])})")
        out[-1] += ")"
    out[-1] += ")"
    return "\n".join(out) + "\n"


def pdf_to_djvu(pdf_path, dst, progress, start=0.7, options=None):
    """Pages become grey pictures, with the PDF text as a hidden text layer.
    options may hold first_page / last_page."""
    with tempfile.TemporaryDirectory() as tmp, fitz.open(pdf_path) as doc:
        tmp = Path(tmp)
        zoom = DJVU_DPI / 72
        names = []
        first, end = page_range(options or {}, len(doc))
        for i in range(first, end):
            page = doc[i]
            progress(start + (0.98 - start) * (i - first) / (end - first),
                     f"Making DjVu page {i + 1 - first} of {end - first}")
            pix = page.get_pixmap(dpi=DJVU_DPI, colorspace=fitz.csGRAY)
            pix.save(tmp / f"p{i}.pgm")
            run_tool("c44", "-dpi", str(DJVU_DPI), f"p{i}.pgm", f"p{i}.djvu", cwd=tmp)
            (tmp / f"p{i}.txt").write_text(djvu_text_layer(page, pix.width, pix.height, zoom),
                                           encoding="utf-8")
            run_tool("djvused", "-s", f"p{i}.djvu", "-e", f"select 1; set-txt p{i}.txt", cwd=tmp)
            names.append(f"p{i}.djvu")
        run_tool("djvm", "-c", "book.djvu", *names, cwd=tmp)
        shutil.move(tmp / "book.djvu", dst)


DJVU_PAGE_RE = re.compile(rb"# page (\d+)")
DJVU_SIZE_RE = re.compile(rb"\(page -?\d+ -?\d+ (\d+) (\d+)")
DJVU_LINE_RE = re.compile(rb"\(line (-?\d+) (-?\d+) (-?\d+) (-?\d+)")
DJVU_WORD_RE = re.compile(rb'\(word (-?\d+) (-?\d+) (-?\d+) (-?\d+) "((?:[^"\\]|\\.)*)"\)')


def djvu_lines(raw):
    """{page index: (width, height, [[x0, y0, x1, y1, text], ...])}: the text lines
    of every page, from `djvused -u -e output-txt`. Coordinates start bottom left."""
    pages, current, line = {}, None, None
    for row in raw.splitlines():
        page = DJVU_PAGE_RE.search(row)
        if page:
            current, line = int(page.group(1)) - 1, None
            continue
        size = DJVU_SIZE_RE.search(row)
        if size and current is not None:
            pages[current] = (int(size.group(1)), int(size.group(2)), [])
        if current not in pages:
            continue
        box = DJVU_LINE_RE.search(row)
        if box:
            line = [*map(int, box.groups()), ""]
            pages[current][2].append(line)
        for m in DJVU_WORD_RE.finditer(row):
            text = re.sub(rb"\\(.)", rb"\1", m.group(5)).decode("utf-8", "replace")
            if line is None:  # a word outside any line: its own line
                pages[current][2].append([*map(int, m.groups()[:4]), text])
            else:
                line[4] = f"{line[4]} {text}".strip()
    return pages


def djvu_to_pdf(src, dst, progress, options=None):
    """Pages become pictures; the DjVu text layer becomes invisible PDF text,
    so the PDF can still be searched and read back into PGN.
    options may hold first_page / last_page."""
    with tempfile.TemporaryDirectory() as tmp:
        shutil.copyfile(src, Path(tmp) / "book.djvu")
        count = int(run_tool("djvused", "-e", "n", "book.djvu", cwd=tmp).decode().strip())
        first, end = page_range(options or {}, count)
        progress(0.1, "Making PDF pages")
        run_tool("ddjvu", "-format=pdf", f"-page={first + 1}-{end}", "book.djvu", "book.pdf", cwd=tmp)
        progress(0.6, "Copying the text layer")
        lines = djvu_lines(run_tool("djvused", "-u", "-e", "output-txt", "book.djvu", cwd=tmp))
        fonts = [(name, str(Path(FONT_DIR) / f"{name}.ttf")) for name in ("arial", "seguisym")]
        fonts = [(name, path, fitz.Font(fontfile=path)) for name, path in fonts]
        with fitz.open(Path(tmp) / "book.pdf") as doc:
            for index, (width, height, items) in lines.items():
                if not first <= index < end:
                    continue
                page = doc[index - first]
                sx, sy = page.rect.width / width, page.rect.height / height
                for x0, y0, x1, y1, text in items:
                    if not text:
                        continue
                    # pieces of the line in Arial, or in Segoe UI Symbol for the
                    # figurines (♘) Arial lacks
                    runs = []
                    for c in text:
                        arial, symbol = fonts
                        f = symbol if not arial[2].has_glyph(ord(c)) and symbol[2].has_glyph(ord(c)) else arial
                        if runs and runs[-1][0] is f:
                            runs[-1][1] += c
                        else:
                            runs.append([f, c])
                    size = max(1, (y1 - y0) * sy * 0.8)
                    natural = sum(f[2].text_length(t, fontsize=size) for f, t in runs) or 1
                    scale = (x1 - x0) * sx / natural  # stretch to fit the line width
                    x, y = x0 * sx, (height - y0) * sy - size * 0.15
                    for (name, path, font), part in runs:
                        origin = fitz.Point(x, y)
                        page.insert_text(origin, part, fontsize=size, fontname=name,
                                         fontfile=path, render_mode=3,  # 3 = invisible
                                         morph=(origin, fitz.Matrix(scale, 1)))
                        x += font.text_length(part, fontsize=size) * scale
            doc.save(dst, garbage=3, deflate=True)
    return f"Made {dst.name}{range_text(first, end, count)}."


def pgn_to_book(src, dst, options, progress):
    games = read_pgn(src, progress)
    writer = BookWriter(options)
    if dst.suffix.lower() == ".pdf":
        writer.write_pdf(games, dst, progress)
    else:
        djvu_tool("c44")  # fail early if DjVuLibre is missing
        with tempfile.TemporaryDirectory() as tmp:
            pdf = Path(tmp) / "book.pdf"
            writer.write_pdf(games, pdf, progress)
            pdf_to_djvu(pdf, dst, progress)
    return f"Wrote {len(games)} games to {dst.name}."


def convert(src, dst, options, progress=lambda value, text: None, stopped=lambda: False):
    src, dst = Path(src), Path(dst)
    if not src.exists():
        raise UserError(f"File not found: {src}")
    report = progress

    def progress(value, text):
        if stopped():
            raise Stopped("Stopped.")
        report(value, text)

    s, d = src.suffix.lower(), dst.suffix.lower()
    if s in BOOK_TYPES and d == ".pgn":
        return book_to_pgn(src, dst, options, progress)
    if s == ".pgn" and d in BOOK_TYPES:
        return pgn_to_book(src, dst, options, progress)
    if s == ".pdf" and d in (".djvu", ".djv"):
        djvu_tool("c44")  # fail early if DjVuLibre is missing
        pdf_to_djvu(src, dst, progress, start=0.02, options=options)
        with fitz.open(src) as doc:
            count = len(doc)
        return f"Made {dst.name}{range_text(*page_range(options, count), count)}."
    if s in (".djvu", ".djv") and d == ".pdf":
        return djvu_to_pdf(src, dst, progress, options)
    raise UserError("This pair of file types is not supported.")


# ---------------------------------------------------------------- Window

def run_gui():
    import tkinter as tk
    from tkinter import filedialog, messagebox

    import customtkinter as ctk

    formats = {"PDF": ".pdf", "DjVu": ".djvu", "PGN": ".pgn"}
    targets = {"PDF": ["PGN", "DjVu"], "DjVu": ["PGN", "PDF"], "PGN": ["PDF", "DjVu"]}
    file_types = {"PDF": [("PDF", "*.pdf")], "DjVu": [("DjVu", "*.djvu *.djv")],
                  "PGN": [("PGN", "*.pgn")]}
    notes = {
        ("PDF", "PGN"): "Finds the games in the book and saves them as PGN. "
                        "Scanned pages are read with OCR.",
        ("DjVu", "PGN"): "Finds the games in the book and saves them as PGN. "
                         "Scanned pages are read with OCR.",
        ("PGN", "PDF"): "Prints the games as a book, with diagrams.",
        ("PGN", "DjVu"): "Prints the games as a book, with diagrams.",
        ("PDF", "DjVu"): "Saves every page as a grey picture (300 dpi). The text stays searchable.",
        ("DjVu", "PDF"): "Saves every page as a picture. The text stays searchable.",
    }
    langs = {"Auto": "auto", "English (K Q R B N)": "en",
             "Russian (Кр Ф Л С К)": "ru", "German (K D T L S)": "de"}
    pieces = {"English letters (N, B)": "letters", "Figurines (♘, ♗)": "figurines",
              "Russian letters (К, С)": "russian"}
    ocr_modes = {"Auto (only pages without text)": "auto", "Always (all pages)": "always",
                 "Off": "off"}

    ctk.set_appearance_mode("system")
    ctk.set_default_color_theme("blue")
    root = ctk.CTk()
    root.title(APP_TITLE)
    root.geometry("780x880")
    root.minsize(680, 800)
    root.grid_columnconfigure(0, weight=1)

    title_font = ctk.CTkFont("Segoe UI", 24, "bold")
    head_font = ctk.CTkFont("Segoe UI", 15, "bold")
    body_font = ctk.CTkFont("Segoe UI", 13)
    small_font = ctk.CTkFont("Segoe UI", 12)
    muted = ("gray40", "gray62")
    quiet_button = {"fg_color": "transparent", "border_width": 1,
                    "border_color": ("gray70", "gray35"), "text_color": ("gray10", "gray90"),
                    "hover_color": ("gray85", "gray25")}

    src_var, dst_var = tk.StringVar(), tk.StringVar()
    lang_var = tk.StringVar(value="Auto")
    ocr_var = tk.StringVar(value="Auto (only pages without text)")
    keep_var = tk.BooleanVar(value=True)
    piece_var = tk.StringVar(value="English letters (N, B)")
    final_var = tk.BooleanVar(value=True)
    first_var, last_var = tk.StringVar(), tk.StringVar()
    events = queue.Queue()
    stop_flag = threading.Event()

    # -- layout
    header = ctk.CTkFrame(root, fg_color="transparent")
    header.grid(row=0, column=0, sticky="ew", padx=24, pady=(20, 6))
    header.grid_columnconfigure(0, weight=1)
    ctk.CTkLabel(header, text="♞  Chess Book Converter", font=title_font).grid(
        row=0, column=0, sticky="w")
    ctk.CTkLabel(header, text="Chess books to PGN games, and games back to books.",
                 font=body_font, text_color=muted).grid(row=1, column=0, sticky="w")
    theme = ctk.CTkSegmentedButton(header, values=["Light", "Dark", "System"], font=small_font,
                                   command=lambda value: ctk.set_appearance_mode(value.lower()))
    theme.set("System")
    theme.grid(row=0, column=1, rowspan=2, sticky="e")

    def card(row, title):
        frame = ctk.CTkFrame(root, corner_radius=14)
        frame.grid(row=row, column=0, sticky="ew", padx=24, pady=8)
        frame.grid_columnconfigure(1, weight=1)
        ctk.CTkLabel(frame, text=title, font=head_font).grid(
            row=0, column=0, columnspan=3, sticky="w", padx=18, pady=(14, 4))
        return frame

    convert_card = card(1, "What to convert")
    ctk.CTkLabel(convert_card, text="From", font=body_font, text_color=muted).grid(
        row=1, column=0, sticky="w", padx=(18, 14), pady=6)
    src_fmt = ctk.CTkSegmentedButton(convert_card, values=list(formats), font=head_font,
                                     height=38, width=300, dynamic_resizing=False, command=lambda _: on_from())
    src_fmt.grid(row=1, column=1, sticky="w", pady=6)
    ctk.CTkLabel(convert_card, text="To", font=body_font, text_color=muted).grid(
        row=2, column=0, sticky="w", padx=(18, 14), pady=6)
    dst_fmt = ctk.CTkSegmentedButton(convert_card, values=targets["PDF"], font=head_font,
                                     height=38, width=200, dynamic_resizing=False, command=lambda _: on_to())
    dst_fmt.grid(row=2, column=1, sticky="w", pady=6)
    note = ctk.CTkLabel(convert_card, text="", font=small_font, text_color=muted,
                        justify="left", anchor="w")
    note.grid(row=3, column=0, columnspan=3, sticky="ew", padx=18, pady=(2, 14))

    files_card = card(2, "Files")
    for row, (label, var, command) in enumerate(
            (("Input", src_var, lambda: browse_src()), ("Output", dst_var, lambda: browse_dst())), 1):
        ctk.CTkLabel(files_card, text=label, font=body_font, text_color=muted).grid(
            row=row, column=0, sticky="w", padx=(18, 14), pady=6)
        ctk.CTkEntry(files_card, textvariable=var, font=small_font, height=34).grid(
            row=row, column=1, sticky="ew", pady=6)
        ctk.CTkButton(files_card, text="Browse…", width=100, height=34, font=body_font,
                      command=command, **quiet_button).grid(row=row, column=2, padx=18, pady=6)
    ctk.CTkFrame(files_card, width=1, height=8, fg_color="transparent").grid(row=3, column=0)

    options_card = card(3, "Options")
    options = ctk.CTkFrame(options_card, fg_color="transparent")
    options.grid(row=1, column=0, columnspan=3, sticky="ew", padx=18, pady=(0, 14))

    actions = ctk.CTkFrame(root, fg_color="transparent")
    actions.grid(row=4, column=0, sticky="ew", padx=24, pady=(10, 4))
    actions.grid_columnconfigure(0, weight=1)
    status = ctk.CTkLabel(actions, text="Ready.", font=body_font, text_color=muted, anchor="w")
    status.grid(row=0, column=0, sticky="ew")
    stop_button = ctk.CTkButton(actions, text="Stop", width=100, height=40, font=body_font,
                                state="disabled", command=stop_flag.set, **quiet_button)
    stop_button.grid(row=0, column=1, padx=(0, 10))
    go_button = ctk.CTkButton(actions, text="Convert", width=150, height=40,
                              font=ctk.CTkFont("Segoe UI", 14, "bold"), command=lambda: start())
    go_button.grid(row=0, column=2)
    bar = ctk.CTkProgressBar(root, height=10)
    bar_color = bar.cget("progress_color")

    def set_progress(value):
        # at 0 CustomTkinter still draws a dot: hide it with the track colour
        bar.configure(progress_color=bar_color if value > 0 else bar.cget("fg_color"))
        bar.set(value)

    set_progress(0)
    bar.grid(row=5, column=0, sticky="ew", padx=24, pady=(8, 8))
    log_box = ctk.CTkTextbox(root, height=110, font=small_font, corner_radius=12,
                             state="disabled", wrap="word")
    log_box.grid(row=6, column=0, sticky="nsew", padx=24, pady=(4, 20))
    root.grid_rowconfigure(6, weight=1)

    # -- behaviour
    def pair():
        return src_fmt.get(), dst_fmt.get()

    def option_row(row, label, widget):
        ctk.CTkLabel(options, text=label, font=body_font, text_color=muted).grid(
            row=row, column=0, sticky="w", padx=(0, 14), pady=5)
        widget.grid(row=row, column=1, sticky="w", pady=5)

    def show_options():
        for child in options.winfo_children():
            child.destroy()
        src, dst = pair()
        menu = {"font": body_font, "dropdown_font": body_font, "width": 280, "height": 32}
        if src != "PGN":  # books have pages: allow a page range
            pages = ctk.CTkFrame(options, fg_color="transparent")
            for column, (text, var) in enumerate((("from", first_var), ("to", last_var))):
                ctk.CTkLabel(pages, text=text, font=body_font, text_color=muted).grid(
                    row=0, column=column * 2, padx=(0 if column == 0 else 12, 8))
                ctk.CTkEntry(pages, textvariable=var, width=70, height=32, font=body_font,
                             justify="center").grid(row=0, column=column * 2 + 1)
            ctk.CTkLabel(pages, text="empty = whole book", font=small_font,
                         text_color=muted).grid(row=0, column=4, padx=(12, 0))
            option_row(9, "Pages", pages)
        if dst == "PGN":
            option_row(0, "Notation in the book",
                       ctk.CTkOptionMenu(options, variable=lang_var, values=list(langs), **menu))
            option_row(1, "Read scans (OCR)",
                       ctk.CTkOptionMenu(options, variable=ocr_var, values=list(ocr_modes), **menu))
            ctk.CTkSwitch(options, text="Keep the book text as comments", variable=keep_var,
                          font=body_font).grid(row=2, column=0, columnspan=2, sticky="w", pady=6)
        elif src == "PGN":
            option_row(0, "Pieces",
                       ctk.CTkOptionMenu(options, variable=piece_var, values=list(pieces), **menu))
            ctk.CTkSwitch(options, text="Diagram at the end of each game", variable=final_var,
                          font=body_font).grid(row=1, column=0, columnspan=2, sticky="w", pady=6)
        note.configure(text=notes[pair()])

    def suggest_output():
        src = src_var.get().strip()
        if src:
            dst_var.set(str(Path(src).with_suffix(formats[dst_fmt.get()])))

    def on_from():
        choices = targets[src_fmt.get()]
        dst_fmt.configure(values=choices)
        if dst_fmt.get() not in choices:
            dst_fmt.set(choices[0])
        src = src_var.get().strip()
        if src and Path(src).suffix.lower() not in file_types_ext(src_fmt.get()):
            src_var.set("")
            dst_var.set("")
        show_options()
        suggest_output()

    def on_to():
        show_options()
        suggest_output()

    def file_types_ext(name):
        return (".djvu", ".djv") if name == "DjVu" else (formats[name],)

    def browse_src():
        path = filedialog.askopenfilename(
            filetypes=file_types[src_fmt.get()] + [("All files", "*.*")])
        if not path:
            return
        ext = Path(path).suffix.lower()
        for name in formats:  # a file of another type: switch "From" to match it
            if ext in file_types_ext(name) and name != src_fmt.get():
                src_fmt.set(name)
                on_from()
        src_var.set(path)
        suggest_output()

    def browse_dst():
        name = dst_fmt.get()
        path = filedialog.asksaveasfilename(defaultextension=formats[name],
                                            filetypes=file_types[name])
        if path:
            dst_var.set(path)

    def log(text):
        log_box.configure(state="normal")
        log_box.insert("end", text + "\n")
        log_box.see("end")
        log_box.configure(state="disabled")

    def start():
        src, dst = src_var.get().strip(), dst_var.get().strip()
        if not src or not dst:
            messagebox.showwarning(APP_TITLE, "Choose an input file and an output file first.")
            return
        if Path(src).suffix.lower() not in file_types_ext(src_fmt.get()):
            messagebox.showwarning(APP_TITLE, f"The input file is not a {src_fmt.get()} file.")
            return
        if Path(dst).suffix.lower() not in file_types_ext(dst_fmt.get()):
            dst = str(Path(dst).with_suffix(formats[dst_fmt.get()]))
            dst_var.set(dst)
        page_numbers = []
        for var, name in ((first_var, "first"), (last_var, "last")):
            text = var.get().strip()
            if src_fmt.get() == "PGN" or not text:
                page_numbers.append(None)
            elif text.isdigit() and int(text) > 0:
                page_numbers.append(int(text))
            else:
                messagebox.showwarning(APP_TITLE, f"The {name} page must be a number like 12.")
                return
        if None not in page_numbers and page_numbers[0] > page_numbers[1]:
            messagebox.showwarning(APP_TITLE, "The first page must not be after the last page.")
            return
        if Path(dst).exists() and not messagebox.askyesno(
                APP_TITLE, f"{Path(dst).name} already exists.\nReplace it?"):
            return
        settings = {"lang": langs[lang_var.get()], "keep_text": keep_var.get(),
                    "ocr": ocr_modes[ocr_var.get()],
                    "pieces": pieces[piece_var.get()], "final_diagram": final_var.get(),
                    "first_page": page_numbers[0], "last_page": page_numbers[1]}
        go_button.configure(state="disabled")
        stop_button.configure(state="normal")
        stop_flag.clear()
        set_progress(0)
        log(f"Converting {Path(src).name} ({src_fmt.get()} → {dst_fmt.get()}) ...")

        def work():
            try:
                done = convert(src, dst, settings,
                               lambda value, text: events.put(("progress", value, text)),
                               stop_flag.is_set)
                events.put(("done", done, None))
            except Stopped:
                events.put(("stopped", "Stopped.", None))
            except UserError as exc:
                events.put(("error", str(exc), None))
            except Exception as exc:  # show anything unexpected instead of crashing
                events.put(("error", f"Unexpected error: {exc!r}", None))

        threading.Thread(target=work, daemon=True).start()

    def poll():
        try:
            while True:
                kind, value, text = events.get_nowait()
                if kind == "progress":
                    set_progress(value)
                    status.configure(text=text)
                else:
                    go_button.configure(state="normal")
                    stop_button.configure(state="disabled")
                    set_progress(1 if kind == "done" else 0)
                    status.configure(text={"done": "Done.", "stopped": "Stopped."}.get(kind, "Failed."))
                    log(value)
                    if kind == "error":
                        messagebox.showerror(APP_TITLE, value)
        except queue.Empty:
            pass
        root.after(100, poll)

    src_fmt.set("PDF")
    dst_fmt.set("PGN")
    show_options()
    log("Tip: choose From and To, then the input file. Scanned pages are read with OCR, "
        "which takes a few seconds per page and makes some mistakes.")
    try:
        djvu_tool("djvutxt")
    except UserError:
        log("Warning: DjVuLibre is not installed. DjVu files will not work.")
    try:
        find_tessdata("eng+rus+deu")
    except UserError:
        log(f"Warning: OCR language files are missing in {TESSDATA_DIRS[0]}.")
    root.after(100, poll)
    root.mainloop()


def main():
    if len(sys.argv) == 1:
        run_gui()
        return
    parser = argparse.ArgumentParser(description=APP_TITLE)
    parser.add_argument("input")
    parser.add_argument("output")
    parser.add_argument("--lang", default="auto", choices=["auto", "en", "ru", "de"])
    parser.add_argument("--ocr", default="auto", choices=["auto", "always", "off"])
    parser.add_argument("--pieces", default="letters", choices=["letters", "figurines", "russian"])
    parser.add_argument("--pages", help="only these book pages, like 5-20 (or 5- or -20)")
    parser.add_argument("--no-comments", action="store_true")
    parser.add_argument("--no-final-diagram", action="store_true")
    args = parser.parse_args()
    options = {"lang": args.lang, "ocr": args.ocr, "pieces": args.pieces,
               "keep_text": not args.no_comments, "final_diagram": not args.no_final_diagram}
    if args.pages:
        first, dash, last = args.pages.partition("-")
        last = last if dash else first  # "7" alone means only page 7
        options["first_page"] = int(first) if first.strip() else None
        options["last_page"] = int(last) if last.strip() else None
    try:
        print(convert(args.input, args.output, options,
                      lambda value, text: print(f"{value * 100:5.1f}%  {text}", file=sys.stderr)))
    except UserError as exc:
        sys.exit(f"Error: {exc}")


if __name__ == "__main__":
    main()
