"""Quality gates that every fixture must clear, in every profile.

The per-feature tests in `test_convert.py` check that a given shape converts
the way it should. These check the invariants that must hold for *all* of
them at once, so a heuristic tuned to make one document better cannot quietly
make another worse. Two bugs of exactly that kind -- a whole bibliography and
a whole notes section dropped without a word of warning -- are what this file
exists to catch.
"""
import html
import re
import zipfile

import pymupdf
import pytest
from lxml import etree

from pdf2kindle import ConvertOptions, convert_pdf
from pdf2kindle.text import normalize
from tests.make_academic import main as make_academic
from tests.make_bookish import main as make_bookish
from tests.make_endnotes import main as make_endnotes
from tests.make_journal import main as make_journal
from tests.make_sample import main as make_sample

BUILDERS = {
    "sample": make_sample,
    "academic": make_academic,
    "bookish": make_bookish,
    "endnotes": make_endnotes,
    "journal": make_journal,
}

# Every fixture, in every profile it is meant to be read in.
CASES = [
    ("sample", "academic"),
    ("sample", "general"),
    ("academic", "academic"),
    ("bookish", "academic"),
    ("endnotes", "academic"),
    ("journal", "academic"),
    ("journal", "article"),
]

# The share of the source's distinct words that must survive into the EPUB.
#
# It is not 100% because some text is *meant* to change: de-hyphenation joins
# "know-ledge" into one word neither half of which matches, and a table's
# cells move into a picture by design. The journal fixture's allowance is the
# lower one for that reason -- its Table 1 is an image, as the rules require.
# Everything else must come through essentially whole.
MIN_COVERAGE = {
    ("journal", "academic"): 0.88,
    ("journal", "article"): 0.88,
    ("endnotes", "academic"): 0.96,
}
DEFAULT_MIN_COVERAGE = 0.98

# Inline tags are removed without leaving a space, so a word split across
# markup -- a drop cap's <span>T</span>his, a bold run, a note marker -- is
# still one word. Block tags become a space, so neighbours do not fuse.
_INLINE_TAG_RE = re.compile(r"</?(?:span|strong|em|i|b|sup|sub|a)\b[^>]*>", re.I)
_ANY_TAG_RE = re.compile(r"<[^>]+>")
_WORD_RE = re.compile(r"[^\W\d_]{4,}", re.UNICODE)


def _words(text: str) -> set:
    """Distinct words of four letters or more, with print artefacts folded.

    `normalize` is applied to the source side too: folding "ﬁnd" to "find" is
    a deliberate repair, not a loss, and comparing raw glyphs would flag every
    ligature in the book.
    """
    return set(_WORD_RE.findall(normalize(text).lower()))


def _epub_text(z: zipfile.ZipFile) -> str:
    parts = []
    for name in z.namelist():
        if name.endswith(".xhtml"):
            doc = z.read(name).decode("utf-8")
            parts.append(html.unescape(_ANY_TAG_RE.sub(" ", _INLINE_TAG_RE.sub("", doc))))
    return " ".join(parts)


@pytest.fixture(scope="module")
def built(tmp_path_factory):
    """Convert every fixture in every profile once, and hand back the results."""
    base = tmp_path_factory.mktemp("quality")
    out = {}
    for name, profile in CASES:
        pdf = base / f"{name}.pdf"
        if not pdf.exists():
            BUILDERS[name](str(pdf))
        epub = base / f"{name}-{profile}.epub"
        result = convert_pdf(str(pdf), str(epub),
                             ConvertOptions(profile=profile, ocr="never"))
        out[(name, profile)] = (str(pdf), str(epub), result)
    return out


@pytest.mark.parametrize("case", CASES, ids=lambda c: f"{c[0]}-{c[1]}")
def test_no_section_is_silently_dropped(built, case):
    """Almost every word of the source must reach the EPUB."""
    pdf, epub, _ = built[case]
    with pymupdf.open(pdf) as doc:
        source = _words("".join(page.get_text() for page in doc))
    with zipfile.ZipFile(epub) as z:
        produced = _words(_epub_text(z))
    missing = source - produced
    coverage = 1 - len(missing) / max(len(source), 1)
    floor = MIN_COVERAGE.get(case, DEFAULT_MIN_COVERAGE)
    assert coverage >= floor, (
        f"{case}: only {coverage:.1%} of source words survived "
        f"(floor {floor:.0%}); missing e.g. {sorted(missing)[:15]}"
    )


@pytest.mark.parametrize("case", CASES, ids=lambda c: f"{c[0]}-{c[1]}")
def test_every_note_link_resolves(built, case):
    """A note marker always points at a note body in the same file."""
    _, epub, _ = built[case]
    with zipfile.ZipFile(epub) as z:
        for name in z.namelist():
            if not name.endswith(".xhtml"):
                continue
            doc = z.read(name).decode("utf-8")
            ids = set(re.findall(r'id="([^"]+)"', doc))
            refs = re.findall(r'class="noteref"[^>]*href="#([^"]+)"', doc)
            assert [r for r in refs if r not in ids] == [], f"{case}: dangling in {name}"


@pytest.mark.parametrize("case", CASES, ids=lambda c: f"{c[0]}-{c[1]}")
def test_output_is_well_formed(built, case):
    """Every document in the container parses as XML."""
    _, epub, _ = built[case]
    with zipfile.ZipFile(epub) as z:
        for name in z.namelist():
            if name.endswith((".xhtml", ".opf", ".ncx")):
                etree.fromstring(z.read(name))  # raises if malformed


@pytest.mark.parametrize("case", CASES, ids=lambda c: f"{c[0]}-{c[1]}")
def test_book_has_navigable_structure(built, case):
    """A title, a cover, and a table of contents that points somewhere."""
    _, epub, result = built[case]
    assert result.title.strip()
    assert result.chapters >= 1
    with zipfile.ZipFile(epub) as z:
        nav = next(z.read(n).decode("utf-8") for n in z.namelist() if n.endswith("nav.xhtml"))
        hrefs = re.findall(r'<a href="([^"#]+)', nav)
        assert hrefs, f"{case}: empty table of contents"
        names = {n.split("/")[-1] for n in z.namelist()}
        assert all(h.split("/")[-1] in names for h in hrefs), f"{case}: nav points at nothing"


@pytest.mark.parametrize("case", CASES, ids=lambda c: f"{c[0]}-{c[1]}")
def test_no_empty_chapters(built, case):
    """A chapter with nothing in it is a split that went wrong."""
    _, epub, _ = built[case]
    with zipfile.ZipFile(epub) as z:
        for name in sorted(z.namelist()):
            if not (name.endswith(".xhtml") and "chap" in name):
                continue
            doc = z.read(name).decode("utf-8")
            body = doc[doc.index("<body>"):]
            text = html.unescape(_ANY_TAG_RE.sub(" ", body)).strip()
            assert text or "<img" in body, f"{case}: {name} is empty"
