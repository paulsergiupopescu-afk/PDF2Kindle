# Recipe: Sri Guru Granth Sahib, English Version, Vol. I (Gopal Singh)

A dedicated build for one difficult book: Dr. Gopal Singh's verse translation
(Allied Publishers, 1960; 2005 reprint), from the 393-page scan distributed by
the Sri Satguru Jagjit Singh Ji eLibrary. The generic `pdf2kindle convert`
reads this scan badly: page furniture becomes headings, blackletter becomes
garbage, and the notes are lost. This recipe knows the book's structure.

## What the scan contains

| | |
|---|---|
| Pages | 393: front matter (roman), scripture pp. 1–337, glossary i–viii |
| Text | image scan at ~150 dpi with a hidden OCR layer; ~97% of words read correctly |
| Structure | 31 sections: Japu, So-Daru, Sohilā, Sri Rāg, Rāg Mājh, Rāg Gauri, and their sub-forms (Ashtapadis, Vārs, Sukhmani…) |
| Verse | line-for-line, with bracketed stanza numbers `[1]`, `[1-Pause]`, `[4-5-25]` |
| Furniture | chain-link border on every page, running numbers, watermark footer |
| Blackletter | the Mool Mantar invocation (long and short forms) and the title |
| Margins | "P. 241" = page (ang) of the original Gurmukhi text |
| Notes | per-page footnotes mixing English, Gurmukhi and Devanagari |

## The design

Old-school, modelled on the 1960 edition itself:

- **EB Garamond** (an old-style roman like the letterpress) for text;
  **Unifraktur Maguntia** blackletter for the invocations and title page, as printed.
- **Rubrication**: invocations, rubrics (Shaloka, Pauri) and ornaments in deep madder red
  (dark grey on e-ink).
- Verse set line for line, ragged right, with a hanging indent for wrapped lines; the
  *Pause* (rahāu) couplet inset in italic; stanza numbers in small italic brackets.
- Hymn titles in letter-spaced small caps with the mahala (M. 5) in italic.
- A drawn **chain band**, echoing the page border, opens each chapter; fleurons ❦ divide sections.
- Original-text page numbers kept in the margin; an **Ang index** links every ang 1–346.
- The printed page numbers become the Kindle **page-list** ("real page numbers").
- Footnotes become Kindle **pop-up notes**; Gurmukhi/Devanagari set in Noto Serif.
- Nehru's message is set as a letter with his signature cut from the scan.
- Cover: maroon buckram with gold blocking, drawn in code (`art.py`).

## Building

```bash
pip install pymupdf fonttools pillow lxml
apt install tesseract-ocr tesseract-ocr-pan tesseract-ocr-hin hunspell hunspell-en-gb hunspell-en-us

python fonts.py fonts/                          # fetch the OFL fonts
python ocr_pass.py SCAN.pdf ocr/                 # optional second OCR (~30 min, restartable)
python build.py SCAN.pdf ggs-vol1.epub --ocr ocr --fonts fonts
python qa.py ggs-vol1.epub --headings            # well-formedness, links, OCR debris
```

The output passes EPUBCheck 5.1 with no errors or warnings. Send it to Kindle with
*Send to Kindle* (EPUB is accepted directly); the embedded fonts appear as "Publisher Font".

## Hand-corrected pages and the coherency test

Machine repair leaves errors a reader will notice, so pages can be corrected by hand
against the scan. `corrections.py` defines a plain-text, line-per-element format
(`pNNN.txt` per PDF page: `v:` verse lines with `{1-Pause}` stanza numbers, `p:`/`p+:`
paragraphs, `h:` titles, `ang:` margin numbers, `n3:` notes, `[^3]` references,
`*italics*`). Any page present in the corrections directory replaces the OCR parse:

```bash
python build.py SCAN.pdf out.epub --ocr ocr --fonts fonts --corrections corrections/
python coherency.py corrections/ --from 20 --to 63
```

`coherency.py` checks every corrected page and the joins between pages: syntax, notes
numbered 1..n and each referenced once, stanza numbers in the translator's grammar
with the running hymn count advancing, the Japu's pauris 1..38, Ang numbers increasing,
paragraphs carried across page breaks, OCR debris, and unknown words.

The corrected transcriptions are the book's (copyrighted) text, so they are kept out
of this public repository.

## How the text is repaired

1. Lines are rebuilt from the OCR layer by baseline; border debris is dropped.
2. Every word is checked against hunspell plus the book's own vocabulary
   (`lexicon.py`: names, Panjabi terms, *Givest*/*Knoweth*). An unknown word
   is replaced by Tesseract's reading of the same spot, or by a known OCR
   confusion (b→h, l↔I, ii→ā…), only when that yields a known word.
3. Stanza numbers, the mahala and hymn titles are normalised against their own
   small grammars (`parse.py`, `headings.py`).
4. Footnote references are matched in order on each page, including superscripts
   the OCR glued to words ("ingrained9") or misread ("oneself?").
5. Ang numbers are fitted to the longest increasing sequence, restoring dropped digits.

## Known limits

- Gurmukhi and Devanagari in the notes come from OCR of a 150 dpi scan and are imperfect.
- Some italic words and rare names may still carry OCR errors (well under 1% of words).
- The text is © the author (1960); this recipe is for making a personal reading copy.
