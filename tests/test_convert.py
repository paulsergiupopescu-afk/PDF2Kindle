"""End-to-end tests for the conversion pipeline."""
import os
import zipfile

import pytest
from lxml import etree

from pdf2kindle import ConvertOptions, convert_pdf
from tests.make_sample import main as make_sample

HERE = os.path.dirname(__file__)


@pytest.fixture(scope="module")
def sample_pdf(tmp_path_factory):
    out = tmp_path_factory.mktemp("data") / "sample.pdf"
    make_sample(str(out))
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
