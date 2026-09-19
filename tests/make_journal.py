"""Generate a journal-article PDF: the shape a published paper actually has.

Exercises what a book-shaped fixture cannot:

* an outline whose single level-1 root is the article title, with the real
  sections hanging off it at level 2;
* a ruled comparison table with a "Table 1." caption;
* endnotes numbered continuously and printed once, in a Notes section at the
  end, far from the markers that cite them;
* a bibliography typeset smaller than the body text;
* a production code in the PDF's /Title, the way a typesetter leaves it;
* the front matter an article opens with -- byline, affiliation, contact,
  abstract, keywords -- and a publisher's notice printed at the page foot; and
* a quoted poem, whose line breaks are its content.
"""
import sys
import pymupdf

CSS = """
body { font-family: serif; font-size: 11px; line-height: 1.5; text-align: justify; }
h1 { font-family: sans-serif; font-size: 20px; }
h2 { font-family: sans-serif; font-size: 13px; }
sup { font-size: 7px; vertical-align: super; }
"""
SMALL_CSS = CSS + "body { font-size: 8px; }"

INTRO = """
<h2>1 Introduction</h2>
<p>This article examines how island communities came to describe themselves in
national terms, and why the vocabulary they reached for so often belonged to a
mainland.<sup>1</sup> The question is not merely terminological. What a community
calls itself shapes which futures it can imagine, and which it cannot.<sup>2</sup></p>
<p>The argument proceeds in three stages, moving from the administrative record
to the literary one, and finally to the comparative frame that the table below
sets out.</p>
"""

COMPARISON = """
<h2>2 Comparative frame</h2>
<p>Placing the case beside its neighbours makes the pattern legible in a way no
single archive can.<sup>3</sup> The comparison is summarised below.</p>
"""

AFTER_TABLE = """
<p>As the table shows, a community possessing a language of its own proved far
likelier to articulate its identity in local rather than borrowed terms.<sup>4</sup>
The exceptions are instructive, and the remainder of this section takes them in
turn before returning to the central case.</p>
"""

FICTION = """
<h2>3 The evidence of fiction</h2>
<p>Novels and periodicals record what official correspondence omits: the texture
of an argument as it was actually conducted.<sup>5</sup> The periodical press is
especially valuable here, since it published both sides. One verse of the period
put the case plainly:</p>
"""

# A quoted poem: short lines, indented well inside the measure.
VERSE_LINES = [
    "People of the island, near and far,",
    "Together with your neighbour,",
    "You have a common interest,",
    "Work together now.",
]

FRONT = """
<p>Jane Doe</p>
<p>Department of History, University of Somewhere</p>
<p>Email: jane.doe@example.edu</p>
<p>Abstract</p>
<p>This article examines how island communities came to describe themselves in
national terms during the long nineteenth century, and argues that the
vocabulary they adopted was borrowed rather than local. It draws on
administrative records, the periodical press, and a comparative frame covering
four neighbouring cases.</p>
<p>Keywords: islands; nationalism; identity; empire</p>
"""

NOTES = """
<h2>Notes</h2>
<p>1 On the general problem, see the foundational survey, which frames the
debate that follows and remains the standard point of departure.</p>
<p>2 This formulation is deliberately strong; a weaker version would still
support the argument advanced here.</p>
<p>3 The comparison is restricted to cases sharing an imperial administrative
history, for reasons set out in the appendix.</p>
<p>4 The correlation is suggestive rather than decisive, given the small number
of cases available for comparison.</p>
<p>5 See in particular the serialised fiction of the period, discussed at length
in the secondary literature.</p>
"""

REFS = """
<h2>References</h2>
<p>Anderson, B. 1983. Imagined Communities. London: Verso.</p>
<p>Bauman, Z. 1991. Modernity and Ambivalence. Cambridge: Polity Press.</p>
<p>Constantakopoulou, C. 2007. The Dance of the Islands. Oxford: Oxford
University Press.</p>
<p>Gellner, E. 1983. Nations and Nationalism. Oxford: Blackwell.</p>
<p>Herzfeld, M. 1988. The Poetics of Manhood. Princeton: Princeton University
Press.</p>
<p>Hobsbawm, E., and T. Ranger, eds. 1983. The Invention of Tradition.
Cambridge: Cambridge University Press.</p>
"""

# (label, cyprus, malta, crete) -- kept short so the cells wrap the way a real
# table's do, which is what shreds into nonsense when extracted as text.
ROWS = [
    ("Aspect", "Cyprus", "Malta", "Crete"),
    ("Historical context", "Ottoman to British rule", "British rule",
     "Ottoman to autonomous"),
    ("Identity", "Greek, Turkish", "Maltese", "Greek, Muslim"),
    ("Religion", "Orthodox Christian, Muslim", "Catholic Christian",
     "Orthodox Christian"),
    ("Language", "Greek, Turkish", "Maltese, Italian", "Greek, Turkish"),
]
COLS = [56, 150, 260, 360]
RIGHT = 452


def htmlbox(page, html, top=72, bottom=110, left=56, right=56, css=CSS):
    r = pymupdf.Rect(left, top, page.rect.width - right, page.rect.height - bottom)
    page.insert_htmlbox(r, html, css=css)


def draw_table(page, top):
    """A ruled table: caption, then rows bracketed by full-width rules."""
    page.insert_text((56, top), "Table 1. Comparative Analysis of Insular Identity",
                     fontsize=9, fontname="helv")
    y = top + 10
    page.draw_line(pymupdf.Point(56, y), pymupdf.Point(RIGHT, y), width=0.8)
    for r, row in enumerate(ROWS):
        y += 12
        for x, cell in zip(COLS, row):
            page.insert_text((x, y), cell, fontsize=8,
                             fontname="hebo" if r == 0 else "helv")
        # Wrapped second lines, as a real table's cells produce.
        if r == 1:
            y += 9
            page.insert_text((150, y), "rule", fontsize=8, fontname="helv")
            page.insert_text((360, y), "and then Greek rule", fontsize=8, fontname="helv")
        y += 4
        page.draw_line(pymupdf.Point(56, y), pymupdf.Point(RIGHT, y), width=0.4)
    return y


def main(out="tests/journal.pdf"):
    doc = pymupdf.open()

    p1 = doc.new_page()
    p1.insert_text((56, 60), "ARTICLE", fontsize=8, fontname="hebo")
    htmlbox(p1, "<h1>Who are the islanders? Identity on the periphery</h1>"
                + FRONT + INTRO, top=70)
    p1.insert_text(
        (56, 700),
        "\u00a9 The Author(s), 2024. Published by Example University Press. This is "
        "an Open Access article distributed under a Creative Commons licence.",
        fontsize=6, fontname="helv",
    )

    p2 = doc.new_page()
    htmlbox(p2, COMPARISON, top=72, bottom=560)
    end = draw_table(p2, 300)
    htmlbox(p2, AFTER_TABLE, top=end + 14)

    p3 = doc.new_page()
    htmlbox(p3, FICTION, bottom=420)
    # Verse, indented past the body's left edge and far short of the measure.
    y = 330
    for line in VERSE_LINES:
        p3.insert_text((92, y), line, fontsize=11, fontname="tiro")
        y += 17

    p4 = doc.new_page()
    htmlbox(p4, NOTES)

    # The bibliography is set smaller than the body -- the whole page lands in
    # the footnote zone, and must not be discarded for failing to parse as notes.
    p5 = doc.new_page()
    htmlbox(p5, REFS, css=SMALL_CSS)

    # One level-1 root carrying the title; every real section beneath it.
    doc.set_toc([
        [1, "Who are the islanders? Identity on the periphery", 1],
        [2, "1 Introduction", 1],
        [2, "2 Comparative frame", 2],
        [2, "3 The evidence of fiction", 3],
        [2, "Notes", 4],
        [2, "References", 5],
    ])
    doc.set_metadata({"title": "JNL_2400088 1..5", "author": ""})
    doc.save(out)
    doc.close()
    return out


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "tests/journal.pdf")
