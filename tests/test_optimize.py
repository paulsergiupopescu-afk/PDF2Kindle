"""Tests for re-typesetting an existing EPUB (pdf2kindle optimize)."""
import re
import zipfile

import pytest

from pdf2kindle.audit import audit_epub
from pdf2kindle.optimize import OptimizeOptions, optimize_epub

# The shape of a typical Calibre/Sigil-era retail EPUB: styled <p> headings,
# an embedded font, an NCX listing only the parts, footnotes as plain link
# pairs, spacer paragraphs around verse, legacy cedilla diacritics and an
# ASCII-folded dc:title.
_CSS = """@namespace h "http://www.w3.org/1999/xhtml";
@font-face { font-family: "Lib"; src: url(../Fonts/lib.ttf) }
.corp { font-family: "Lib"; color: #000; font-size: 1em; text-indent: 36px; text-align: justify }
.flush { font-size: 1em; text-indent: 0 }
.vers { font-size: 0.95em; text-indent: 36px }
.parte { font-size: 1.5em; text-align: center; text-indent: 0 }
.cap { font-size: 1.3em; text-align: center; text-indent: 0 }
.titlu { font-size: 1.29em; text-align: center }
.nota { font-size: 0.83em; text-indent: 0 }
.ital { font-style: italic }
.sus { vertical-align: super }
"""

_HEAD = ('<html xmlns="http://www.w3.org/1999/xhtml"><head><title>{0}</title>'
         '<link href="../Styles/s.css" rel="stylesheet" type="text/css"/></head><body><div>')
_FOOT = "</div></body></html>"

_DOCS = {
    "cover.xhtml": ('<html xmlns="http://www.w3.org/1999/xhtml"><head><title>Cover</title></head><body><div>'
                    '<svg xmlns="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink">'
                    '<image xlink:href="../Images/cover.png"/></svg></div></body></html>'),
    "titlu.html": _HEAD.format("t") + '<p class="titlu">Autor</p><p class="titlu">Crimă şi pedeapsă</p>' + _FOOT,
    "p1.html": _HEAD.format("p1")
    + '<p class="parte">PARTEA ÎNTÂI</p><p class="cap">~ I ~</p>'
    + '<p class="corp">Primul paragraf, într-o seară înăbuşitor de caldă.</p>'
    + '<p class="corp">Un om îmbrăcat într-o sibirkă<span class="sus">'
      '<a href="#fn-1" id="fn-1-back">1</a></span>, cânta aşa:</p>'
    + '<p class="corp"><br/><br/></p>'
    + '<p class="vers"><span class="ital">Că un an mi-am dezmierdat</span></p>'
    + '<p class="vers"><span class="ital">Of, of, of, un an</span></p>'
    + '<p class="corp"><br/><br/></p>'
    + '<p class="flush">sau dezmeticindu-se iar.</p>'
    + '<div><p class="nota"><a href="#fn-1-back" id="fn-1">1</a> Haină scurtă cu încreţituri.</p></div>'
    + _FOOT,
    "p1-1.html": _HEAD.format("p1-1")
    + '<p class="corp">Continuarea capitolului întâi.</p>'
    + '<p class="cap"><br/>~ II ~</p>'
    + '<p class="corp">Al doilea capitol<span class="sus"><a href="#fn-1" id="fn-1-back">1</a></span>.</p>'
    + '<p class="corp">...........................................................</p>'
    + '<div><p class="nota"><a href="#fn-1-back" id="fn-1">1</a> O notă cu acelaşi id, în alt fişier.</p></div>'
    + _FOOT,
    "p2.html": _HEAD.format("p2")
    + '<p class="parte">PARTEA A DOUA</p><p class="cap">~ I ~</p>'
    + '<p class="corp">Ultimul capitol.</p>' + _FOOT,
}

_OPF = """<?xml version="1.0" encoding="utf-8"?>
<package xmlns="http://www.idpf.org/2007/opf" unique-identifier="uid" version="2.0">
  <metadata xmlns:dc="http://purl.org/dc/elements/1.1/" xmlns:opf="http://www.idpf.org/2007/opf">
    <dc:identifier id="uid" opf:scheme="uuid">1d8175e9-0000-0000-0000-6dc0e21b44ca</dc:identifier>
    <dc:identifier opf:scheme="ISBN">978-973-46-2148-4</dc:identifier>
    <dc:title>Crima si pedeapsa</dc:title>
    <dc:creator opf:file-as="Dostoievski, F.M." opf:role="aut">F.M. Dostoievski</dc:creator>
    <dc:contributor opf:role="trl">Adriana Liciu</dc:contributor>
    <dc:contributor opf:role="bkp">calibre</dc:contributor>
    <dc:publisher>Polirom</dc:publisher>
    <dc:language>ro</dc:language>
    <meta name="calibre:title_sort" content="Crimă şi pedeapsă"/>
    <meta name="cover" content="cover"/>
  </metadata>
  <manifest>
    <item id="cover" href="Images/cover.png" media-type="image/png"/>
    <item id="font" href="Fonts/lib.ttf" media-type="application/x-font-ttf"/>
    <item id="css" href="Styles/s.css" media-type="text/css"/>
    <item id="ncx" href="toc.ncx" media-type="application/x-dtbncx+xml"/>
{items}
  </manifest>
  <spine toc="ncx">
{refs}
  </spine>
</package>"""

_NCX = """<?xml version="1.0" encoding="utf-8"?>
<ncx xmlns="http://www.daisy.org/z3986/2005/ncx/" version="2005-1"><navMap>
<navPoint id="n1" playOrder="1"><navLabel><text>PARTEA ÎNTÂI</text></navLabel><content src="Text/p1.html"/></navPoint>
<navPoint id="n2" playOrder="2"><navLabel><text>PARTEA A DOUA</text></navLabel><content src="Text/p2.html"/></navPoint>
</navMap></ncx>"""

# A 1x1 PNG.
_PNG = bytes.fromhex(
    "89504e470d0a1a0a0000000d4948445200000001000000010806000000"
    "1f15c4890000000d49444154789c6360000002000154a24f5d0000000049454e44ae426082"
)


@pytest.fixture(scope="module")
def legacy_epub(tmp_path_factory):
    path = tmp_path_factory.mktemp("data") / "legacy.epub"
    names = list(_DOCS)
    items = "\n".join(f'    <item id="d{i}" href="Text/{n}" media-type="application/xhtml+xml"/>'
                      for i, n in enumerate(names))
    refs = "\n".join(f'    <itemref idref="d{i}"/>' for i in range(len(names)))
    with zipfile.ZipFile(path, "w") as z:
        z.writestr(zipfile.ZipInfo("mimetype"), "application/epub+zip", zipfile.ZIP_STORED)
        z.writestr("META-INF/container.xml",
                   '<?xml version="1.0"?><container version="1.0" '
                   'xmlns="urn:oasis:names:tc:opendocument:xmlns:container"><rootfiles>'
                   '<rootfile full-path="OEBPS/content.opf" media-type="application/oebps-package+xml"/>'
                   '</rootfiles></container>')
        z.writestr("OEBPS/content.opf", _OPF.format(items=items, refs=refs))
        z.writestr("OEBPS/toc.ncx", _NCX)
        z.writestr("OEBPS/Styles/s.css", _CSS)
        z.writestr("OEBPS/Fonts/lib.ttf", b"\0" * 64)
        z.writestr("OEBPS/Images/cover.png", _PNG)
        for n, body in _DOCS.items():
            z.writestr(f"OEBPS/Text/{n}", body)
    return str(path)


@pytest.fixture(scope="module")
def optimized(legacy_epub, tmp_path_factory):
    out = str(tmp_path_factory.mktemp("out") / "book.epub")
    result = optimize_epub(legacy_epub, out)
    with zipfile.ZipFile(out) as z:
        files = {n: z.read(n).decode("utf-8", "replace") for n in z.namelist() if not n.endswith(".png")}
    return result, out, files


def _chapters(files):
    return [files[n] for n in sorted(files) if re.match(r"EPUB/chap_\d+\.xhtml$", n)]


def test_optimized_epub_passes_audit(optimized):
    result, out, _ = optimized
    report = audit_epub(out)
    assert report.ok, report
    assert report.notes == report.noterefs == 2
    assert not report.dead_internal_links
    assert result.footnotes == 2


def test_parts_nest_chapters_in_the_contents(optimized):
    _, _, files = optimized
    nav = files["EPUB/nav.xhtml"]
    toc = nav[nav.index('epub:type="toc"'):]
    toc = toc[:toc.index("</nav>")]
    labels = re.findall(r"<a [^>]*>([^<]+)</a>", toc)
    assert labels == ["PARTEA ÎNTÂI", "I", "II", "PARTEA A DOUA", "I"]
    # Chapters are nested one level under their part.
    first_part = toc[toc.index("PARTEA ÎNTÂI"):toc.index("PARTEA A DOUA")]
    assert first_part.count("<ol>") == 1
    # The front matter is kept but not listed; Kindle starts at the text.
    assert "Autor" not in toc
    assert '<reference type="text"' in files["EPUB/content.opf"]


def test_each_chapter_is_its_own_file_with_a_real_heading(optimized):
    _, _, files = optimized
    headings = [re.findall(r"<h1>([^<]+)</h1>", c) for c in _chapters(files)]
    assert [h for h in headings if h] == [["PARTEA ÎNTÂI"], ["~ I ~"], ["~ II ~"], ["PARTEA A DOUA"], ["~ I ~"]]
    # Text split across source files is rejoined under its chapter.
    chapter_one = next(c for c in _chapters(files) if "<h1>~ I ~</h1>" in c)
    assert "Continuarea capitolului întâi" in chapter_one


def test_footnotes_become_popups_in_the_chapter_of_their_marker(optimized):
    _, _, files = optimized
    chapters = _chapters(files)
    one = next(c for c in chapters if "<h1>~ I ~</h1>" in c and "sibirkă" in c)
    two = next(c for c in chapters if "<h1>~ II ~</h1>" in c)
    # Same source id ("fn-1") in two files resolves to two distinct notes.
    assert 'epub:type="noteref"' in one and "Haină scurtă cu încrețituri." in one
    assert "O notă cu același id, în alt fișier." in two and "Haină" not in two
    assert 'epub:type="footnote"' in two


def test_reader_settings_are_no_longer_overridden(optimized):
    result, _, files = optimized
    assert result.dropped_fonts == 1
    assert not any(n.endswith(".ttf") for n in files)
    body = "".join(_chapters(files))
    assert "Lib" not in body and "#000" not in body and "px" not in body
    assert "font-face" not in files["EPUB/style.css"]


def test_paragraph_shapes_survive(optimized):
    _, _, files = optimized
    body = "".join(_chapters(files))
    assert '<p class="verse gap"><em>Că un an mi-am dezmierdat</em></p>' in body
    assert '<p class="verse"><em>Of, of, of, un an</em></p>' in body
    assert '<p class="noindent gap">sau dezmeticindu-se iar.</p>' in body
    # A full-width dotted rule would wrap on a small screen.
    assert '<p class="center">. . . . . . . .</p>' in body
    assert '<p class="center large">Autor</p>' in body


def test_metadata_is_repaired_and_kept(optimized):
    result, _, files = optimized
    opf = files["EPUB/content.opf"]
    assert result.title == "Crimă și pedeapsă"
    assert "<dc:title>Crimă și pedeapsă</dc:title>" in opf
    assert "urn:uuid:1d8175e9-0000-0000-0000-6dc0e21b44ca" in opf
    assert "urn:isbn:978-973-46-2148-4" in opf
    assert "<dc:publisher>Polirom</dc:publisher>" in opf
    assert "Adriana Liciu" in opf and ">trl<" in opf
    assert "calibre" not in opf
    assert "Dostoievski, F.M." in opf
    assert 'name="cover"' in opf


def test_romanian_cedillas_become_comma_below(optimized):
    _, _, files = optimized
    body = "".join(_chapters(files))
    assert "ş" not in body and "ţ" not in body
    assert "înăbușitor" in body


def test_keep_diacritics_option(legacy_epub, tmp_path):
    out = str(tmp_path / "kept.epub")
    optimize_epub(legacy_epub, out, OptimizeOptions(fix_diacritics=False))
    with zipfile.ZipFile(out) as z:
        body = "".join(z.read(n).decode() for n in z.namelist() if n.startswith("EPUB/chap_"))
    assert "înăbuşitor" in body


def test_cover_only_page_is_dropped(optimized):
    _, _, files = optimized
    assert not any("<svg" in c for c in _chapters(files))


def test_drm_encrypted_book_is_refused(legacy_epub, tmp_path):
    locked = tmp_path / "locked.epub"
    with zipfile.ZipFile(legacy_epub) as src, zipfile.ZipFile(locked, "w") as dst:
        for info in src.infolist():
            dst.writestr(info, src.read(info))
        dst.writestr("META-INF/encryption.xml",
                     '<encryption xmlns="urn:oasis:names:tc:opendocument:xmlns:container" '
                     'xmlns:enc="http://www.w3.org/2001/04/xmlenc#"><enc:EncryptedData>'
                     '<enc:CipherData><enc:CipherReference URI="OEBPS/Text/p1.html"/></enc:CipherData>'
                     '</enc:EncryptedData></encryption>')
    with pytest.raises(ValueError, match="DRM"):
        optimize_epub(str(locked), str(tmp_path / "out.epub"))
