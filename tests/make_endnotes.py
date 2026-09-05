"""Generate a PDF using chapter-end endnotes with a hanging indent.

This is the shape that defeats naive converters: markers are numbered
continuously through the chapter, the notes live many pages later under a
"Notes" heading, they are set with a hanging indent (wider labels hang further
left), and the list spills onto a second page.
"""
import sys
import pymupdf

CSS = """
body { font-family: serif; font-size: 11px; line-height: 1.5; text-align: justify; }
h1 { font-family: sans-serif; font-size: 19px; }
sup { font-size: 7px; vertical-align: super; }
"""

BODY = [
    """<h1>Chapter 1 The Argument</h1>
<p>The first consideration tells against the received view.<sup>1</sup> A second
consideration, closely related, tells against it as well.<sup>2</sup> Neither is
decisive on its own, but together they are suggestive.<sup>3</sup></p>""",
    """<p>The third consideration is more subtle.<sup>4</sup> It depends on a
distinction that is easy to state but hard to apply.<sup>5</sup> We take up that
distinction in the following section.<sup>6</sup></p>""",
    """<p>A fourth line of argument proceeds differently.<sup>7</sup> It begins
from premises that few would dispute.<sup>8</sup> The conclusion, however, is
far from obvious.<sup>9</sup> We defend it at length.<sup>10</sup></p>""",
]

# (label, [lines]) — first line carries the label, the rest are continuations.
NOTES = [
    ("1", ["The case is a version of that offered by Bertrand Russell in Human Know-",
           "ledge: Its Scope and Limits (New York: Allen and Unwin, 1948), p. 154."]),
    ("2", ["John Austin, The Province of Jurisprudence Determined, ed. Wilfrid Rumble",
           "(Cambridge: Cambridge University Press, 1995 [originally published 1832])."]),
    ("3", ["Ibid., Lecture I, pp. 21, 25."]),
    ("4", ["Ibid., Lecture I, pp. 21-2."]),
    ("5", ["Ibid., Lecture VI, pp. 168-9."]),
    ("6", ["Hart, The Concept of Law, 2nd edn. (Oxford: Clarendon Press, 1994),",
           "pp. 26-78."]),
    ("7", ["Ibid., p. 28."]),
    ("8", ["Ibid., pp. 29-33."]),
    ("9", ["Raz, 'Authority, Law, and Morality,' Monist 68 (1985), pp. 295-",
           "324."]),
    ("10", ["Ibid., pp. 192-200."]),
]

LABEL_X, CONT_X = 72.0, 88.0


def draw_notes(page, notes, y):
    """Hanging indent: labels right-aligned into the gutter, text at CONT_X."""
    for label, lines in notes:
        page.insert_text((LABEL_X + (0 if len(label) > 1 else 5), y),
                         f"{label} {lines[0]}", fontsize=9, fontname="times-roman")
        y += 12
        for cont in lines[1:]:
            page.insert_text((CONT_X, y), cont, fontsize=9, fontname="times-roman")
            y += 12
    return y


def main(out="tests/endnotes.pdf"):
    doc = pymupdf.open()
    for html in BODY:
        pg = doc.new_page()
        pg.insert_htmlbox(pymupdf.Rect(72, 80, pg.rect.width - 72, pg.rect.height - 90),
                          html, css=CSS)
        pg.insert_text((72, 48), "The Argument", fontsize=9, fontname="times-roman")

    # Notes section, spilling across two pages.
    p1 = doc.new_page()
    p1.insert_text((72, 48), "The Argument", fontsize=9, fontname="times-roman")
    p1.insert_text((250, 100), "Notes", fontsize=13, fontname="helv")
    draw_notes(p1, NOTES[:6], 130)

    p2 = doc.new_page()
    p2.insert_text((72, 48), "The Argument", fontsize=9, fontname="times-roman")
    draw_notes(p2, NOTES[6:], 100)

    doc.set_metadata({"title": "The Argument", "author": "E. Noter"})
    doc.save(out)
    doc.close()
    print("wrote", out)


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "tests/endnotes.pdf")
