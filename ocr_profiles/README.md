# Local figurine templates

`classic.json` contains 12 explicit templates for printed K/Q/R/B/N glyphs.
It is not a chess engine or a move database. Each template stores a normalized
32 × 32 binary shape, aspect ratio and provenance of the tiny source glyph.

Source: user-provided `Kasparov_MGP_Vol_2.djvu`, file pages 14 and 15, rendered
at 300 dpi. No book pages or prose are distributed here. The held-out shapes in
`tests/fixtures/figurine_shapes.json` come from a different page (16); prose
negatives are from `spasski2.djvu`, page 26. Do not train on these test shapes.

This profile is deliberately narrow. A shape must clear both a similarity
threshold and a margin over other piece classes. Unsupported fonts may produce
no detections. Shape similarity is not a calibrated confidence percentage.

Any new profile or threshold must be evaluated on separate pages/fonts and on
ordinary-text negatives. Changing a profile invalidates OCR and batch caches.

`coordinates.json` contains 43 character templates for the bold algebraic font
on file page 14 of the same volume. It covers letters a-h, digits 0-9 and a few
punctuation marks. It contains no move list or reference game. Whole-word reads
must be unambiguous; a partial number read supplies typography evidence only.
The parser treats this supported font as mainline typography in numbered game
sections. This convention is not universal across books.

`tests/fixtures/coordinate_words.json` contains tiny, thresholded word crops
from file pages 15-16, including a king capture newly recovered with the colon templates.
These were held out from coordinate-profile construction. Page 15 supplied
some figurine templates separately, so it is not a holdout for the whole OCR
pipeline. Do not add these test words to the character profile.

When this glyph family has been independently detected on the page, coordinate
segmentation may use the figurine alphabet to recover touching symbols which
the connected-component detector missed. This does not consult a chess board.
`joined_notation.json` exercises development examples from pages 14-15, including
the additional king sample. These are regression cases, not independent holdouts.
The new nine-word evaluation on file page 18 was not used for training/tuning.

## Board profile (subsequent development stage)

`boards.json` stores 16×16 grayscale square samples labelled with piece and
square colour, from file pages 14 and 18 of volume 2. There are no positions,
move lists or FENs in the runtime profile. This is a narrowly calibrated family
of hatched diagrams. Frame/checker detection precedes classification; all 64
squares must pass distance and margin thresholds to supply a placement.

`board-development.png` is a regression crop from page 18, not a holdout.
`board-holdout.png` comes from page 15, which was not used for this board
profile (although it supplied some separate in-text figurine templates).
Recognition must abstain on its uncertain squares. Neither crop includes
the page's prose. These are local development fixtures.
The earlier page-18 coordinate-word evaluation is historical: page 18 is now
development material for the board/parser stages, so it must not be called an
independent test of the entire current pipeline.

## Fine print

`regular.json` contains 45 character templates from the regular, smaller
algebraic text on volume 2, file page 18. Provenance is recorded per character.
The source page is development material. The profile currently covers a-e,
g-h, digits and selected punctuation; it is not a complete font model.
Matching uses the same strict character similarity and ambiguity margins as
the bold reader, without a chess position or reference game.

Regular readings supply exact notation evidence, never mainline typography.
Incompatible complete readings from the two profiles are rejected. Matching
text with uncertain weight is treated as regular. Both profiles are included
in OCR and batch cache fingerprints.

`tests/fixtures/fine_print_words.json` contains 46 tiny binary word crops from
file page 21, with independently transcribed target readings. That page was
not used for profile construction or threshold adjustment. The regular visual
verifier reads 12 of the 46 crops, with no incorrect accepted readings in this
sample. The remaining crops are abstentions; ordinary OCR may still read them.
This is not an end-to-end OCR accuracy estimate. Do not train on these crops.

## Supplemental diagram samples and promotions

`boards-extra.json` adds three uncertain-cell samples from volume 2, file page 24:
black bishop/dark, white king/light, white knight/light. It competes with the
original board templates at unchanged distance/margin thresholds. The white
king/light class was absent from the original training set. Refinement reads
original pixels after OCR cache loading and keeps both readings in the report;
it does not write refined cells back into the base OCR cache. Batch fingerprints
include this profile. The following three diagrams on pages 25-26 are held out:
188/192 accepted cells match; four remain unknown. Their reduced cell features
and checked labels are in `diagram_supplement.json`, for evaluation only.

`promotion_word.json` is a development crop of `33.bc♕` from file page 22.
It checks that the full printed promotion can be decoded without a supplied
figurine anchor or a chess position. No new letter/figurine samples were added
for this word; the notation grammar and parser now accept the trailing piece.

## Coverage follow-up

The regular profile now includes two additional samples (`f` and `8`) from
page 18; the bold profile adds `c` and a dot from page 26. These are development
samples, with unchanged thresholds. OCR cache version 20 also recovers a clipped
word edge only as far as the next blank pixel column. `clipped_notation.json`
tests both the printed small `Kf8` and the clipped `34...Kc3` word. Page 26 is
now development material for notation; its diagrams remain held out from the
separate board profiles. The 46-word evaluation on page 21 stays held out.

## Split words and regular-font ligatures

The following stage grows `regular.json` from 47 to 67 entries on development
page 18. These include parentheses, evaluations and short touching sequences
such as a figurine plus its neighbouring letter. Figurine-containing entries
are disabled unless this family has been independently detected on the page.
The general `classic.json` detector is unchanged. Existing figure anchors may
use another visually verified boundary, but never a different piece identity.
Two thin-sign samples retain the actual column-stacking raster phase; their
source records include the raster crop and its translation to the source page.
No move list, board position or reference PGN is used in these profiles.

`split_notation.json` contains 12 small development crops, including negative
page-number and missing-dot checks. The unchanged 46-word holdout on page 21
now accepts 16 correct words, up from 12, with no incorrect accepted reading.
The similarity and ambiguity thresholds remain unchanged. OCR cache version 21
also corrects split-word ownership and keeps explicit audit evidence for joins
and isolated text bands. This does not establish accuracy on arbitrary fonts.

## Diagram follow-up

`boards-extra.json` version 2 adds five visually labelled cells from volume 2,
file page 19: black knight/dark, black rook/light, white bishop/dark, white
rook/light and white king/dark. The profile now has eight supplemental samples;
the base profile and distance/margin thresholds are unchanged. These are only
cell features and source coordinates, with no positions or move sequences.
Page 19 is now development material and must not be called a held-out test.

The diagram on that page improves from 59 to 64 correct accepted cells. Page 20
remains held out for the board profile (61/64). Earlier holdouts on pages 15,
25 and 26 retain their readings. Two newly labelled diagrams on pages 27 and
28 accept 49/64 and 58/64 cells, respectively; page 28 previously accepted
57/64. None of the accepted cells in these checks disagrees with the visual
labels. No sample from a held-out page was added to the profile.

`diagram_followup.json` and `diagram_new_pages.json` store reduced cell features
and independent visual labels for regression/evaluation, never for conversion.
The original board-image crops remain unchanged. Fully reading a diagram does
not establish castling rights or en-passant history; those are checked separately.

## Bold words on the original page

`bold-extra.json` adds three shapes from development pages 18 and 22 of
Kasparov volume 1: the touching knight/file-f pair, a lowercase d, and a
thick full stop. It contains no moves or game identifiers. Six small grayscale
crops in `tests/fixtures/bold_source_words.json` exercise full words, split
numbers and split square fragments. Source coordinates accompany the crops.

`bold_ocr.py` applies this supplement after cached OCR, on the original page
in mainline mode. Number/side constraints, a unique match on the audited line,
and a complete pixel reading are required. Figurines require independent
font detection. Existing similarity and ambiguity thresholds are unchanged;
the base OCR cache is not modified. Batch fingerprints include the new module
and profile. This is limited font support, not general font recognition.

Two new game checks were kept out of tuning at this stage: Spassky–Yukhtman
(Spassky volume 1, file pages 22–24) and Anderssen–Kieseritzky (Kasparov volume 1,
pages 26–29). Their results remain a correct 38/59-ply prefix and no exported
game, respectively. See `BOLD_MAINLINE_DEVELOPMENT_RU.md` for scope and limits.
