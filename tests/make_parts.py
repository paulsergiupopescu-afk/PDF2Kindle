"""Generate a publisher-style book PDF with the structure that tripped up
real conversions: chapters nested under "Part" bookmarks, running heads that
carry their page's folio ("Classic teaching on original sin 13") in chapters
too short for the head to repeat often, a publisher's logo stamped on every
blank verso, a cover page with text drawn over its art, a chapter title
wrapped after a colon, a vector diagram with text labels, and a two-column
index.
"""
import sys

import pymupdf

W, H = 432, 648  # 6 x 9 in
LEFT, RIGHT = 54, W - 54
BODY = 10.5
LEAD = 14.0
# Printed folio = PDF page index - this offset (page 6 of the PDF is "1").
FOLIO_OFFSET = 5

PROSE = (
    "The doctrine has a long and tangled history which lends itself to many "
    "variations of interpretation, and churches have commonly taught simplified "
    "versions of a complex and subtle tradition to believers who were less "
    "tolerant of ambiguity than the tradition itself. "
)


def _logo_png() -> bytes:
    pix = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, 400, 120), False)
    pix.set_rect(pix.irect, (200, 200, 200))
    pix.set_rect(pymupdf.IRect(20, 20, 100, 100), (90, 90, 90))
    return pix.tobytes("png")


def _cover_png() -> bytes:
    pix = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, 600, 900), False)
    pix.set_rect(pix.irect, (30, 30, 30))
    pix.set_rect(pymupdf.IRect(60, 200, 540, 260), (230, 150, 40))
    return pix.tobytes("png")


def _wrap(text: str, width: float, size: float = BODY) -> list:
    words, lines, cur = text.split(), [], ""
    for w in words:
        cand = f"{cur} {w}".strip()
        if pymupdf.get_text_length(cand, fontname="tiro", fontsize=size) > width and cur:
            lines.append(cur)
            cur = w
        else:
            cur = cand
    if cur:
        lines.append(cur)
    return lines


def _prose(page, y: float, paragraphs: int, *, stop: float = H - 70) -> float:
    for _ in range(paragraphs):
        lines = _wrap(PROSE * 3, RIGHT - LEFT - 14)
        for i, ln in enumerate(lines):
            if y > stop:
                return y
            x = LEFT + (14 if i == 0 else 0)
            page.insert_text((x, y), ln, fontname="tiro", fontsize=BODY)
            y += LEAD
    return y


def _running_head(page, number: int, chapter_head: str) -> None:
    """One line holding the head and its folio, at nearly body size -- small
    enough to be furniture, too close to body size to be caught as such."""
    folio = number - FOLIO_OFFSET
    if folio % 2:  # recto: chapter title then folio
        x = LEFT + 120
        page.insert_text((x, 40), chapter_head, fontname="tiit", fontsize=10)
        x += pymupdf.get_text_length(chapter_head + " ", fontname="tiit", fontsize=10)
        page.insert_text((x, 40), str(folio), fontname="tiro", fontsize=10)
    else:  # verso: folio then book title
        page.insert_text((LEFT, 40), str(folio), fontname="tiro", fontsize=10)
        page.insert_text((LEFT + 20, 40), "Original Selfishness", fontname="tiit", fontsize=10)


def _blank_with_logo(doc, logo: bytes) -> None:
    p = doc.new_page(width=W, height=H)
    p.insert_image(pymupdf.Rect(60, 60, 300, 132), stream=logo)


def _chapter_opening(page, title_lines, *, label="", y=150.0) -> float:
    if label:
        # The small "CHAPTER ONE" over the title, in the running-head band.
        page.insert_text((W / 2 - 30, 56), label, fontname="tiro", fontsize=8)
    for t in title_lines:
        page.insert_text((LEFT, y), t, fontname="tibo", fontsize=20)
        y += 40
    return y + 30


def main(out="tests/parts.pdf"):
    doc = pymupdf.open()
    logo, cover = _logo_png(), _cover_png()

    # 0: cover -- full-bleed art with the title also present as text.
    p = doc.new_page(width=W, height=H)
    p.insert_image(p.rect, stream=cover)
    p.insert_text((60, 240), "Original Selfishness", fontname="tibo", fontsize=30, color=(1, 0.6, 0.1))
    p.insert_text((60, 600), "Daryl P. Domning and Monika K. Hellwig", fontname="tibo", fontsize=12, color=(1, 1, 1))

    # 1: blank verso with the publisher's logo.
    _blank_with_logo(doc, logo)

    # 2: title page.
    p = doc.new_page(width=W, height=H)
    p.insert_text((LEFT, 200), "Original Selfishness", fontname="tibo", fontsize=26)
    p.insert_text((LEFT, 240), "DARYL P. DOMNING", fontname="tibo", fontsize=12)

    # 3: blank verso with the logo again.
    _blank_with_logo(doc, logo)

    # 4: Part One title page; 5: blank logo verso.
    p = doc.new_page(width=W, height=H)
    p.insert_text((LEFT, 200), "Part One", fontname="tibo", fontsize=22)
    p.insert_text((LEFT, 232), "The historical background", fontname="tibo", fontsize=22)
    _blank_with_logo(doc, logo)

    # 6-8: Chapter 1 (three pages: only one recto running head after the opening page).
    p = doc.new_page(width=W, height=H)
    y = _chapter_opening(p, ["The classic teaching on original sin"], label="CHAPTER ONE")
    _prose(p, y, 3)
    for i in range(2):
        p = doc.new_page(width=W, height=H)
        _running_head(p, p.number, "Classic teaching on original sin")
        y = 58
        if i == 0:
            # A section head set bold at body size, wrapped onto a second
            # line: invisible to font-size heading detection.
            for ln in ("1.1 The Fall Was Not a Single Event in Time, Nor Was It",
                       "Committed by One Couple in a Garden"):
                p.insert_text((LEFT, y), ln, fontname="tibo", fontsize=BODY)
                y += LEAD
            y += 10
        _prose(p, y, 4)

    # 9: Part Two title page.
    p = doc.new_page(width=W, height=H)
    p.insert_text((LEFT, 200), "Part Two", fontname="tibo", fontsize=22)
    p.insert_text((LEFT, 232), "Why the old view is untenable", fontname="tibo", fontsize=22)
    p.insert_text((LEFT, 290), "Daryl P. Domning", fontname="tibo", fontsize=14)

    # 10-12: Chapter 2, title wrapped after a colon.
    p = doc.new_page(width=W, height=H)
    y = _chapter_opening(p, ["The Genesis cosmogony disproven:", "the universe is ancient and large"],
                         label="CHAPTER TWO")
    y = _prose(p, y, 1)
    # A bulleted list whose items hang their wrapped lines under the text.
    # (The base-14 fonts have no bullet glyph, so it comes from another font.)
    bullet = pymupdf.Font("cjk")
    for i, ln in enumerate(("The story is about our sinfulness: we are each Adam and Eve.",
                            "All that is now keeping us out of Eden, that is the Kingdom, is our",
                            "present sins, not some sin of the primordial past.")):
        if i < 2:
            tw = pymupdf.TextWriter(p.rect)
            tw.append((LEFT, y), "\u2022", font=bullet, fontsize=BODY)
            tw.write_text(p)
        p.insert_text((LEFT + 12, y), ln, fontname="tiro", fontsize=BODY)
        y += LEAD
    _prose(p, y + 6, 2)
    for _ in range(2):
        p = doc.new_page(width=W, height=H)
        _running_head(p, p.number, "The Genesis cosmogony disproven")
        _prose(p, 58, 4)

    # 13-14: Chapter 3, with a vector diagram whose labels are text.
    p = doc.new_page(width=W, height=H)
    y = _chapter_opening(p, ["Adam and Eve reinterpreted"], label="CHAPTER THREE")
    y = _prose(p, y, 1)
    top = y + 10
    shape = p.new_shape()
    shape.draw_rect(pymupdf.Rect(LEFT + 10, top, LEFT + 130, top + 40))
    shape.draw_rect(pymupdf.Rect(RIGHT - 130, top, RIGHT - 10, top + 40))
    shape.draw_rect(pymupdf.Rect(LEFT + 100, top + 120, RIGHT - 100, top + 160))
    shape.draw_line((LEFT + 70, top + 40), (LEFT + 140, top + 120))
    shape.draw_line((RIGHT - 70, top + 40), (RIGHT - 140, top + 120))
    shape.draw_curve((LEFT + 20, top + 60), (W / 2, top + 30), (RIGHT - 20, top + 60))
    shape.finish(color=(0, 0, 0), width=0.8)
    shape.commit()
    p.insert_text((LEFT + 20, top + 25), "COMMON ORIGIN", fontname="helv", fontsize=8)
    p.insert_text((RIGHT - 120, top + 25), "HUMAN FREE WILL", fontname="helv", fontsize=8)
    p.insert_text((LEFT + 110, top + 145), "Original Sin", fontname="helv", fontsize=8)
    p.insert_text((LEFT + 50, top + 85), "(evolution)", fontname="helv", fontsize=8)
    p.insert_text((RIGHT - 110, top + 85), "(all humans)", fontname="helv", fontsize=8)
    cap_y = top + 185
    p.insert_text((LEFT, cap_y), "Figure 3.1 The composite origin of original sin.", fontname="tibo", fontsize=9)
    _prose(p, cap_y + 25, 1)
    p = doc.new_page(width=W, height=H)
    _running_head(p, p.number, "Adam and Eve reinterpreted")
    _prose(p, 58, 4)

    # 15: blank logo verso before the back matter.
    _blank_with_logo(doc, logo)

    # 16: two-column index.
    p = doc.new_page(width=W, height=H)
    p.insert_text((LEFT, 90), "Index of subjects", fontname="tibo", fontsize=18)
    left_entries = [
        "Abelard, Peter 13, 152", "absolute monarchy 134-135, 168", "actual sin 118, 140-141, 145,",
        "    149, 157, 163", "adoption 128-132", "aggression 5, 58, 102, 104",
        "aging 80, 82", "altruism 28, 48-50, 56, 67", "anthropic principle 110",
        "apes 41, 45, 49, 58-59", "apoptosis 79, 107", "atheism 63-64, 70",
    ]
    right_entries = [
        "cooperation 29, 48-49, 50, 68", "covenant 86, 122, 125", "creationism 1, 6, 41, 46,",
        "    54, 58, 63, 65-69", "cross of Jesus 126, 134", "cruelty 52-53, 115, 165",
        "cyclic universe 83-84, 87", "Daly, Gabriel 132, 179", "darkening of intellect 47-48",
        "Darwin, Charles 2, 24, 39", "death 3, 14, 26, 47, 51", "deceit 63, 102-103",
    ]
    y = 130
    for le, ri in zip(left_entries, right_entries):
        for x, entry in ((LEFT, le), (W / 2 + 10, ri)):
            # A continuation line is indented by position, as typeset.
            indent = 12 if entry.startswith(" ") else 0
            p.insert_text((x + indent, y), entry.strip(), fontname="tiro", fontsize=8.5)
        y += 12

    # 17: bibliography with hanging-indent entries.
    p = doc.new_page(width=W, height=H)
    p.insert_text((LEFT, 90), "Bibliography", fontname="tibo", fontsize=18)
    refs = [
        'Alszeghy, Z. 1967. "Development in the doctrinal formulation of the Church concerning the theory of evolution." pp. 25-33 in J. Metz (ed.), The evolving world and theology. Concilium, vol. 26.',
        'Alters, B. J. and S. M. Alters. 2001. Defending evolution: a guide to the creation/evolution controversy. Jones and Bartlett Publishers, Boston, MA.',
        'Ayala, F. J. 1995. "The myth of Eve: molecular biology and human origins." Science 270: 1930-1936.',
        'Pennock, R. T. (ed.). 2001. Intelligent design creationism and its critics. Bradford Books/MIT Press, Cambridge, MA.',
        'Petit, C. W. 1998. "Touched by nature: putting evolution to work on the assembly line." US News & World Report 125(4): 43-45.',
    ]
    y = 130
    for ref in refs:
        for i, ln in enumerate(_wrap(ref, RIGHT - LEFT - 14, 9.5)):
            p.insert_text((LEFT + (14 if i else 0), y), ln, fontname="tiro", fontsize=9.5)
            y += 12.5

    doc.set_toc([
        [1, "Cover", 1],
        [1, "Title Page", 3],
        [1, "Part One The historical background", 5],
        [2, "1 The classic teaching on original sin", 7],
        [3, "1.1 Was the Fall one event?", 8],
        [1, "Part Two Why the old view is untenable", 10],
        [2, "The Genesis cosmogony disproven: the universe is ancient and large", 11],
        [2, "Adam and Eve reinterpreted", 14],
        [1, "Index of subjects", 17],
        [1, "Bibliography", 18],
    ])
    doc.set_metadata({"title": "Original Selfishness", "author": "Daryl P. Domning"})
    doc.save(out)
    doc.close()
    return out


if __name__ == "__main__":
    print("wrote", main(*(sys.argv[1:] or [])))
