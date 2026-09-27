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

This development version adds game-boundary detection, local batch processing,
quality reports and regression tests. It is **not a lossless converter**:
diagram recognition and visual notation reading support only calibrated print
styles, and some analysis remains in comments rather than PGN variations.
See [STRUCTURE_DEVELOPMENT_RU.md](STRUCTURE_DEVELOPMENT_RU.md) and the latest
[fine-print evaluation](FINE_PRINT_DEVELOPMENT_RU.md).

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

Book conversions also write `games.pgn.report.json`. The report traces each
exported move to its source page/line and lists corrections, unparsed move
candidates, missing game sections and parser errors. Every exported branch is
checked for legality, and the PGN is read back to check the full tree and comments.
`completed` means no implemented checks raised warnings; it is not proof of
fidelity to the printed book. All OCR conversions are marked `needs_review`.

```
python chess_converter.py book.pdf games.pgn --strict --report review.json
python chess_batch.py "C:\books" "C:\converted" --lang ru
python chess_batch.py "C:\books" "C:\converted" --lang ru --strict
```

Strict mode writes the report and leaves any existing PGN unchanged if warnings
are found (exit code 2). This intentionally rejects unverified OCR output.
Normal mode saves a candidate PGN alongside the report. Batch mode returns 0
when all jobs completed without warnings, 2 when review is needed, or 1 on a
failed job. One failed book does not stop the remaining books.

Batch output keeps relative subfolders and input extensions (`book.djvu.pgn`),
so equally named PDF and DjVu files cannot collide. `batch-report.json` records
each job. A rerun reuses a job only if the source, settings, converter code,
dependencies, traineddata, PGN and report still match their recorded hashes.
Use `--force` to reconvert. OCR pages are still cached separately.

Game boundaries use numbered headings, results, resignation phrases and
annotation credits. When numbered games are detected, standalone opening
examples outside those sections are excluded. Text before the first move and
unbounded prose after the last move are excluded. Ambiguous extraction still
requires review; typography and OCR can hide the boundary cues.

Pages already read with OCR are kept in `%LOCALAPPDATA%\ChessConverter\ocr-cache`,
so a second run of the same pages is fast.
Set `CHESS_CONVERTER_CACHE` to use another cache folder. In environments that
disallow multiprocessing on Windows, OCR falls back to serial processing.

OCR also recognizes a narrow family of printed chess figurines using local visual
templates. Unsupported shapes are left unchanged. `--figurine-ocr off` disables
this stage for comparison; it is supported by both the converter and batch CLI.
The report records glyph boxes, original words, replacements and disagreements
between readings. Similarity scores are not probabilities. See
[the Russian OCR development notes](OCR_DEVELOPMENT_RU.md) for the tested scope.
Set `CHESS_CONVERTER_DJVU` to the folder containing portable DjVuLibre tools.

The coordinate stage reads a narrow bold algebraic typeface directly from
pixels and records character boxes in `coordinate_ocr`. It can also recognize
just a move number as typography evidence, without certifying the unread move.
In numbered game sections, supported bold numbers help distinguish the
mainline from analysis. `--coordinate-ocr off` disables this stage and its
typography cues. To disable both visual stages, also use `--figurine-ocr off`.
Both switches are available in batch mode and in corpus case settings. They
control move notation; diagram recognition during OCR is a separate stage.
Touching figurines and letters can be segmented together when the supported
font family is detected. A second OCR reading cannot discard an explicit
capture mark or replace the readable parts of a damaged square. Look-ahead
repair must also preserve a recognized piece type. These safeguards improve
fidelity; comments, variation trees and diagram positions still require review.
The current local development cases match the full Euwe-Reti mainline/result
(61 plies) and Estrin-Spassky mainline/result (38 plies). These selected examples
are not a whole-book accuracy estimate. On a new page, six of nine selected
bold move words were read exactly; seven had the correct piece and square.
That was the earlier coordinate-stage evaluation, before diagram/tree work.

The parser now anchors nested and enumerated variations to printed move
numbers, preserves starting comments separately from comments after a move,
and uses supported bold mainline/paragraph cues to return from analysis.
An unread variation is retained as source text rather than filled with guessed
moves. Recognized board images are removed from OCR prose and represented in
reading order. A narrow calibrated board profile can create a `SetUp`/`FEN`
game when all 64 squares, orientation and move context are unambiguous.
Castling or en-passant history that cannot be established is left unresolved;
the unknown halfmove clock is explicitly recorded. This currently applies to
pages read with OCR, not diagram images on text-only extraction paths.
See [tree and diagram development notes](STRUCTURE_DEVELOPMENT_RU.md).

## Development and a checked corpus

Recent local changes recognize explicit pawn-promotion suffixes and recheck
unknown diagram squares against additional labelled samples. Missing promotion
pieces are not inferred from subsequent moves. Supplemental diagram readings run
after cached OCR, retain their original evidence and require the same thresholds.
See [the promotion and diagram results](CONTINUATION_DEVELOPMENT_RU.md).

The next completeness stage adds a 272-path annotated reference, detailed
missing-branch reports, clipped-word recovery and safer separation of damaged
variations. Euwe-Capablanca now matches all 50 mainline plies and its draw result;
its full tree remains incomplete. See [completeness results](COMPLETENESS_DEVELOPMENT_RU.md)
and [annotation conventions](tests/fixtures/ANNOTATION_POLICY_RU.md).

The following stage restores all 101 Euwe-Speijer move paths from its DjVu
page, including the printed variations, their order and NAGs. Four comment
differences remain because line-end word hyphens are retained. Euwe-Capablanca
improves from 90 to 95 of 272 paths, with no unexpected paths in either example.
This is development-corpus coverage, not general accuracy on unseen books.
See [split-word and variation-tree results](SPEIJER_TREE_DEVELOPMENT_RU.md).

```
pip install -r requirements-dev.txt
python -m pytest -q
python benchmark.py corpus.json --output benchmark-report.json
```

A corpus manifest contains existing output and manually checked reference PGNs:

```json
{"cases": [{"id": "sample-01", "actual": "actual.pgn", "expected": "checked.pgn"}]}
```

Paths are relative to the manifest. Pair games with `BookGame` tags when
available. The benchmark compares entire move paths (including variations),
starting FEN, comments, NAGs and result. An earlier wrong move cannot be hidden
by later legal moves. Metadata such as player names is not scored yet.
Keep private books and reference excerpts out of the public repository.
The public test suite includes small notation/board fixtures and factual
mainline PGNs. Full annotated reference PGNs and the transcribed book passage
remain in the local evaluation bundle and are ignored by Git. Three tests
explicitly skip when those optional files are absent: the public suite has
223 passing tests and 3 skips; the local suite runs all 226 tests.
The optional files, placed in `tests/fixtures`, are
`euwe-speijer-1924-checked-tree.pgn`, `euwe-capablanca-1928-checked-tree.pgn`
and `page18-checked-notation.txt`. The converter never reads reference PGNs
to generate its output.
For live book conversion checks, `corpus_runner.py` accepts `compare_tree: true`
in a case. Such a case checks the complete tree, comments, NAGs and initial FEN
and returns code 2 when the mainline matches but some branches are missing.

## Licenses

- This program: [AGPL-3.0](LICENSE).
- It uses [PyMuPDF](https://github.com/pymupdf/PyMuPDF) (AGPL-3.0),
  [python-chess](https://github.com/niklasf/python-chess) (GPL-3.0) and
  [CustomTkinter](https://github.com/TomSchimansky/CustomTkinter) (MIT).
- The files in `tessdata` come from
  [tesseract-ocr/tessdata_best](https://github.com/tesseract-ocr/tessdata_best)
  and are under the Apache-2.0 license ([tessdata/LICENSE](tessdata/LICENSE)).
