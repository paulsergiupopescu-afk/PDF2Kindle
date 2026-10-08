"""Regressions from converting a real publisher's monograph (Part > Chapter
outline, folio-bearing running heads, logo-stamped blank versos, vector
diagrams, a two-column index and a hanging-indent bibliography)."""
import re
import zipfile
from collections import Counter

import pytest

from pdf2kindle import ConvertOptions, convert_pdf
from tests.make_parts import main as make_parts


@pytest.fixture(scope="module")
def parts_pdf(tmp_path_factory):
    out = tmp_path_factory.mktemp("data") / "parts.pdf"
    make_parts(str(out))
    return str(out)


def _convert(pdf, tmp_path, **opts):
    out = tmp_path / "book.epub"
    convert_pdf(pdf, str(out), ConvertOptions(ocr="never", **opts))
    with zipfile.ZipFile(out) as z:
        return {n: z.read(n) for n in z.namelist()}


def _chapters(files):
    return {n: v.decode("utf-8") for n, v in sorted(files.items())
            if re.search(r"chap_\d+\.xhtml$", n)}


def _body_text(html):
    return re.sub(r"<[^>]+>", " ", html.split("<body", 1)[1])


@pytest.fixture(scope="module")
def parts_epub(parts_pdf, tmp_path_factory):
    return _convert(parts_pdf, tmp_path_factory.mktemp("out"))


def test_chapters_inside_parts_get_their_own_files_and_nest_in_the_toc(parts_epub):
    nav = parts_epub["EPUB/nav.xhtml"].decode("utf-8")
    for title in ("1 The classic teaching on original sin",
                  "The Genesis cosmogony disproven: the universe is ancient and large",
                  "Adam and Eve reinterpreted"):
        assert f">{title}</a>" in nav
    # Each chapter nests inside its Part's <li>, not beside it.
    part_two = nav.index("Part Two Why the old view is untenable")
    part_li = nav[part_two:nav.index("Bibliography")]
    assert "<ol>" in part_li and "Adam and Eve reinterpreted" in part_li
    ncx = parts_epub["EPUB/toc.ncx"].decode("utf-8")
    assert "Adam and Eve reinterpreted" in ncx
    titles = [re.search(r"<title>(.*?)</title>", h).group(1) for h in _chapters(parts_epub).values()]
    assert "Adam and Eve reinterpreted" in titles


def test_running_head_with_folio_is_stripped_even_when_rare(parts_epub):
    text = " ".join(_body_text(h) for h in _chapters(parts_epub).values())
    assert "Classic teaching on original sin" not in text
    assert "The Genesis cosmogony disproven 1" not in text
    assert not re.search(r"Original Selfishness\s*\d", text)


def test_publisher_logo_on_blank_pages_is_not_a_figure(parts_epub):
    images = [n for n in parts_epub if n.startswith("EPUB/images/")]
    # Only the rendered diagram survives; the logo stamped on four blank
    # versos is dropped, and identical images would be stored once anyway.
    assert len(images) == 1


def test_cover_text_does_not_open_the_book(parts_epub):
    first = _chapters(parts_epub)["EPUB/chap_000.xhtml"]
    assert "Daryl P. Domning and Monika K. Hellwig" not in first


def test_title_wrapped_after_a_colon_is_one_heading(parts_epub):
    html = "".join(_chapters(parts_epub).values())
    assert ('<span class="chtitle">The Genesis cosmogony disproven: the universe is ancient and large'
            '</span></h1>') in html
    assert "the universe is ancient and large</h1>" not in html.replace("</span></h1>", "")


def test_vector_diagram_becomes_a_picture_not_label_text(parts_epub):
    html = next(h for h in _chapters(parts_epub).values() if "Figure 3.1" in h)
    text = _body_text(html)
    assert "COMMON ORIGIN" not in text and "(evolution)" not in text
    # The picture sits where the diagram was: before its caption.
    assert html.index("<img") < html.index("Figure 3.1")


def test_caption_at_the_foot_of_a_page_is_not_page_furniture(parts_epub):
    assert any('class="caption"' in h and "Figure 3.1" in h for h in _chapters(parts_epub).values())


def test_hanging_indent_bibliography_is_one_entry_per_paragraph(parts_epub):
    html = next(h for h in _chapters(parts_epub).values() if "<h1>Bibliography</h1>" in h)
    entries = re.findall(r'<p class="reference">(.*?)</p>', html)
    assert len(entries) == 5
    assert entries[0].startswith("Alszeghy") and "Concilium, vol. 26." in entries[0]
    assert entries[3].endswith("Bradford Books/MIT Press, Cambridge, MA.")
    assert "<h1>Books/MIT" not in html


def test_index_is_dropped_by_default(parts_epub):
    nav = parts_epub["EPUB/nav.xhtml"].decode("utf-8")
    assert "Index of subjects" not in nav


def test_kept_two_column_index_reads_column_by_column(parts_pdf, tmp_path):
    files = _convert(parts_pdf, tmp_path, keep_print_nav=True)
    html = next(h for h in _chapters(files).values() if "Index of subjects" in h)
    entries = re.findall(r'<p class="reference">(.*?)</p>', html)
    assert entries[:3] == ["Abelard, Peter 13, 152", "absolute monarchy 134-135, 168",
                           "actual sin 118, 140-141, 145, 149, 157, 163"]
    # The right-hand column follows the whole left-hand one.
    assert entries.index("cooperation 29, 48-49, 50, 68") == entries.index("atheism 63-64, 70") + 1


def test_table_markup_has_no_literal_backslash_n():
    from pdf2kindle.html import _render_element
    from pdf2kindle.model import Element, ElementKind

    el = Element(kind=ElementKind.TABLE, table_rows=[["a", "b"], ["1", "2"]])
    out = _render_element(el, lambda e: "", False, False)
    assert "\\n" not in out and "<tr><td>1</td><td>2</td></tr>" in out


def test_identical_images_are_stored_once(tmp_path):
    from pdf2kindle.epub import build_epub
    from pdf2kindle.model import Chapter, Document, Element, ElementKind, ImageBlock

    im = ImageBlock(data=b"\x89PNG fake", ext="png", bbox=(0, 0, 1, 1), width=100, height=100)
    doc = Document(title="T", chapters=[
        Chapter(title="A", elements=[Element(kind=ElementKind.IMAGE, image=im)]),
        Chapter(title="B", elements=[Element(kind=ElementKind.IMAGE, image=im)]),
    ])
    out = tmp_path / "x.epub"
    build_epub(doc, str(out))
    with zipfile.ZipFile(out) as z:
        assert [n for n in z.namelist() if "images/" in n] == ["EPUB/images/img_0.png"]


@pytest.mark.parametrize("left, right, forms, expected", [
    ("self", "interest", {}, "self-interest"),            # a self- compound
    ("self", "ish", {}, "selfish"),
    ("brother", "in-law", {}, "brother-in-law"),          # inside a longer compound
    ("evo", "lution", {}, "evolution"),                   # an ordinary break
    ("cultural", "transmission", {"cultural-transmission": 2}, "cultural-transmission"),
    ("by", "product", {"byproduct": 3, "by-product": 1}, "byproduct"),
])
def test_line_break_hyphen_follows_the_books_own_spelling(left, right, forms, expected):
    from pdf2kindle.text import BREAK_MARK, resolve_break_hyphens

    assert resolve_break_hyphens(left + BREAK_MARK + right, Counter(forms)) == expected


def test_soft_hyphen_break_is_always_dropped():
    from pdf2kindle.text import drop_break_hyphen

    assert drop_break_hyphen("compli\xad") == "compli"


def test_folio_offset_is_learned_from_the_margins():
    from pdf2kindle.analyze import _carries_folio, _edge_folios

    assert _edge_folios("Classic teaching on original sin 13") == [("arabic", 13)]
    assert _edge_folios("x Foreword") == [("roman", 10)]
    assert _carries_folio("Rejoinder 191", 204, {"arabic": 13})
    assert not _carries_folio("Rejoinder 191", 205, {"arabic": 13})


def test_chapter_label_over_title_is_kept_and_split(parts_epub):
    html = "".join(_chapters(parts_epub).values())
    assert ('<h1><span class="chnum">Chapter One</span>'
            '<span class="chtitle">The classic teaching on original sin</span></h1>') in html
    assert '<span class="chnum">Chapter Three</span>' in html


def test_author_byline_is_not_a_heading_or_toc_entry(parts_epub):
    html = "".join(_chapters(parts_epub).values())
    assert '<p class="byline">' in html and "Daryl P. Domning</strong></p>" in html
    assert "<h1>Daryl P. Domning</h1>" not in html
    nav = parts_epub["EPUB/nav.xhtml"].decode("utf-8")
    assert "Daryl P. Domning" not in nav


def test_outline_section_becomes_a_heading_even_at_body_size(parts_epub):
    html = "".join(_chapters(parts_epub).values())
    assert re.search(r'<h2 id="[^"]+">1\.1 The Fall Was Not a Single Event in Time, Nor Was It '
                     r'Committed by One Couple in a Garden</h2>', html)
    # The table of contents uses the outline's own concise wording.
    nav = parts_epub["EPUB/nav.xhtml"].decode("utf-8")
    assert ">1.1 Was the Fall one event?</a>" in nav


def test_bulleted_items_keep_their_wrapped_lines(parts_epub):
    html = "".join(_chapters(parts_epub).values())
    assert ("<p>• All that is now keeping us out of Eden, that is the Kingdom, is our "
            "present sins, not some sin of the primordial past.</p>") in html


def test_title_split_over_two_headings_joins_when_outline_says_so():
    from pdf2kindle.model import Element, ElementKind, InlineRun
    from pdf2kindle.structure import _join_title_headings

    els = [Element(kind=ElementKind.HEADING, level=1, runs=[InlineRun(text="Are we going anywhere?")]),
           Element(kind=ElementKind.HEADING, level=1, runs=[InlineRun(text="A static universe")]),
           Element(kind=ElementKind.PARAGRAPH, runs=[InlineRun(text="Body.")])]
    out = _join_title_headings(els, "7 Are we going anywhere? A static universe")
    assert [e.text for e in out] == ["Are we going anywhere? A static universe", "Body."]


def _line(text, x0, y0, size, x1=380.0):
    from pdf2kindle.model import Line, Span

    return Line(spans=[Span(text=text, font="Times", size=size, flags=0, color=0,
                            bbox=(x0, y0, x1, y0 + size), origin=(x0, y0 + size))],
                bbox=(x0, y0, x1, y0 + size))


def test_first_lines_of_an_endnote_page_are_not_a_running_head():
    from collections import Counter as C

    from pdf2kindle.analyze import _is_furniture

    first = _line("8. Duffy 1993, 331, expressing the views of Karl Rahner:", 52, 56, 7.8)
    second = _line("Grace is in the end more powerful, eschatologically,", 66, 66, 7.8)
    assert not _is_furniture(first, second, at_top=True, height=663, body_size=9.8,
                             line_height=11, repeats=C())
    head = _line("Objections to the Darwinian view of nature", 139, 36, 7.8)
    body = _line("To many people, however, this beautifully simple", 48, 56, 9.8)
    assert _is_furniture(head, body, at_top=True, height=663, body_size=9.8,
                         line_height=11, repeats=C())


def test_chapter_label_is_never_a_running_head():
    from collections import Counter as C

    from pdf2kindle.analyze import _is_furniture

    label = _line("CHAPTER FOUR", 182, 56, 8.0, x1=248)
    title = _line("Objections to the Darwinian", 98, 79, 19.5)
    assert not _is_furniture(label, title, at_top=True, height=663, body_size=9.8,
                             line_height=11, repeats=C())


def test_block_quote_line_opening_with_a_reference_tail_is_not_a_footnote():
    from pdf2kindle.analyze import _starts_with_marker

    assert not _starts_with_marker(_line("16] are not even about God, much less", 71, 483, 8.8), 9.8)
    assert _starts_with_marker(_line("16 Mendenhall 2001, 151.", 51, 483, 8.8), 9.8)


def test_prose_with_long_paragraph_endings_is_not_a_hanging_list():
    from pdf2kindle.structure import _is_hanging_indent

    lines = []
    y = 56.0
    for para in range(4):
        lines.append(_line("Opening line of a paragraph", 58, y, 9.8, x1=384)); y += 11
        for _ in range(6):
            lines.append(_line("a full justified line of prose", 48, y, 9.8, x1=384)); y += 11
        lines.append(_line("a long last line", 48, y, 9.8, x1=360)); y += 11
    assert not _is_hanging_indent(lines, 6.7, 384, 336)


def test_audit_flags_running_heads_split_headings_and_duplicate_images(tmp_path):
    from pdf2kindle.audit import audit_epub

    head = ('<?xml version="1.0" encoding="utf-8"?>\n<html xmlns="http://www.w3.org/1999/xhtml" '
            'xmlns:epub="http://www.idpf.org/2007/ops"><head><title>T</title>'
            '<link href="style.css" rel="stylesheet" type="text/css"/></head><body>')
    chap = (head + "<h1>The classic teaching on original sin</h1>\n<h1>the second line</h1>\n"
            "<p>Text.</p>\n<blockquote><p><em>Classic teaching on original sin </em>13</p></blockquote>\n"
            "</body></html>")
    out = tmp_path / "x.epub"
    with zipfile.ZipFile(out, "w") as z:
        z.writestr("mimetype", "application/epub+zip")
        z.writestr("EPUB/chap_000.xhtml", chap)
        z.writestr("EPUB/style.css", "")
        z.writestr("EPUB/cover.jpg", b"x")
        z.writestr("EPUB/images/img_0.png", b"same")
        z.writestr("EPUB/images/img_1.png", b"same")
    a = audit_epub(str(out))
    assert any("Classic teaching on original sin 13" in f for f in a.furniture)
    assert a.split_headings and "the second line" in a.split_headings[0]
    assert a.duplicate_images == ["EPUB/images/img_1.png = EPUB/images/img_0.png"]
