"""Generate a PDF whose transliterated words carry free-standing accent glyphs.

Transliterated Punjabi is often typeset with its macrons and dots drawn as
separate glyphs -- a hyphen or full stop raised or lowered over its letter --
and the PDF draws them *between* the two halves of the row they decorate. That
is the shape that defeats a line-based extractor: the macrons arrive as
stray "- -" lines in the middle of a sentence, the row is cut in two, and
"fire" comes out as "fi" and "re".

This reproduces it with the standard Helvetica font: a row of prose is drawn in
two pieces, with the accents for the second piece drawn between them.
"""
import sys
import pymupdf

FONT, SIZE = "helv", 10.5
LEFT, LEADING = 72.0, 14.0
RAISE, DROP = 3.5, 2.0  # how far an accent sits off the baseline

# Each row: the text, and accents as (index of the letter in the row, kind).
# "macron" is drawn raised, "dot" lowered under the letter.
ROWS = [
    ("Humans are also made aware of their spatiality, their materiality.", []),
    # ... "pani" -> pāṇī, "patal" -> pātāl; the row is split just after "fi".
    ("We are basically elements, air (pavan), water (pani), fire (agni), earth (patal), and",
     [("pani", 1, "macron"), ("pani", 2, "dot"), ("pani", 3, "macron"), ("patal", 1, "macron"), ("patal", 3, "macron")]),
    ("their compounds. Humans are not set apart from other species, nor is the living set apart.", []),
]
SPLIT_AFTER = "fi"  # the second half of the row starts at "re (agni)"


def _width(text):
    return pymupdf.get_text_length(text, fontname=FONT, fontsize=SIZE)


def _draw_accents(page, row, y, accents):
    for word, idx, kind in accents:
        start = row.index(word)
        x = LEFT + _width(row[: start + idx])
        w = _width(row[start + idx])
        glyph, dy = ("-", -RAISE) if kind == "macron" else (".", DROP)
        gw = _width(glyph)
        page.insert_text((x + (w - gw) / 2, y + dy), glyph, fontsize=SIZE, fontname=FONT)


def main(out="tests/accents.pdf"):
    doc = pymupdf.open()
    page = doc.new_page()
    y = 90.0
    for row, accents in ROWS:
        cut = row.index(SPLIT_AFTER + "re") + len(SPLIT_AFTER) if accents else None
        if cut is None:
            page.insert_text((LEFT, y), row, fontsize=SIZE, fontname=FONT)
        else:
            first, second = row[:cut], row[cut:]
            page.insert_text((LEFT, y), first, fontsize=SIZE, fontname=FONT)
            # the accents of the whole row are drawn here, between the halves
            _draw_accents(page, row, y, accents)
            page.insert_text((LEFT + _width(first), y), second, fontsize=SIZE, fontname=FONT)
        y += LEADING
    doc.set_metadata({"title": "Accents", "author": "A. Marker"})
    doc.save(out)
    doc.close()
    print("wrote", out)


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "tests/accents.pdf")
