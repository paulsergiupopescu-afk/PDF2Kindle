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
