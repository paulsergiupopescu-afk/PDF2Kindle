# pdf2kindle

## What this repo is

A standalone tool that converts PDFs into clean, reflowable Kindle-ready
EPUBs — reconstructed chapters, a navigable TOC, justified body text, and
footnotes re-linked as Kindle pop-up notes.

Repository: `paulsergiupopescu-afk/PDF2Kindle`.

## What this repo is not

This project is **self-contained**. It is not a module, plugin, or subsystem
of any other project, and it has no dependency on one.

In particular, it is unrelated to `paulsergiupopescu-afk/f1-telemetry-hub`.
The two repos share no code and no purpose. Do not add pdf2kindle code to
f1-telemetry-hub, and do not add telemetry, racing, or unrelated
application code here. If a task mentions both, the PDF→EPUB work belongs
in this repository only.

Keep the scope tight: PDF parsing, structure reconstruction, and EPUB
generation. Anything else does not belong here.

## Layout

```
pdf2kindle/
  extract.py     PDF -> structured spans/lines/blocks (PyMuPDF)
  text.py        typography repair (ligatures, quotes, fractions)
  analyze.py     reading order, body-font detection, header/footer stripping,
                 line -> paragraph reconstruction, de-hyphenation
  structure.py   heading detection + chapter splitting (outline or font clusters)
  footnotes.py   marker <-> note detection and pairing (footnotes and endnotes)
  ocr.py         Tesseract fallback for image-only pages
  spelling.py    dictionary lookups, to judge which OCR reading is real words
  html.py        semantic, Kindle-tuned XHTML + CSS generation
  epub.py        EPUB3 assembly (ebooklib): nav, ncx, metadata, cover
  audit.py       quality report over a produced EPUB
  convert.py     orchestrator
  cli.py         command line
  server.py      FastAPI app + static frontend
web/             TypeScript + React (Vite) drag-and-drop UI
tests/           pytest suite + generated PDF fixtures
```

## Setup

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e ".[all]"     # core + OCR + web server extras
```

Optional system packages: `tesseract-ocr` for scanned PDFs, plus a hunspell
dictionary matching `--ocr-lang` when using `--repair-ocr`.

## Running

```bash
pdf2kindle convert book.pdf -o book.epub          # academic profile (default)
pdf2kindle convert novel.pdf -o novel.epub --profile general
pdf2kindle audit book.epub                        # quality report
pdf2kindle serve                                  # local web UI on :8000
```

## Tests

```bash
python -m pytest tests/ -q
```

The suite generates its PDF fixtures (`tests/academic.pdf`, `bookish.pdf`,
`endnotes.pdf`) via the `tests/make_*.py` scripts; they are gitignored and
rebuilt on demand.

## Conventions

- `web/dist` is committed on purpose so `pdf2kindle serve` works without
  Node installed. Rebuild it with `cd web && npm install && npm run build`
  whenever you change anything under `web/src`.
- Kindle's renderer supports only a narrow slice of CSS. Do not rely on
  floats, embedded fonts, or script; every typographic flourish must degrade
  to plain, correct text.
- Structure detection is heuristic. When adding a rule, add a test PDF case
  alongside it in `tests/test_convert.py`.

## Standing conversion rules

These are settled decisions, not defaults to revisit per book.

- **A table is always reproduced as an image, never reflowed as text. No
  exceptions.** The same goes for maps and diagrams. A grid's meaning is
  carried by the alignment of its cells, which no reflowing reader preserves;
  extracted as text it shreds, interleaving the cells of a row into nonsense.
  Render the region (`_extract_table_images` in `extract.py`), place the
  picture where the table sat, pull the caption and any source note into the
  image, and drop the shredded lines from the text.
- **Never discard text silently.** If a heuristic cannot classify a block,
  it must fall back to emitting it as body text. Two bugs of exactly this
  shape have already cost whole sections: a bibliography set smaller than the
  body was dropped as unparseable footnotes, and a notes-only chapter was
  dropped for having no body elements. Prefer slightly wrong output to
  missing output.
- **Footnote and endnote markers must end up as working links**, including
  when the notes are printed once at the end of the document and the markers
  that cite them live in other chapters. Notes move to the chapter citing
  them so the link stays in-file.
- **A clean reading interface always wins over the original formatting.**
  This produces a reflowable ebook, not a facsimile. When the two conflict,
  the page's appearance loses every time: drop the print furniture, let the
  text reflow, and do not reproduce a layout merely because the PDF had it.
  The table rule above is not an exception to this — a table becomes a
  picture because reflowed cells are *unreadable*, not because the printed
  grid is worth preserving.

## How this program is built

**The goal is the algorithm, not a model.** Every conversion must be
something the code does deterministically, on its own, from the PDF's own
geometry and typography. Do not reach for an LLM to read a page, repair a
heading, or decide what a block is — if a document defeats the current
heuristics, the answer is a better heuristic in the source, never a model
call at conversion time.

**Every PDF is a test case, and leaves the program better than it found
it.** When a new book or paper is converted, the job is not finished when
the EPUB is handed over. Work it as follows:

1. Convert it, then actually inspect the result — chapter split, note
   linking, tables, dropped text, metadata. Compare word counts against the
   source when anything looks thin; silent loss is the failure mode that
   hides best.
2. Every fault found is a fault in the program, not in that one file. Fix it
   in the engine, generically, so the next document of that shape converts
   correctly untouched.
3. Add a fixture and a test for the shape that broke (`tests/make_*.py` plus
   a case in `tests/test_convert.py`), so it cannot regress.
4. Commit the improvement. The corpus of handled shapes only grows.

Earlier converted documents are worth consulting as worked examples of what
good output looks like for a given genre.
