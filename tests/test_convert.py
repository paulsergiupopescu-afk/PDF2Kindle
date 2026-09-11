"""End-to-end tests for the conversion pipeline."""
import os
import re
import zipfile

import pytest
from lxml import etree

from pdf2kindle import ConvertOptions, convert_pdf
from tests.make_sample import main as make_sample
from tests.make_academic import main as make_academic
from tests.make_bookish import main as make_bookish
from tests.make_endnotes import main as make_endnotes

HERE = os.path.dirname(__file__)


@pytest.fixture(scope="module")
def sample_pdf(tmp_path_factory):
    out = tmp_path_factory.mktemp("data") / "sample.pdf"
    make_sample(str(out))
    return str(out)


@pytest.fixture(scope="module")
def bookish_pdf(tmp_path_factory):
    out = tmp_path_factory.mktemp("data") / "bookish.pdf"
    make_bookish(str(out))
    return str(out)


@pytest.fixture(scope="module")
def academic_pdf(tmp_path_factory):
    out = tmp_path_factory.mktemp("data") / "academic.pdf"
    make_academic(str(out))
    return str(out)


def _read(z, suffix):
    name = next(n for n in z.namelist() if n.endswith(suffix))
    return z.read(name).decode("utf-8")


def test_basic_conversion(sample_pdf, tmp_path):
    out = tmp_path / "book.epub"
    result = convert_pdf(sample_pdf, str(out))
    assert out.exists()
    assert result.pages == 2
    assert result.chapters == 2  # split from the PDF outline
    assert result.footnotes == 2
    assert result.title == "The Long Road"
    assert result.author == "A. Traveller"


def test_epub_is_well_formed(sample_pdf, tmp_path):
    out = tmp_path / "book.epub"
    convert_pdf(sample_pdf, str(out))
    with zipfile.ZipFile(out) as z:
        assert z.namelist()[0] == "mimetype"
        for n in z.namelist():
            if n.endswith((".xhtml", ".opf", ".ncx", ".xml")):
                etree.fromstring(z.read(n))  # raises if malformed


def test_footnotes_are_linked(sample_pdf, tmp_path):
    out = tmp_path / "book.epub"
    convert_pdf(sample_pdf, str(out))
    with zipfile.ZipFile(out) as z:
        body = _read(z, "chap_000.xhtml")
    assert 'epub:type="noteref"' in body
    assert 'epub:type="footnote"' in body
    assert 'id="n0-1"' in body and 'href="#n0-1"' in body


def test_running_header_stripped(sample_pdf, tmp_path):
    out = tmp_path / "book.epub"
    convert_pdf(sample_pdf, str(out))
    with zipfile.ZipFile(out) as z:
        body = _read(z, "chap_000.xhtml")
    assert "THE LONG ROAD" not in body  # running head removed
    assert "<h1>Chapter One</h1>" in body


def test_dehyphenation_and_justify(sample_pdf, tmp_path):
    out = tmp_path / "book.epub"
    convert_pdf(sample_pdf, str(out))
    with zipfile.ZipFile(out) as z:
        css = _read(z, "style.css")
    assert "text-align: justify" in css
    assert "hyphens: auto" in css


def test_override_metadata(sample_pdf, tmp_path):
    out = tmp_path / "book.epub"
    result = convert_pdf(
        sample_pdf, str(out), ConvertOptions(title="Custom", author="Me", ocr="never")
    )
    assert result.title == "Custom"
    assert result.author == "Me"


# --------------------------------------------------------------------------- #
# Academic profile
# --------------------------------------------------------------------------- #


def test_academic_features(academic_pdf, tmp_path):
    out = tmp_path / "aca.epub"
    convert_pdf(academic_pdf, str(out), ConvertOptions(profile="academic", ocr="never"))
    with zipfile.ZipFile(out) as z:
        body = _read(z, "chap_000.xhtml")
        nav = _read(z, "nav.xhtml")
        # References is a named top-level division like any other (see
        # _CHAPTER_RE) and may land in its own chapter rather than staying a
        # sub-heading, so check across every chapter rather than assuming it
        # stays in chap_000.
        all_bodies = "".join(
            z.read(n).decode() for n in z.namelist()
            if n.startswith("EPUB/chap_") and n.endswith(".xhtml")
        )
    assert "<h1" in body and "<h2" in body           # multi-level headings
    assert "<blockquote>" in body                     # block quote detected
    assert 'class="caption"' in body                  # figure caption
    assert 'class="reference"' in all_bodies           # bibliography entries
    # Endnotes extracted from the "Notes" section and linked as pop-ups,
    # with every marker resolving to a real note:
    assert 'epub:type="noteref"' in body and 'epub:type="footnote"' in body
    refs = set(re.findall(r'epub:type="noteref"[^>]*href="#([^"]+)"', body))
    notes = set(re.findall(r'epub:type="footnote" id="([^"]+)"', body))
    assert refs and not (refs - notes)
    # Nested table of contents with sub-section links:
    assert 'href="chap_000.xhtml#sec-0-' in nav


def test_academic_nested_toc_has_subsections(academic_pdf, tmp_path):
    out = tmp_path / "aca.epub"
    convert_pdf(academic_pdf, str(out), ConvertOptions(profile="academic", ocr="never"))
    with zipfile.ZipFile(out) as z:
        nav = _read(z, "nav.xhtml")
    assert "Data" in nav and "Results" in nav


def test_general_profile_skips_academic_markup(academic_pdf, tmp_path):
    out = tmp_path / "gen.epub"
    convert_pdf(academic_pdf, str(out), ConvertOptions(profile="general", ocr="never"))
    with zipfile.ZipFile(out) as z:
        body = _read(z, "chap_000.xhtml")
    assert 'class="reference"' not in body
    assert 'class="caption"' not in body


# --------------------------------------------------------------------------- #
# Regressions from a real monograph conversion
# --------------------------------------------------------------------------- #


@pytest.fixture(scope="module")
def bookish_epub(bookish_pdf, tmp_path_factory):
    out = tmp_path_factory.mktemp("out") / "bookish.epub"
    convert_pdf(bookish_pdf, str(out), ConvertOptions(ocr="never"))
    return str(out)


def test_stylesheet_is_linked_in_every_chapter(bookish_epub):
    """ebooklib regenerates <head>; the CSS link must survive or nothing is
    justified in the reader."""
    with zipfile.ZipFile(bookish_epub) as z:
        assert "EPUB/style.css" in z.namelist()
        for n in z.namelist():
            if n.startswith("EPUB/chap_") and n.endswith(".xhtml"):
                assert "style.css" in z.read(n).decode(), f"{n} has no stylesheet link"


def test_cover_is_present(bookish_epub):
    with zipfile.ZipFile(bookish_epub) as z:
        assert any("cover" in n.lower() for n in z.namelist())
        assert "cover-image" in z.read("EPUB/content.opf").decode()


def test_page_furniture_is_stripped(bookish_epub):
    """Running heads and folios must not leak into the text."""
    with zipfile.ZipFile(bookish_epub) as z:
        body = _read(z, "chap_000.xhtml")
    assert not re.search(r"<p[^>]*>\s*Introduction\s*</p>", body)
    assert not re.search(r"<p[^>]*>\s*1[012]\s*</p>", body)


def test_no_dead_footnote_links(bookish_epub):
    """Every noteref must resolve to a real note in the same file."""
    with zipfile.ZipFile(bookish_epub) as z:
        for n in z.namelist():
            if not (n.startswith("EPUB/chap_") and n.endswith(".xhtml")):
                continue
            c = z.read(n).decode()
            refs = set(re.findall(r'epub:type="noteref"[^>]*href="#([^"]+)"', c))
            notes = set(re.findall(r'epub:type="footnote" id="([^"]+)"', c))
            assert not (refs - notes), f"{n}: dead note links {refs - notes}"


def test_all_footnotes_are_captured(bookish_epub):
    with zipfile.ZipFile(bookish_epub) as z:
        body = _read(z, "chap_000.xhtml")
    assert len(re.findall(r'epub:type="footnote"', body)) == 3


def test_paragraph_merged_across_page_break(bookish_epub):
    """A sentence broken by a page turn must read as one paragraph."""
    with zipfile.ZipFile(bookish_epub) as z:
        body = _read(z, "chap_000.xhtml")
    text = re.sub(r"<[^>]+>", "", body)
    assert "perhaps, be made democratically" in text
    assert "evasions that we now turn" in text


# --------------------------------------------------------------------------- #
# Chapter-end endnotes (hanging indent, continuous numbering, multi-page)
# --------------------------------------------------------------------------- #


@pytest.fixture(scope="module")
def endnotes_epub(tmp_path_factory):
    src = tmp_path_factory.mktemp("data") / "endnotes.pdf"
    make_endnotes(str(src))
    out = tmp_path_factory.mktemp("out") / "endnotes.epub"
    convert_pdf(str(src), str(out), ConvertOptions(ocr="never"))
    return str(out)


def test_endnotes_all_linked(endnotes_epub):
    """Markers numbered through the chapter must bind to notes gathered at its
    end, including notes that spill onto a second page."""
    with zipfile.ZipFile(endnotes_epub) as z:
        body = _read(z, "chap_000.xhtml")
    refs = set(re.findall(r'epub:type="noteref"[^>]*href="#([^"]+)"', body))
    notes = set(re.findall(r'epub:type="footnote" id="([^"]+)"', body))
    assert len(notes) == 10, f"expected 10 endnotes, got {len(notes)}"
    assert len(refs) == 10
    assert not (refs - notes)


def test_endnote_continuation_lines_are_joined(endnotes_epub):
    """A hanging-indent continuation belongs to the note above it, and a word
    split across the line break is rejoined."""
    with zipfile.ZipFile(endnotes_epub) as z:
        body = _read(z, "chap_000.xhtml")
    text = re.sub(r"<[^>]+>", "", body)
    assert "Human Knowledge: Its Scope and Limits" in text  # de-hyphenated
    assert "originally published 1832" in text              # continuation kept


def test_endnote_section_not_duplicated_in_body(endnotes_epub):
    """The printed Notes list is replaced by pop-up notes, not shown twice."""
    with zipfile.ZipFile(endnotes_epub) as z:
        body = _read(z, "chap_000.xhtml")
    main = body[: body.find("<section")]
    assert "Bertrand Russell" not in main


# --------------------------------------------------------------------------- #
# Typography, false-positive markers, packaging
# --------------------------------------------------------------------------- #


def test_typography_normalization():
    from pdf2kindle.text import normalize

    # Doubled single quotes standing in for double quotes.
    assert normalize("‘‘Legal Positivism’’") == "“Legal Positivism”"
    # Ligature glyphs break Kindle search and dictionary lookup.
    assert normalize("beneﬁt aﬃrmed ﬂow") == "benefit affirmed flow"
    # A fraction split into numerator / fraction slash / denominator.
    assert normalize("Positivism: 51⁄2 Myths") == "Positivism: 5½ Myths"
    assert normalize("pp. 199–227") == "pp. 199–227"  # en dash untouched


def _line(parts):
    """parts: (text, size, origin_y, superscript)"""
    from pdf2kindle.model import Line, Span

    spans = [
        Span(text=t, font="f", size=s, flags=1 if sup else 0, color=0,
             bbox=(0, 0, 1, 1), origin=(0, y))
        for t, s, y, sup in parts
    ]
    return Line(spans=spans, bbox=(0, 0, 1, 1))


def test_fraction_numerator_is_not_a_note_marker():
    """"5 1/2" must not turn its digits into footnote links."""
    from pdf2kindle.footnotes import find_markers

    ln = _line([("Positivism:", 9.5, 403.5, False), ("5", 9.5, 403.5, False),
                ("1", 6.7, 399.3, True), ("⁄", 9.5, 403.5, False),
                ("2", 6.7, 405.1, False)])
    assert find_markers(ln, body_size=10.5) == []


def test_real_superscript_is_still_a_marker():
    from pdf2kindle.footnotes import find_markers

    ln = _line([("authority.", 10.5, 200.7, False), ("7", 7.0, 197.1, True),
                (" One might", 10.5, 200.7, False)])
    assert [lbl for _, lbl in find_markers(ln, body_size=10.5)] == ["7"]


def test_superscript_detection_is_line_relative():
    """A section set smaller than body text must not read as all-superscript."""
    from pdf2kindle.footnotes import find_markers

    ln = _line([("found in Gardner, page", 9.5, 403.5, False), ("46", 9.5, 403.5, False)])
    assert find_markers(ln, body_size=10.5) == []


def test_no_bogus_page_list_in_nav(bookish_epub):
    """ebooklib lists every epub:type+id element as a page; ours are footnotes."""
    with zipfile.ZipFile(bookish_epub) as z:
        nav = z.read("EPUB/nav.xhtml").decode()
    assert "page-list" not in nav


def test_epub_zip_is_well_formed(bookish_epub):
    with zipfile.ZipFile(bookish_epub) as z:
        assert z.namelist()[0] == "mimetype"
        assert z.getinfo("mimetype").compress_type == zipfile.ZIP_STORED


def test_audit_reports_clean(bookish_epub):
    from pdf2kindle.audit import audit_epub

    report = audit_epub(bookish_epub)
    assert report.ok, report.as_dict()
    assert report.notes == 3 and not report.dead_links


# --------------------------------------------------------------------------- #
# Broken-font watermarks, map pages, headless front matter, whitespace
# --------------------------------------------------------------------------- #


def test_garbled_footer_text_is_filtered():
    """A footer/watermark drawn with a broken ToUnicode CMap decodes to raw
    control characters, not real glyphs; it must never reach body text."""
    from pdf2kindle.extract import _is_garbled

    assert _is_garbled(":DD$C\x0e\x04\x04\x19#\x1e #B9\x04\x06\x05 \x06\x05\x06")
    assert not _is_garbled("Cambridge University Press has no responsibility")
    assert not _is_garbled("")


def _lines_from_texts(specs):
    """specs: list of (text, x0). One word-per-line, at a fixed y-step."""
    from pdf2kindle.model import Line, Span

    out = []
    for i, (text, x0) in enumerate(specs):
        y = 100.0 + i * 12.0
        out.append(Line(
            spans=[Span(text=text, font="f", size=10.0, flags=0, color=0,
                        bbox=(x0, y, x0 + 40, y + 10), origin=(x0, y + 8))],
            bbox=(x0, y, x0 + 40, y + 10),
        ))
    return out


def test_map_page_detected_by_scatter_not_shape_alone():
    """A map's place-name labels scatter across many x-positions; a two-column
    table's fragments cluster into just a couple. Word-shortness alone must
    not be enough, or a Contents page reads as a map."""
    from pdf2kindle.extract import _looks_like_map

    # Two-column list: labels at x=110, page numbers at x=470 -- a table.
    table = _lines_from_texts(
        [("Introduction", 110), ("1", 470), ("Beginnings", 110), ("6", 470)] * 4
    )
    assert not _looks_like_map(table)

    # Scattered place-name labels at many distinct x-positions -- a map.
    import random
    random.seed(0)
    scattered = _lines_from_texts([(w, x) for w, x in zip(
        ["Napoca", "Apulum", "Dacia", "I", "C", "A", "50", "100", "km",
         "Tapae", "Sarmizegetusa", "Danube", "0", "150", "200"] * 2,
        [110, 240, 305, 400, 430, 460, 130, 180, 220, 270, 340, 390, 100, 160, 210] * 2,
    )])
    assert _looks_like_map(scattered)


def test_headless_toc_page_dropped(tmp_path):
    """A Contents page whose own heading was stripped upstream still gets
    dropped, via its table shape plus keyword confirmation."""
    from pdf2kindle.analyze import PageContent
    from pdf2kindle.structure import _is_headless_toc_page

    lines = _lines_from_texts([
        ("List", 110), ("of", 150), ("maps", 180), ("xii", 470),
        ("Acknowledgments", 110), ("xiii", 470), ("Introduction", 110),
        ("1", 470), ("Beginnings", 110), ("6", 470), ("Further", 110),
        ("reading", 160), ("311", 470), ("Index", 110), ("315", 470),
    ])
    pc = PageContent(number=2, width=595, height=842, ocr=False, body_lines=lines)
    assert _is_headless_toc_page(pc)

    # A short-lined epigraph/poem must NOT be swept up: single column, and no
    # front-matter keyword.
    poem = _lines_from_texts([(w, 110) for w in
        ["Roses", "are", "red", "violets", "are", "blue", "a", "short",
         "poem", "here", "for", "you"]])
    pc2 = PageContent(number=3, width=595, height=842, ocr=False, body_lines=poem)
    assert not _is_headless_toc_page(pc2)


def test_multispace_collapsed():
    """Justified typesetting can bake padding spaces into the text stream."""
    from pdf2kindle.text import normalize

    assert normalize("KEITH  HITCHINS   University of Illinois") == (
        "KEITH HITCHINS University of Illinois"
    )


def test_author_guessed_from_copyright_line():
    from pdf2kindle.model import Chapter, Element, ElementKind, InlineRun
    from pdf2kindle.structure import _guess_author

    ch = Chapter(title="Front Matter", elements=[
        Element(kind=ElementKind.PARAGRAPH, runs=[InlineRun(text="© Keith Hitchins 2014")]),
    ])
    assert _guess_author([ch]) == "Keith Hitchins"
    assert _guess_author([Chapter(title="Empty")]) == ""


def test_wrapped_chapter_title_merges_across_three_fragments():
    """A bare chapter number, then a title wrapped over two lines, must merge
    into one heading -- even though the first merge changes the running
    level, which must not block merging the third fragment."""
    from pdf2kindle.model import Element, ElementKind, InlineRun
    from pdf2kindle.structure import _merge_split_headings

    flat = [
        (30, Element(kind=ElementKind.HEADING, level=1, runs=[InlineRun(text="2")])),
        (30, Element(kind=ElementKind.HEADING, level=2,
                     runs=[InlineRun(text="Between East and West, fourteenth")])),
        (30, Element(kind=ElementKind.HEADING, level=2,
                     runs=[InlineRun(text="century to 1774")])),
    ]
    merged = _merge_split_headings(flat)
    assert len(merged) == 1
    assert merged[0][1].text == "2 Between East and West, fourteenth century to 1774"


# --------------------------------------------------------------------------- #
# A Word-generated thesis: dot-leader ToC, multi-line title, footnote zones
# masked by same-size body text, and justified-line word-splitting
# --------------------------------------------------------------------------- #


def test_dot_leader_toc_entry_is_not_a_heading():
    """"Introduction .......... 4" is a printed Contents/Index row -- never a
    real heading, regardless of bold weight or a leading section number."""
    from pdf2kindle.model import Line, Span
    from pdf2kindle.structure import _is_heading

    ln = Line(
        spans=[Span(text="1.Literature overview " + "." * 40, font="f", size=12.0,
                    flags=1 << 4, color=0, bbox=(0, 0, 1, 1), origin=(0, 8))],
        bbox=(0, 0, 1, 1),
    )
    assert _is_heading(ln, body_size=12.0) is None


def test_numbered_heading_without_space_after_dot():
    """"1.Literature overview" (no space after the dot) is as common as
    "1. Literature overview" in Word-generated numbering."""
    from pdf2kindle.model import Line, Span
    from pdf2kindle.structure import _is_heading

    ln = Line(
        spans=[Span(text="1.Literature overview", font="f", size=20.0, flags=0,
                    color=0, bbox=(0, 0, 1, 1), origin=(0, 16))],
        bbox=(0, 0, 1, 1),
    )
    assert _is_heading(ln, body_size=12.0) == 1


def test_blank_lines_dont_trigger_map_detection():
    """A centered title page is mostly blank spacer "lines"; left uncounted
    they drag the map heuristic's word-per-line average toward zero."""
    from pdf2kindle.extract import _looks_like_map
    from pdf2kindle.model import Line, Span

    def line(text, x0):
        return Line(spans=[Span(text=text, font="f", size=14.0, flags=0, color=0,
                                bbox=(x0, 0, x0 + 40, 10), origin=(x0, 8))],
                    bbox=(x0, 0, x0 + 40, 10))

    title_page = (
        [line("University Name", 180), line("Author Name", 220),
         line("A Thesis Title Here", 150), line("Field of Study", 190)]
        + [line("", 0)] * 10  # blank spacer lines
    )
    assert not _looks_like_map(title_page)


def test_same_size_headings_at_different_depths_dont_merge():
    """A book can style every numbered depth identically ("1.Overview" and its
    own "1.1. Subsection" both at 20pt) -- same size alone must not merge a
    section into its own subsection."""
    from pdf2kindle.model import Element, ElementKind, InlineRun
    from pdf2kindle.structure import _merge_split_headings

    flat = [
        (10, Element(kind=ElementKind.HEADING, level=1, size=20.0,
                     runs=[InlineRun(text="1.Literature overview")])),
        (10, Element(kind=ElementKind.HEADING, level=2, size=20.0,
                     runs=[InlineRun(text="1.1. Hegemony and language")])),
    ]
    merged = _merge_split_headings(flat)
    assert len(merged) == 2
    assert merged[0][1].text == "1.Literature overview"
    assert merged[1][1].text == "1.1. Hegemony and language"


def test_footnote_zone_survives_a_same_size_block_quote_above_it():
    """A block quote set at footnote size directly above the real footnotes
    must not make the whole footnote block unrecognisable: only the block
    quote should be demoted back to body text."""
    from pdf2kindle.analyze import _split_body_notes
    from pdf2kindle.model import Line, Span

    def line(text, y0, size=10.0):
        return Line(spans=[Span(text=text, font="f", size=size, flags=0, color=0,
                                bbox=(70, y0, 70 + len(text) * 5, y0 + 10),
                                origin=(70, y0 + 8))],
                    bbox=(70, y0, 70 + len(text) * 5, y0 + 10))

    lines = [
        line("A paragraph of ordinary body text here.", 460, size=12.0),
        line("A second line of that same body paragraph.", 500, size=12.0),
        line('"A quoted excerpt set in a smaller font."', 580),
        line("1 First, S. (2020). A cited source.", 668),
        line("2 Second, T. (2021). Another source.", 700),
    ]
    body, notes = _split_body_notes(lines, body_size=12.0, height=842.0, line_height=13.0)
    assert [n.text for n in notes] == ["1 First, S. (2020). A cited source.",
                                        "2 Second, T. (2021). Another source."]
    assert any("quoted excerpt" in ln.text for ln in body)


def test_justified_line_split_by_wide_gaps_is_rejoined():
    """Extreme word-spacing on a fully-justified line can make PyMuPDF report
    each word run as its own "line"; they must be recombined into one."""
    from pdf2kindle.extract import _merge_same_row_lines
    from pdf2kindle.model import Line, Span

    def frag(text, x0):
        return Line(spans=[Span(text=text, font="f", size=10.0, flags=0, color=0,
                                bbox=(x0, 464.7, x0 + 30, 474.7), origin=(x0, 472.7))],
                    bbox=(x0, 464.7, x0 + 30, 474.7))

    fragments = [frag("transmisje", 176.9), frag("on-line", 264.0),
                 frag("i", 335.7), frag("relacje", 376.8), frag("na", 446.4)]
    merged = _merge_same_row_lines(fragments)
    assert len(merged) == 1
    assert merged[0].text == "transmisje on-line i relacje na"


def test_front_matter_gets_a_sensible_name(tmp_path):
    """An untitled first chapter (everything before the book's first real
    heading) is named "Front Matter", not the generic "Chapter 1"."""
    from pdf2kindle.model import Element, ElementKind, InlineRun
    from pdf2kindle.structure import _split_by_headings

    flat = [
        (0, Element(kind=ElementKind.PARAGRAPH, runs=[InlineRun(text="A university name")])),
        (1, Element(kind=ElementKind.HEADING, level=1, runs=[InlineRun(text="Introduction")])),
        (1, Element(kind=ElementKind.PARAGRAPH, runs=[InlineRun(text="Body text.")])),
    ]
    chapters = _split_by_headings(flat, {})
    assert chapters[0].title == "Front Matter"
    assert chapters[1].title == "Introduction"


# --------------------------------------------------------------------------- #
# Typography design: chapter openings, drop caps, dinkus
# --------------------------------------------------------------------------- #


def test_numbered_chapter_heading_splits_into_number_and_title():
    from pdf2kindle.model import Element, ElementKind, InlineRun
    from pdf2kindle.html import _render_heading

    el = Element(kind=ElementKind.HEADING, level=1,
                 runs=[InlineRun(text="2. Methodological framework of research")])
    html = _render_heading(el)
    assert '<span class="chnum">2</span>' in html
    assert '<span class="chtitle">Methodological framework of research</span>' in html


def test_plain_chapter_heading_is_unsplit():
    """A title with no leading number renders exactly as a bare heading --
    required so existing content is never restyled into something new."""
    from pdf2kindle.model import Element, ElementKind, InlineRun
    from pdf2kindle.html import _render_heading

    el = Element(kind=ElementKind.HEADING, level=1, runs=[InlineRun(text="Chapter One")])
    assert _render_heading(el) == "<h1>Chapter One</h1>\n"


def test_opening_paragraph_gets_drop_cap_and_lead():
    from pdf2kindle.model import Element, ElementKind, InlineRun
    from pdf2kindle.html import _render_opening_paragraph

    el = Element(kind=ElementKind.PARAGRAPH,
                 runs=[InlineRun(text="It was the best of times, it was the worst of times.")])
    html = _render_opening_paragraph(el)
    assert html is not None
    assert '<span class="dropcap">I</span>' in html
    assert '<span class="lead">t was</span>' in html
    assert "best of times" in html


def test_opening_paragraph_falls_back_for_lowercase_or_styled_start():
    """A run that doesn't open on a plain capital letter -- lowercase, or
    already bold/italic/a footnote marker -- must decline gracefully rather
    than drop-cap something that would look wrong or corrupt markup."""
    from pdf2kindle.model import Element, ElementKind, InlineRun
    from pdf2kindle.html import _render_opening_paragraph

    lowercase = Element(kind=ElementKind.PARAGRAPH,
                         runs=[InlineRun(text="continuing from the previous page.")])
    assert _render_opening_paragraph(lowercase) is None

    italic_start = Element(kind=ElementKind.PARAGRAPH,
                            runs=[InlineRun(text="Emphasis", italic=True),
                                  InlineRun(text=" opens this paragraph.")])
    assert _render_opening_paragraph(italic_start) is None


def test_footnote_section_has_dinkus_not_a_visible_notes_label(bookish_epub):
    with zipfile.ZipFile(bookish_epub) as z:
        body = _read(z, "chap_000.xhtml")
    assert 'class="dinkus"' in body
    assert "<h2>Notes</h2>" in body  # kept for semantics, hidden via CSS


def test_stylesheet_keeps_required_kindle_properties():
    """Regardless of redesign, these two properties must stay literally
    present -- they are what makes the body text justified and hyphenated."""
    from pdf2kindle.html import STYLESHEET

    assert "text-align: justify" in STYLESHEET
    assert "hyphens: auto" in STYLESHEET
