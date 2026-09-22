# Structure analysis

A snapshot review of how `pdf2kindle` is organized: module layering, where the
complexity sits, and the seams that have drifted. Measured against the tree at
the time of writing (16 Python modules, ~4,100 lines of package code, 138
functions, 77 tests).

## 1. Layering

The internal import graph is acyclic and cleanly staged — each module depends
only on earlier pipeline stages plus the shared vocabulary in `model.py`:

```
model            (no internal deps — shared dataclasses)
text, spelling   (no internal deps — pure helpers)
ocr              -> model
extract          -> model, ocr, spelling
analyze          -> model
document_analysis-> model
footnotes        -> model, text
structure        -> analyze, document_analysis, extract, footnotes, model, text
html             -> model
epub             -> html, model
audit            -> (no internal deps — reads a finished EPUB)
convert          -> analyze, document_analysis, epub, extract, model, ocr, structure
server           -> convert, ocr
cli              -> audit, convert, server
```

`model` is depended on by 9 of 16 modules and depends on nothing — the right
shape for a pipeline that passes a progressively-enriched document tree between
stages. `audit` deliberately has no package dependencies: it re-reads the
produced EPUB from disk, so it validates the output rather than the in-memory
model it came from. That independence is worth preserving.

## 2. The pipeline

`convert.convert_pdf` is the single orchestrator, and it is readable — 86 lines,
only 5 branches, most of its length being progress reporting and result
assembly:

| Stage | Module | Input → output |
|---|---|---|
| Read | `extract.extract` | PDF → `Page[]` of typed spans/lines/images (+ OCR fallback, OCR repair) |
| Classify | `document_analysis.classify_pages` | `Page[]` → per-page `PageType` with weighted evidence |
| Analyze | `analyze.analyze` | `Page[]` → `Analyzed` (body font, reading order, furniture stripped, notes split off) |
| Structure | `structure.build_document` | `Analyzed` → `Document` of chapters/elements/footnotes |
| Render | `html` + `epub.build_epub` | `Document` → XHTML + EPUB3 on disk |
| Verify | `audit.audit_epub` | EPUB → quality report |

Two design choices stand out as good ones. First, every heuristic degrades to
plain correct text rather than failing — `structure.py` is a stack of
`Optional`-returning detectors (`_is_heading`, `_table_from_group`,
`_is_blockquote`) where `None` simply means "treat as a paragraph". Second, the
`Element`/`InlineRun` model is renderer-agnostic: `html.py` is the only module
that knows about XHTML, so a second output format would not touch the analysis
stages.

## 3. Where complexity concentrates

`structure.py` is 1,103 lines — a quarter of the package, and 2.7× the next
largest module. It holds at least six separable concerns:

1. inline run construction (`_append_text`, `_paragraph_runs`)
2. element classification (`_is_heading`, `_is_blockquote`, `_table_from_group`, `_is_headless_toc_page`)
3. flow assembly and repair (`_build_flow`, `_merge_split_headings`, `_merge_split_paragraphs`)
4. chapter splitting (`_split_by_toc`, `_split_by_headings`)
5. academic post-processing (`_extract_endnotes`, `_style_references`, `_assign_nav`)
6. metadata inference (`_guess_title`, `_guess_author`)

Splitting it into a `structure/` package along those lines would be mechanical —
the groups barely call across each other, and `build_document` already reads as
a sequence of those six phases. Nothing forces the change today; it is the one
file where a newcomer has to page past unrelated code to find anything.

Function-level density is otherwise healthy (average 20 LOC). The branchiest
functions are `_is_heading` (79 LOC / 26 branch points),
`classify_pages` (73 / 26) and `extract` (91 / 24) — all genuinely
heuristic-heavy, all covered by tests, so the branch counts reflect the problem
rather than tangled control flow.

## 4. Seams that have drifted

**Duplicated document statistics.** `analyze.py` and `document_analysis.py`
each compute the same four measurements over the same `Page[]`, with
independently written implementations:

| Measurement | `analyze.py` | `document_analysis.py` |
|---|---|---|
| body font size | `_dominant_body_size` | `_body_size` |
| median line height | `_median_line_height` | `_line_height` |
| dominant left margin | `_dominant_left` | `_body_left` |
| margin repeats | `_margin_repeats` | `_margin_repeats` |

They are near-identical but not identical, and the differences are the kind that
bite later: `document_analysis._body_size` rounds span sizes to one decimal
before counting, `analyze._dominant_body_size` does not — so on a PDF using
10.98pt and 11.02pt body text the two stages can disagree about what "body size"
is. Likewise `analyze` reads its margin bands from the `_TOP_BAND`/`_BOT_BAND`
constants while `document_analysis` hardcodes `0.14`/`0.86`; tuning one no
longer tunes the other. `classify_pages` already accepts a `stats_seed`
parameter that would let `convert` compute these once and pass them to both —
the seam exists, it is just never used (`convert.py` calls `classify_pages(pages)`
with no seed).

**A private import across layers.** `structure.py` imports `_column_count` from
`extract.py`. That reaches from the last analysis stage back into the first, and
through a private name. Column counting is a geometry primitive with no PDF
dependency; it belongs in `analyze.py` (or a shared geometry helper) where both
callers can use it publicly.

**Two analysis passes over raw pages.** `classify_pages` and `analyze` both walk
the unmodified `Page[]` independently. That is defensible — classification must
see the page furniture that `analyze` strips — but it is undocumented, and it is
the root cause of the duplication above. A one-line comment in `convert.py`
saying why the order is `classify → analyze` and not `analyze → classify` would
save the next reader the trace.

## 5. Interface gaps

**`--preserve-page-breaks` is unreachable from the web UI.** The option is
plumbed through `cli.py → convert.ConvertOptions → structure.build_document`,
and `server.py` accepts `preserve_page_breaks` as a form field — but nothing in
`web/src` ever sends it. It is absent from the `ConvertOptions` interface in
`web/src/types.ts`, from the form assembly in `App.tsx`, and from the committed
`web/dist` bundle. The API exposure landed; the UI control did not. Anyone using
the web app gets `False` with no way to change it.

**Audit runs on the CLI path only.** `cli.py` calls `audit_epub` after every
conversion and prints the report. `server.py` does not — the web response
carries counts (pages, chapters, footnotes, images, warnings) but no quality
signals. The README's "every conversion ends with a quality report" is true of
the CLI and not of the web app.

**Server state is in-process and unbounded.** `_JOBS` is a module-level dict and
the temp files under `tempfile.gettempdir()/pdf2kindle` are never reaped, so a
long-lived `pdf2kindle serve` accumulates both. Fine for the stated
single-user-local scope; worth a comment saying that is the intended scope, since
nothing in the module says so.

## 6. Tests and build

77 tests pass (76 passed, 1 skipped). Coverage is concentrated where the risk is:
`test_convert.py` (1,049 lines, 73 tests) exercises the heuristic detectors
directly by their private names plus end-to-end conversions over three generated
fixture PDFs (`make_sample`, `make_academic`, `make_bookish`, `make_endnotes`).

Gaps: no test imports `server.py` (the whole HTTP layer is untested), and
`epub.py` is only reached end-to-end, never directly. `test_convert.py` is itself
a 1,049-line monolith covering seven modules — splitting it to mirror the package
would make failures easier to place.

Dependency declarations are also split three ways and disagree:
`pyproject.toml` marks `pytest` nowhere, `requirements.txt` lists the optional
OCR and server extras as if they were core, and CI installs `pytest` ad hoc after
`pip install -e .`. A `test` extra in `pyproject.toml` would make
`pip install -e '.[test]'` reproduce CI exactly. CI also tests only Python 3.11
while `pyproject.toml` claims `requires-python = ">=3.9"` — that claim is
currently unverified.

## Summary

The architecture is sound: acyclic, properly layered, with a shared data model
that keeps heuristics out of the renderer and the auditor independent of both.
Nothing here is structural debt that blocks work. The items worth acting on, in
order of value:

1. Wire the page-break control into the web UI, or drop it from the API.
2. Deduplicate the four document-statistics helpers via the existing
   `stats_seed` seam — the rounding mismatch is a latent bug, not just repetition.
3. Move `_column_count` out of `extract.py` into a shared home and make it public.
4. Declare a `test` extra and add Python 3.9 to the CI matrix (or raise the
   floor to 3.11).
5. Split `structure.py` into a package, and `test_convert.py` to match.
