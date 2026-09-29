# Chess Book Converter

Latest OCR improvement: [read a failed source word at two scales](SOURCE_REREAD_DEVELOPMENT_RU.md).
Incomplete audited bold notation is reread in its original page region at
360 and 450 dpi. Both complete readings must agree at the existing character
thresholds. No board position or reference game participates in the decoding.

Latest safety fix: [reserve unread mainline turns](MAINLINE_CLAIM_DEVELOPMENT_RU.md).
An audited number at the start of a separate notation paragraph reserves its
turn even when the adjacent figurine is unreadable. Unanchored analysis cannot
replace it; unresolved turns are exported as incomplete prefixes. Comparative
phrases such as “better than” also keep alternatives in comments.

The [second validation expansion](VALIDATION_ROUND2_RU.md) brings the local
corpus to 27 cases. Three newly checked games expose two incomplete prefixes
and a legal move taken from analysis instead of the mainline; two non-game
pages correctly produce no games. Only these new cases were rerun in this stage.

The [expanded validation set](EXPANDED_VALIDATION_RU.md) adds three unseen
examples and a biography-only page. It exposes a wrong but legal OCR repair,
a missing table move, and an undetected diagram continuation. These failures
remain in the test set; the recognition code was frozen during this evaluation.

Latest local development: [bounded post-result comments](CLOSING_COMMENT_DEVELOPMENT_RU.md).
Mainline mode retains a clearly bounded final chess explanation at the last move,
without playing its moves or absorbing the following biography. Ambiguous tails
are reported; OCR errors inside retained comments are not automatically repaired.
Earlier: [glued promotions and the end of a game](PROMOTION_DEVELOPMENT_RU.md).
An explicitly printed promotion can be separated from a glued numbered reply;
a draw announcement at the start of the final paragraph stops postgame analysis
from entering the mainline. Earlier: [column boundaries and damaged pawn words](COLUMN_PAWN_DEVELOPMENT_RU.md).
Merged headings request a geometrical reread; short corrupted pawn words require
complete original-pixel evidence. Examples introduced by «скажем» or «например»
remain textual analysis unless a mainline anchor takes precedence.

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
See [STRUCTURE_DEVELOPMENT_RU.md](STRUCTURE_DEVELOPMENT_RU.md) and the earlier
[fine-print evaluation](FINE_PRINT_DEVELOPMENT_RU.md).
Whole-chapter recovery of unmarked game headings, split table rows and damaged
captures is described in [the Portisch evaluation notes](PORTISCH_DEVELOPMENT_RU.md).
The window now defaults to **Главная линия; варианты — текстом**: extract the
mainline and keep analysis as text at its printed position. Turn this switch
off to use variation-tree extraction. This is still an experimental reader;
see [mainline development notes](MAINLINE_DEVELOPMENT_RU.md).
For ambiguous pawn ranks in table rows, mainline mode now checks a separately
cached reading of the original page. Matching requires the same page, number
and companion move; clear conflicting ranks are not replaced. See
[original-row reading and evaluation](SOURCE_ROW_DEVELOPMENT_RU.md).
Split half-rows now match across unequal OCR row counts without borrowing a
Black move for White. A backward row number can be checked against three
matching original-page rows. See [split-row evaluation](SPLIT_ROW_DEVELOPMENT_RU.md).
Incomplete bold words can now be re-read from their original page pixels,
including touching figurines and split numbers/squares. The latest
[bold-text evaluation](BOLD_MAINLINE_DEVELOPMENT_RU.md) recorded 16/17 exact
development mainlines, but neither of two new games was fully extracted.
These results do not establish accuracy on arbitrary books.
The subsequent [row and game-boundary evaluation](ROW_BOUNDARY_DEVELOPMENT_RU.md)
restores the full Spassky–Yukhtman mainline (59 plies) and finds the previously
missed Anderssen–Kieseritzky heading; that game still stops after four plies.
Targeted original-page layout retries separate interleaved columns and leave
the primary OCR cache intact.
The next [diverse-book evaluation](DIVERSE_DEVELOPMENT_RU.md) adds two unseen
games and a biography-only page. Original-page checks recover missed figurine
words and moves split across lines. Mainline mode also prevents suggestions
introduced by "better" or continuations of analysis from filling unread turns.
The evaluation records failed examples as well as successful extractions.

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
python chess_converter.py book.djvu games.pgn --lang ru --mainline-only
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

The next stage repairs Russian word wraps across adjacent lines using a local,
versioned dictionary. It retains real hyphens when the hyphenated spelling is
the only exact match; unknown and ambiguous readings remain unchanged. Both
source lines and every decision are recorded in `text_normalization` in the
quality report. No online service or reference PGN is used. Reinstall
`requirements.txt` when upgrading an earlier copy to get the dictionary.
The checked Euwe-Speijer example now matches the complete 101-path tree,
comments, NAGs and branch order. See [comment results](COMMENTS_DEVELOPMENT_RU.md)
for the new-page check and remaining limitations.

Diagram refinement now reads all 64 squares of the checked Alekhine-Euwe
starting diagram. The report distinguishes unread squares from unknown
castling/en-passant history and lists compatible FEN candidates. It exports
a setup only when a single orientation and historical state fit the printed
first move. Pawns without a legal en-passant capture and home-square kings
without a corresponding rook no longer block otherwise unambiguous setups.
The Alekhine-Euwe example still needs history review; it is not exported with
an assumed en-passant field. See [diagram results](DIAGRAM_DEVELOPMENT_RU.md).

Printed move numbers and the side to move are now checked during extraction.
A missing turn stops the mainline instead of shifting later legal moves into
its place. The PGN is marked incomplete with `Result "*"` and `MissingMove`;
a printed outcome is retained separately as `SourceResult`. Unread OCR lines
and any unattached analysis are kept in the quality report. Narrow row repairs
require exact moves and independent sequence evidence. Eight previously exact
mainlines in the 13-game local corpus remain exact. See
[move-sequence checks and limitations](MOVE_SEQUENCE_DEVELOPMENT_RU.md).

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
explicitly skip when those optional files are absent; the local suite also
runs these three private-reference checks.
The optional files, placed in `tests/fixtures`, are
`euwe-speijer-1924-checked-tree.pgn`, `euwe-capablanca-1928-checked-tree.pgn`
and `page18-checked-notation.txt`. The converter never reads reference PGNs
to generate its output.
For live book conversion checks, `corpus_runner.py` accepts `compare_tree: true`
in a case. Such a case checks the complete tree, comments, NAGs and initial FEN
and returns code 2 when the mainline matches but some branches are missing.

Mainline OCR now stops before an ambiguous figurine move when its destination
file is unreadable and several squares fit. The confirmed prefix is saved as
incomplete; the quality report retains the source continuation. See
[the local validation results](SAFE_DESTINATION_DEVELOPMENT_RU.md) for the
Anderssen–Dufresne regression and the unchanged 21 earlier examples.

An additional bold digit sample now recovers the damaged `19...Qxf3` directly
from the scan, extending that checked prefix from 37 to 42 plies. The sample
comes from a separate word on the preceding page; confidence thresholds and
the ambiguity guard remain unchanged. See [fullness validation](FULLNESS_DEVELOPMENT_RU.md)
for the remaining line-order problem and two independent book regressions.

The subsequent [line-order repair](LINE_ORDER_DEVELOPMENT_RU.md) resolves that
ending: Anderssen–Dufresne now matches all 47 plies and the result. Oversized OCR
word boxes are tightened only when peer lines and an unambiguous ink band agree.
The report records the old and new bounds; OCR cache version 22 prevents reuse
of the former line order. This does not establish exact fidelity of comments or NAGs.

The [Larsen and Keres follow-up](LARSEN_KERES_DEVELOPMENT_RU.md) adds geometry
retries for backwards table order and agreement-based rereading of damaged
black-move rows. Hyphenated commentary and a quoted game's resignation no
longer impersonate mainline rows or the end of the current game. The checked
Larsen–Spassky mainline is complete; Keres–Winter is still a marked partial
prefix. Reference PGNs are used only by validation, never by conversion.

## Licenses

- This program: [AGPL-3.0](LICENSE).
- It uses [PyMuPDF](https://github.com/pymupdf/PyMuPDF) (AGPL-3.0),
  [python-chess](https://github.com/niklasf/python-chess) (GPL-3.0) and
  [CustomTkinter](https://github.com/TomSchimansky/CustomTkinter) (MIT).
- The files in `tessdata` come from
  [tesseract-ocr/tessdata_best](https://github.com/tesseract-ocr/tessdata_best)
  and are under the Apache-2.0 license ([tessdata/LICENSE](tessdata/LICENSE)).
- Russian word-wrap checks use [pymorphy3](https://pypi.org/project/pymorphy3/)
  (MIT) and [pymorphy3-dicts-ru](https://pypi.org/project/pymorphy3-dicts-ru/)
  (MIT package code; OpenCorpora dictionary data under CC BY-SA 3.0).
