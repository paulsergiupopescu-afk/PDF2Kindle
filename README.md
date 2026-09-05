# pdf2kindle

Convert PDFs into clean, **reflowable Kindle-ready EPUBs** — not screenshots of
pages, but real ebooks that reflow on any screen, with justified text, an
organized chapter structure, a navigable table of contents, and footnotes turned
into Kindle pop-up notes.

Most "PDF → EPUB" tools either wrap each page in a fixed-layout image or dump a
wall of unstructured text. `pdf2kindle` does the harder thing: it analyses the
PDF's typography and geometry to *reconstruct* the book.

## Optimized for academic books

Scholarly PDFs have structure that trips up generic converters. The default
**`academic` profile** handles it:

- **Multi-level numbered sections** (`2`, `2.1`, `2.1.3`, roman numerals) are
  detected and turned into a proper heading hierarchy.
- **Nested table of contents** — every sub-section becomes a child entry in the
  EPUB nav, so you can jump straight to §2.1 from the Kindle TOC.
- **Endnotes**, not just page-foot footnotes — a chapter-ending *Notes* section
  is parsed, its entries linked back to their in-text superscript markers, and
  re-emitted as Kindle **pop-up notes** with back-links.
- **Block quotes** (evenly-indented extracts) are set as real `<blockquote>`s.
- **Figure & table captions** ("Figure 2.1 …", "Table 3 …") are detected and
  styled.
- **Bibliographies** under a *References* / *Works Cited* / *Bibliography*
  heading get **hanging-indent** formatting so each entry is scannable.

Use `--profile general` (CLI) or the **Book type** menu (web app) for prose and
fiction, which uses a lighter reconstruction.

```bash
pdf2kindle convert thesis.pdf -o thesis.epub            # academic by default
pdf2kindle convert novel.pdf  -o novel.epub --profile general
```

## What it does

- **Reading-order & paragraph reconstruction** — merges the PDF's fragmented
  lines back into real paragraphs, de-hyphenates words split across lines, and
  handles simple multi-column layouts.
- **Chapter organization** — uses the PDF's own bookmarks/outline when present,
  otherwise detects headings from font-size clustering, and splits the book into
  one navigable chapter per section with a full EPUB3 + NCX table of contents.
- **Typographic nuance** — preserves **bold** / *italic* runs, small-caps and
  superscripts, block quotes, and headings; body text is **justified** with
  automatic hyphenation, tuned for Kindle's renderer.
- **Footnote management** — detects superscript reference markers and the
  matching notes at the foot of the page, then re-links them as EPUB3
  `noteref`/`footnote` pairs so Kindle shows them as tappable pop-ups (with
  back-links) instead of stranding them mid-text.
- **Images** — embeds figures and illustrations inline at their reading position.
- **Scanned PDFs** — when a page is image-only, it falls back to **OCR**
  (Tesseract) so scanned books still become searchable, reflowable text.

## Two ways to run it

Both are driven by the same engine.

### 1. Command line

```bash
pdf2kindle convert book.pdf -o book.epub --title "My Book" --author "Jane Doe"
```

### 2. Local web app

A drag-and-drop web UI (TypeScript + React) served by the local FastAPI backend:

```bash
pdf2kindle serve          # then open http://127.0.0.1:8000
```

## Install

```bash
git clone <this-repo>
cd pdf-to-kindle
python -m venv .venv && source .venv/bin/activate
pip install -e .

# Optional, for scanned PDFs:
#   Debian/Ubuntu: sudo apt-get install tesseract-ocr
#   macOS:         brew install tesseract
```

To build the web frontend from source:

```bash
cd web && npm install && npm run build     # emits web/dist, served by `pdf2kindle serve`
```

A prebuilt `web/dist` is committed, so `pdf2kindle serve` works without Node.

## Architecture

```
pdf2kindle/
  extract.py     PDF → structured spans/lines/blocks (PyMuPDF)
  analyze.py     reading order, body-font detection, header/footer stripping,
                 line → paragraph reconstruction, de-hyphenation
  structure.py   heading detection + chapter splitting (outline or font clusters)
  footnotes.py   superscript marker ↔ page-foot note detection and linking
  ocr.py         Tesseract fallback for image-only pages
  html.py        semantic, Kindle-tuned XHTML + CSS generation
  epub.py        EPUB3 assembly (ebooklib): nav, ncx, metadata, cover
  convert.py     orchestrator
  cli.py         command line
  server.py      FastAPI app + static frontend
web/             TypeScript + React (Vite) drag-and-drop UI
```

## Limitations

Perfect conversion of an arbitrary PDF is not possible — PDFs describe *ink on a
page*, not document structure. `pdf2kindle` reconstructs structure heuristically
and does very well on prose-heavy books; heavily designed layouts (magazines,
textbooks with sidebars, dense tables) are best-effort. See `--help` for tuning
knobs.

## License

MIT — see [LICENSE](LICENSE).
