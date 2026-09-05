"""Generate a small book-like sample PDF for testing the converter."""
import sys
import pymupdf

BODY_CSS = """
body { font-family: serif; font-size: 11px; line-height: 1.5; text-align: justify; }
h1 { font-family: sans-serif; font-size: 22px; }
h2 { font-family: sans-serif; font-size: 16px; }
sup { font-size: 7px; vertical-align: super; }
"""

CH1 = """
<h1>Chapter One</h1>
<p>It was the best of times, it was the worst of times, it was the age of
wisdom, it was the age of foolishness. The travellers pressed on through the
long night, uncertain of what the dawn would bring but certain that they must
keep moving.<sup>1</sup> Nobody spoke.</p>
<p>In the morning the valley opened before them, wide and green and utterly
silent. A single bird crossed the pale sky. They descended slowly, mindful of
the loose stones, and did not look back at the mountains they had left.</p>
<p>By noon the heat was tremendous. They rested in the shade of an old wall
whose origins none of them could name, and shared the last of the water.</p>
"""

CH2 = """
<h1>Chapter Two</h1>
<p>The city, when they reached it, was smaller than the stories had promised.
Its famous towers were half in ruins, and the great market square held only a
handful of quiet stalls.<sup>2</sup> Still, it was a city, and after the empty
country it felt like a kind of miracle.</p>
<p>They found lodging above a bakery. The <b>warm smell of bread</b> rose
through the floorboards all night, and for the first time in weeks they slept
without dreaming of the road.</p>
<p>What happened next is the subject of the <i>remainder of this account</i>,
which the careful reader will find both stranger and more ordinary than any
rumour yet recorded.</p>
"""

FOOT1 = "1  A reference to the well-known opening; see the notes for full context."
FOOT2 = "2  The market had, in earlier centuries, been the largest for a hundred miles."


def add_footnote(page, text):
    rect = pymupdf.Rect(72, page.rect.height - 90, page.rect.width - 72, page.rect.height - 60)
    page.insert_textbox(rect, text, fontsize=8, fontname="helv")


def main(out="tests/sample.pdf"):
    doc = pymupdf.open()

    p1 = doc.new_page()
    r = pymupdf.Rect(72, 72, p1.rect.width - 72, p1.rect.height - 110)
    p1.insert_htmlbox(r, CH1, css=BODY_CSS)
    add_footnote(p1, FOOT1)

    p2 = doc.new_page()
    r = pymupdf.Rect(72, 72, p2.rect.width - 72, p2.rect.height - 110)
    p2.insert_htmlbox(r, CH2, css=BODY_CSS)
    add_footnote(p2, FOOT2)

    # Running header + page numbers to exercise header/footer stripping.
    for i, pg in enumerate(doc):
        pg.insert_text((72, 40), "THE LONG ROAD", fontsize=8, fontname="helv")
        pg.insert_text((pg.rect.width / 2 - 5, pg.rect.height - 40), str(i + 1),
                       fontsize=9, fontname="helv")

    doc.set_metadata({"title": "The Long Road", "author": "A. Traveller"})
    doc.set_toc([[1, "Chapter One", 1], [1, "Chapter Two", 2]])
    doc.save(out)
    doc.close()
    print("wrote", out)


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "tests/sample.pdf")
