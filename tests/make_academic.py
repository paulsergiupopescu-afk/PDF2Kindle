"""Generate an academic-style sample PDF (numbered sections, endnotes, refs)."""
import sys
import pymupdf

CSS = """
body { font-family: serif; font-size: 11px; line-height: 1.5; text-align: justify; }
h1 { font-family: sans-serif; font-size: 21px; }
h2 { font-family: sans-serif; font-size: 13px; }
sup { font-size: 7px; vertical-align: super; }
"""

CH_TOP = """
<h1>2 &nbsp;Methods</h1>
<p>This chapter sets out the methodological commitments of the study and situates
them within the broader literature on comparative analysis.<sup>1</sup> We proceed
in two stages.</p>
<h2>2.1 &nbsp;Data</h2>
<p>The dataset was assembled from three archival sources spanning the period under
review. Each record was coded independently by two researchers.</p>
"""

QUOTE = """<p>As one commentator memorably put it, the archive is never neutral but
always already a theatre of selection, in which the act of preservation is
indistinguishable from the act of erasure.</p>"""

CAPTION = "Figure 2.1  Distribution of coded records across the three archival sources."

CH_RESULTS = """
<h2>2.2 &nbsp;Results</h2>
<p>The analysis yields three findings of note, each of which bears on the central
hypothesis advanced in the introduction.<sup>2</sup> We discuss them in turn.</p>
"""

NOTES = """
<h2>Notes</h2>
<p>1. See especially the foundational discussion in the collected essays, which
frames the debate that follows.</p>
<p>2. A fuller treatment of these findings, with the underlying tables, is
provided in the supplementary appendix.</p>
"""

REFS = """
<h2>References</h2>
<p>Foucault, Michel. 1972. The Archaeology of Knowledge. London: Tavistock.</p>
<p>Steedman, Carolyn. 2001. Dust: The Archive and Cultural History. New Brunswick:
Rutgers University Press.</p>
"""


def htmlbox(page, html, top=72, bottom=110, left=72, right=72):
    r = pymupdf.Rect(left, top, page.rect.width - right, page.rect.height - bottom)
    page.insert_htmlbox(r, html, css=CSS)


def main(out="tests/academic.pdf"):
    doc = pymupdf.open()

    p1 = doc.new_page()
    htmlbox(p1, CH_TOP)
    # Indented block quote → its own box, left-inset to create real geometry.
    p1.insert_htmlbox(
        pymupdf.Rect(120, p1.rect.height - 250, p1.rect.width - 120, p1.rect.height - 170),
        QUOTE, css=CSS,
    )
    p1.insert_textbox(
        pymupdf.Rect(72, p1.rect.height - 150, p1.rect.width - 72, p1.rect.height - 120),
        CAPTION, fontsize=9, fontname="helv",
    )

    p2 = doc.new_page()
    htmlbox(p2, CH_RESULTS + NOTES)

    p3 = doc.new_page()
    htmlbox(p3, REFS)

    doc.set_metadata({"title": "Archives and Method", "author": "R. Scholar"})
    doc.set_toc([[1, "Methods", 1]])
    doc.save(out)
    doc.close()
    print("wrote", out)


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "tests/academic.pdf")
