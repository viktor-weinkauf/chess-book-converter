# Chess Book Converter

A small Windows program that converts chess books and games:

| From | To |
|---|---|
| PDF or DjVu book | PGN |
| PGN | PDF or DjVu book (with diagrams) |
| PDF | DjVu, and back (the text stays searchable) |

It finds the games in the text of a book, including side lines, comments,
player names and results. It reads English, Russian and German notation,
figurines, and the long notation of older Russian books (`Кg1—f3`, `d5 : e4`).

Scanned books (pages that are only pictures) are read with OCR. OCR makes
mistakes, so check the games afterwards. It works best on books with letters;
books printed with figurines (♘) are read poorly.

## Setup

1. Install [Python](https://www.python.org/) 3.10 or newer.
2. Install the Python packages:
   ```
   pip install -r requirements.txt
   ```
3. For DjVu files, install DjVuLibre:
   ```
   winget install DjVuLibre.DjView
   ```

The OCR language files (English, Russian, German) are already in `tessdata`.

## Use

Double-click `Start Chess Converter.bat`. Then:

1. Choose **From** and **To**.
2. Choose the input file. The output file is suggested.
3. Optional: only some pages, the notation, OCR on or off.
4. Click **Convert**.

From the command line:

```
python chess_converter.py book.pdf games.pgn --pages 94-96
python chess_converter.py games.pgn book.pdf --pieces figurines
```

Pages already read with OCR are kept in `%LOCALAPPDATA%\ChessConverter\ocr-cache`,
so a second run of the same pages is fast.

## Licenses

- This program: [AGPL-3.0](LICENSE).
- It uses [PyMuPDF](https://github.com/pymupdf/PyMuPDF) (AGPL-3.0),
  [python-chess](https://github.com/niklasf/python-chess) (GPL-3.0) and
  [CustomTkinter](https://github.com/TomSchimansky/CustomTkinter) (MIT).
- The files in `tessdata` come from
  [tesseract-ocr/tessdata_best](https://github.com/tesseract-ocr/tessdata_best)
  and are under the Apache-2.0 license ([tessdata/LICENSE](tessdata/LICENSE)).
