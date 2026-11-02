# Collins EPUB repair and audit

Input: `A_Primer_of_Ecclesiastical_Latin_-_John_F._Collins_endnotes.epub`

Reference: `A Primer of Ecclesiastical Lat - Collins, John F._7478(1).pdf`

## Changes made to the EPUB

- Rebuilt the EPUB 3 navigation document, legacy NCX, and visible Contents page from the book's actual unit / section structure.
- Grouped the detached Unit 4 `Drills` and `Exercises` documents under Unit 4 rather than exposing them as top-level book divisions.
- Grouped continuation reading extracts (Units 24, 27, and 34) under their corresponding units.
- Kept all printed chapter and subsection text intact; added a stable `a. Vowels` navigation target in Unit 1.
- Preserved the book's original XHTML chapters, images, tables, vocabulary, Latin macrons, endnote text, and note links.
- Updated the OPF `dcterms:modified` timestamp.

## Validation

- ZIP integrity: passed (`testzip()` returned no damaged member).
- `mimetype`: first archive entry, uncompressed.
- XML/XHTML/OPF/NCX files parsed: 58 / 58.
- Broken internal files or fragments: 0.
- Unit navigation entries: Units 1–35 each appear exactly once.
- Endnote bodies: 961.
- Linked endnote references: 1,016; all reference destinations resolve.
- Spine items: 54.
- Page-break landmarks: 451. Printed pages 376 and 410 have no page-break marker in the supplied EPUB; their text remains present.
- Five isolated running folios remain in the extracted reading text (212, 242, 312, 376, 410 where present); these are advisory layout artifacts, not broken EPUB references.

## Repository auditor fix

The `audit.py` improvement in this PR:
- Reads chapter documents from the OPF manifest and spine rather than hardcoding `EPUB/chap_###.xhtml`.
- Counts `epub:type="footnote"` and `epub:type="endnote"` across documents.
- Resolves cross-document note links, back-links, images, stylesheets, and navigation targets relative to the source XHTML path.
- Treats dead internal fragments as audit failures.
- Adds regression tests for OEBPS paths, external endnote documents, and broken navigation fragments.

## Scope note

The original EPUB's full text was retained. This repair focuses on the navigational and package-level issues confirmed by comparison with the supplied PDF. It does not attempt wholesale OCR or editorial correction of the book's Latin/English text.
