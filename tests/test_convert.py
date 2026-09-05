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
    assert "<h1" in body and "<h2" in body           # multi-level headings
    assert "<blockquote>" in body                     # block quote detected
    assert 'class="caption"' in body                  # figure caption
    assert 'class="reference"' in body                # bibliography entries
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
