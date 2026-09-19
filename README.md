# pdf2kindle

Convert PDFs into clean, **reflowable Kindle-ready EPUBs** — not screenshots of
pages, but real ebooks that reflow on any screen, with justified text, an
organized chapter structure, a navigable table of contents, and footnotes turned
into Kindle pop-up notes.

Most "PDF → EPUB" tools either wrap each page in a fixed-layout image or dump a
wall of unstructured text. `pdf2kindle` does the harder thing: it analyses the
PDF's typography and geometry to *reconstruct* the book.

## Scope

This repository does one thing: turn a PDF into a Kindle-ready EPUB. It is a
standalone project — it is not a component of, and shares no code with, any
other project. Anything that is not PDF parsing, structure reconstruction, or
EPUB generation does not belong here.

## Designed like a printed book, not a text dump

Every chapter opens the way a well-typeset print book does: a small
letter-spaced chapter number over a large serif title, then the opening
paragraph set with a drop cap and a small-caps lead-in. Block quotes run in a
quiet italic inset; a chapter's endnotes are introduced by a centered dinkus
(`· · ·`) instead of a plain rule. None of it is decoration bolted on after
the fact — every flourish is generated from the same structural analysis that
finds chapters and footnotes, and degrades to plain, correct text whenever a
heading or paragraph doesn't cleanly fit the pattern (see `pdf2kindle/html.py`
for the exact rules). Kindle's renderer only honours a narrow slice of CSS,
so nothing here relies on floats being supported, embedded fonts, or script —
worst case on an older device, a flourish just renders as plain type.

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

## Journal articles

`--profile article` is the academic profile plus everything a paper needs that
a book does not:

- **Front matter as a section of its own** — title, byline, affiliation and
  contact are each recognized and set apart, instead of arriving as a run of
  anonymous paragraphs in front of the introduction.
- **The abstract is a real section**, with its own heading and nav entry, so
  it is visibly finished before section 1 starts. Keywords are set apart
  below it, and the publisher's copyright notice is moved out of the middle
  of the argument, where the PDF's reading order tends to strand it.
- **A generated cover** carrying just the title and the author. A thumbnail
  of a paper's first page is a wall of two-column type under a journal
  banner — unreadable at the size a library actually shows it.
- **No drop caps.** They are book typography; on a paper they read as
  decoration over the argument.

```bash
pdf2kindle convert book.pdf    -o book.epub               # profile detected
pdf2kindle convert paper.pdf   -o paper.epub  --profile article
pdf2kindle convert novel.pdf   -o novel.epub  --profile general
```

## What it does

- **Reading-order & paragraph reconstruction** — merges the PDF's fragmented
  lines back into real paragraphs, de-hyphenates words split across lines,
  rejoins a line PDF extraction split into word-fragments over extreme
  justified spacing, and handles simple multi-column layouts.
- **Chapter organization** — uses the PDF's own bookmarks/outline when present,
  otherwise detects headings from font-size clustering — including a heading
  wrapped over two or three lines, and one numbered without a space after its
  dot ("1.Literature overview") — and splits the book into one navigable
  chapter per section with a full EPUB3 + NCX table of contents. A printed
  Contents page's dot-leader rows ("Introduction .......... 4") are never
  mistaken for real headings, however boldly Word styled them.
- **Word spaces the typesetter never emitted** — some PDFs encode a word
  boundary as nothing but a wider gap between characters, so the text comes
  out as `Thestateisoneofseriesofconcepts`. The boundary is measured back in
  from the character geometry (and the same pass restores the missing space
  in `question.Thus`), on the pages that need it and no others.
- **Two-column pages** — the gutter is found from the page's own whitespace
  rather than guessed at the half-way mark, because it is not reliably wider
  than the gaps inside a justified line. Columns are read within horizontal
  bands, so a bibliography stacked beneath two columns of notes does not
  interleave with them.
- **Profile detection** — with no flags, `pdf2kindle` decides for itself
  whether it is looking at a paper or a book, from the presence of an
  abstract in the opening pages and the document's length.
- **Headings from the document's own outline** — where a PDF has bookmarks,
  they name the headings and their depths, which beats inferring a heading
  from font size. It has to: a journal sets its section headings a third of a
  point above body text and its subsection headings *below* it, so no
  size-based rule can see them at all.
- **Verse keeps its line breaks** — a quoted poem arrives as one paragraph
  per line, and set as prose it comes out justified and indented, which is
  exactly wrong. A run of short, indented lines is kept as one block with its
  breaks intact.
- **Typographic nuance** — preserves **bold** / *italic* runs, small-caps and
  superscripts, block quotes, and headings; weight is read from subsetted
  font names (`AdvOT…B`) as well as flags, and a lone punctuation mark pulled
  from a bold subset for want of a glyph takes the weight of its neighbours
  rather than printing a stray bold "?" mid-title; body text is **justified** with
  automatic hyphenation, tuned for Kindle's renderer.
- **Footnote management** — detects superscript reference markers and the
  matching notes at the foot of the page, then re-links them as EPUB3
  `noteref`/`footnote` pairs so Kindle shows them as tappable pop-ups (with
  back-links) instead of stranding them mid-text. A block quote or epigraph
  set at the same small size as footnotes, sitting just above them, is
  recognized and kept in the body rather than taking the real footnotes down
  with it.
- **Images** — embeds figures and illustrations inline at their reading position.
- **Tables are always pictures, never reflowed** — a grid's meaning lives in
  the alignment of its cells, and a reflowing reader cannot preserve that.
  Extracted as text a table does not degrade gracefully, it shreds: the cells
  of a row interleave into nonsense ("Aspect Cyprus Malta", "rule", "Ottoman
  to"). So a table region — found by its bracket of rules, or by a "Table 3."
  caption over a run of fragmented multi-column lines — is rendered as one
  image at the position it occupied, with its caption and any source note
  inside the picture, and the shredded cells dropped from the text.
- **Maps and diagrams** — a page that is really vector art (a map's borders,
  rivers, a chart's lines) has nothing for a text/image extractor to find but
  the scatter of labels drawn on top; such pages are rendered as one picture
  instead of scattering "50", "I", "C" through the surrounding chapter as
  bogus paragraphs.
- **Journal articles, not just books** — an outline whose single level-1
  bookmark is the article title is descended past, so the paper splits into
  its real sections; endnotes printed once in a *Notes* section at the end are
  moved to the chapters that cite them, so every marker is a working in-file
  pop-up link; a bibliography typeset smaller than the body is recognized as
  body text rather than discarded as unparseable footnotes; and a typesetter's
  job number left in the PDF's `/Title` ("NPS_2400088 1..23") loses to the
  outline's own root.
- **Typography repair** — folds ligature glyphs (`ﬁ`→`fi`) so Kindle search and
  dictionary lookup work, converts `` ``quoted'' `` to real curly quotes,
  rebuilds split fractions (`51⁄2` → `5½`), and collapses the padding spaces
  justified typesetting can bake into the text stream (`"KEITH  HITCHINS"`).
- **Broken-font watermarks are filtered, not garbled into the text** — some
  PDFs (library "downloaded from" copies especially) stamp a footer in a
  font with no usable character map; the extractor recognizes the resulting
  raw control-character noise and drops it, rather than leaving it in a
  chapter as gibberish.
- **Self-audit** — every conversion ends with a quality report (linked notes,
  dead links, stylesheet, cover, stray page furniture); run it any time with
  `pdf2kindle audit book.epub`.
- **Scanned PDFs** — when a page is image-only, it falls back to **OCR**
  (Tesseract) so scanned books still become searchable, reflowable text.
- **Repairing someone else's bad OCR** (`--repair-ocr`) — a scanned book
  usually arrives with a text layer already baked in by whatever digitized it,
  and where that pass misread the scan it leaves real prose as nonsense
  (`descoperă` as `«It scoperă`, `academice` as `.iradcmice`). Replacing such a
  page wholesale with a fresh OCR pass reads better but throws away the
  per-glyph size and style geometry that heading, footnote and running-head
  detection depend on — so each *line* is arbitrated on its own instead: a
  fresh OCR of it is accepted only when a **dictionary for the book's own
  language** says it reads as clearly better words, and only its characters
  are replaced, leaving the line's measurements untouched. A line the existing
  layer got right is left alone, and so is one neither pass can read — a Greek
  or Slavonic quotation in a Romanian book is never "corrected" into Romanian.
  Needs Tesseract plus a hunspell dictionary for `--ocr-lang` (for Romanian:
  `apt-get install tesseract-ocr-ron hunspell-ro`).

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
git clone https://github.com/paulsergiupopescu-afk/PDF2Kindle.git
cd PDF2Kindle
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
  text.py        typography repair (ligatures, quotes, fractions)
  analyze.py     reading order, body-font detection, header/footer stripping,
                 line → paragraph reconstruction, de-hyphenation
  structure.py   heading detection + chapter splitting (outline or font clusters)
  footnotes.py   marker ↔ note detection and pairing (footnotes and endnotes)
  ocr.py         Tesseract fallback for image-only pages
  spelling.py    dictionary lookups, to judge which OCR reading is real words
  html.py        semantic, Kindle-tuned XHTML + CSS generation
  epub.py        EPUB3 assembly (ebooklib): nav, ncx, metadata, cover
  audit.py       quality report over a produced EPUB
  convert.py     orchestrator
  cli.py         command line
  server.py      FastAPI app + static frontend
web/             TypeScript + React (Vite) drag-and-drop UI
```

## Limitations

Perfect conversion of an arbitrary PDF is not possible — PDFs describe *ink on a
page*, not document structure. `pdf2kindle` reconstructs structure heuristically
and does very well on prose-heavy books; heavily designed layouts (magazines,
textbooks with sidebars) are best-effort. Tables are not reflowed at all —
they are reproduced as pictures, which keeps them readable but means their
text is not searchable or selectable. Run `pdf2kindle audit` on the result: it
reports what it could not resolve rather than leaving you to find it on the
device.

## License

MIT — see [LICENSE](LICENSE).
