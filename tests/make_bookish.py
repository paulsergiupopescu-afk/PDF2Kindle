"""Generate a book-like PDF: running heads, folios, cross-page paragraphs, footnotes.

This mirrors the shape of a real academic monograph and is the regression
fixture for page-furniture stripping and footnote pairing.
"""
import sys
import pymupdf

CSS = """
body { font-family: serif; font-size: 11px; line-height: 1.55; text-align: justify; }
h1 { font-family: sans-serif; font-size: 20px; }
sup { font-size: 7px; vertical-align: super; }
"""

PAGES = [
    """<h1>Introduction</h1>
<p>It is important to understand rightly the claim that law is for the common
good. The commonplace that law is for the common good is a commonplace about
whom law is to benefit, or to whom law is supposed to be justifiable; it is not
supposed to be a claim about the proper form of legislative authority.<sup>1</sup>
One might think, for example, that to say that law is for the common good must
commit one to a certain view about the way that law is made, and that it must,
perhaps, be made</p>""",
    """<p>democratically. But that would be to prejudge difficult philosophical
questions about the best regimes, for example, whether monarchy, or aristocracy,
or democracy is the best form of governance.<sup>2</sup> Whether the many or the
few should rule is a question that is distinct from that of the orientation of
law to the common good.</p>
<p>The idea that law is for the common good is a relatively thin one. That it is
a commonplace is perhaps most eloquently affirmed by those most interested in
evading its apparent implications, and it is to those evasions that we now</p>""",
    """<p>turn. More positively, we can note the prevalence of appeals to the
common good in the law's self-image: just as legal systems affirm their own
status as authoritative, they affirm their orientation toward the common
good.<sup>3</sup></p>""",
]

NOTES = [
    "1 See the discussion of legislative authority in the collected essays, especially\nthe chapters on political obligation and consent.",
    "2 The classical arguments against democracy are surveyed at length in the\nstandard commentaries.",
    "3 Note the way officials defend decisions so as to be justifiable to all.",
]


def main(out="tests/bookish.pdf"):
    doc = pymupdf.open()
    for i, html in enumerate(PAGES):
        pg = doc.new_page()
        pg.insert_htmlbox(
            pymupdf.Rect(72, 80, pg.rect.width - 72, pg.rect.height - 150), html, css=CSS
        )
        # Footnote at the foot of the page, in smaller type.
        pg.insert_textbox(
            pymupdf.Rect(72, pg.rect.height - 140, pg.rect.width - 72, pg.rect.height - 95),
            NOTES[i], fontsize=8.5, fontname="times-roman",
        )
        # Page furniture: running head + folio, present on every page.
        pg.insert_text((72, 48), "Introduction", fontsize=9, fontname="times-roman")
        pg.insert_text((pg.rect.width / 2, pg.rect.height - 45), str(i + 10),
                       fontsize=9, fontname="times-roman")
    doc.set_metadata({"title": "Law and the Common Good", "author": "M. Author"})
    doc.save(out)
    doc.close()
    print("wrote", out)


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "tests/bookish.pdf")
