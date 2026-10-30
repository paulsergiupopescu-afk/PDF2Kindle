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


# The shape of a word-processor export: no cover, HTML named entities the
# XML parser cannot resolve, headings sized on inner spans or set as
# body-size bold that only the NCX identifies, a "Title page" NCX entry, a
# foreword before the first part, and endnotes in their own file whose
# back-links target an empty anchor beside the marker.
_WP_CSS = """p { text-indent: 1.3em; font-family: "Bookman Old Style" }
.p0 { text-indent: 0; text-align: center }
.t1 { font-size: 1.8em }
.t3 { font-size: .65em; vertical-align: super }
.t4 { font-size: 2.4em }
.t5 { font-size: .91em }
"""
_WP_HEAD = ('<?xml version="1.0" encoding="utf-8"?>\n'
            '<!DOCTYPE html PUBLIC "-//W3C//DTD XHTML 1.1//EN" "http://www.w3.org/TR/xhtml11/DTD/xhtml11.dtd">\n'
            '<html xmlns="http://www.w3.org/1999/xhtml"><head>'
            '<link rel="stylesheet" type="text/css" href="style.css"/><title></title></head><body>')
_LONG_TITLE = ("II - ÎNTÂMPLĂRI DIN VIAŢA IEROMONAHULUI STAREŢ ZOSIMA RĂPOSAT ÎNTRU DOMNUL, "
               "POVESTITE DE EL ÎNSUŞI")
_WP_DOCS = {
    "001.html": _WP_HEAD + '<p class="p0">&nbsp;</p><p class="p0"><span class="t1"><b>Autorul</b></span></p>'
                           '<p class="p0">&nbsp;</p></body></html>',
    "002.html": _WP_HEAD + '<p class="p0"><a id="ncx1"></a><span class="t1"><b>DIN PARTEA AUTORULUI</b></span></p>'
                           '<p>&nbsp;</p><p>Cuvânt înainte &ndash; despre fraţii lui<a id="a4"></a>'
                           '<a href="notes.html#a132"><span class="t3"><b>{1}</b></span></a>.</p></body></html>',
    "003.html": _WP_HEAD + '<p class="p0">&nbsp;</p><p class="p0"><a id="ncx2"></a><span class="t4"><b>PARTEA ÎNTÂI'
                           '</b></span></p><p class="p0">&nbsp;</p></body></html>',
    "004.html": _WP_HEAD + '<p>&nbsp;</p><p><a id="ncx3"></a><b>I - FIODOR PAVLOVICI</b></p><p>&nbsp;</p>'
                           '<p>Fraţii Karamazov erau trei &bdquo;fraţi&rdquo;<a id="a9"></a>'
                           '<a href="notes.html#a136"><span class="t3"><b>{2}</b></span></a>.</p>'
                           f'<p><a id="ncx4"></a><b>{_LONG_TITLE}</b></p>'
                           '<p>Un capitol cu titlu lung.</p></body></html>',
    "notes.html": _WP_HEAD + '<p><a id="a132"></a><a href="002.html#a4"><span class="t3"><b>{1}</b></span></a>'
                             '<span class="t5">&nbsp; Prima notă.</span></p>'
                             '<p><a id="a136"></a><a href="004.html#a9"><span class="t3"><b>{2}</b></span></a>'
                             '<span class="t5">&nbsp; A doua notă.</span></p></body></html>',
}
_WP_NCX = """<?xml version="1.0" encoding="UTF-8"?>
<ncx xmlns="http://www.daisy.org/z3986/2005/ncx/" version="2005-1"><navMap>
<navPoint id="n1"><navLabel><text>Title page</text></navLabel><content src="001.html"/></navPoint>
<navPoint id="n2"><navLabel><text>DIN PARTEA AUTORULUI</text></navLabel><content src="002.html#ncx1"/></navPoint>
<navPoint id="n3"><navLabel><text>PARTEA ÎNTÂI</text></navLabel><content src="003.html#ncx2"/>
  <navPoint id="n4"><navLabel><text>I - FIODOR PAVLOVICI</text></navLabel><content src="004.html#ncx3"/></navPoint>
  <navPoint id="n5"><navLabel><text>II - LONG</text></navLabel><content src="004.html#ncx4"/></navPoint>
</navPoint>
</navMap></ncx>"""


@pytest.fixture(scope="module")
def wordproc_epub(tmp_path_factory):
    path = tmp_path_factory.mktemp("data") / "wp.epub"
    names = list(_WP_DOCS)
    items = "\n".join(f'<item id="d{i}" href="{n}" media-type="application/xhtml+xml"/>' for i, n in enumerate(names))
    refs = "\n".join(f'<itemref idref="d{i}"/>' for i in range(len(names)))
    opf = f"""<?xml version="1.0" encoding="UTF-8"?>
<package xmlns="http://www.idpf.org/2007/opf" version="2.0" unique-identifier="BookId">
<metadata xmlns:dc="http://purl.org/dc/elements/1.1/" xmlns:opf="http://www.idpf.org/2007/opf">
<dc:title>Fratii Karamazov - Vol.1</dc:title><dc:creator>F. M. Dostoievski</dc:creator>
<dc:identifier id="BookId">awp2011</dc:identifier><dc:language>ro</dc:language></metadata>
<manifest><item id="ncx" href="toc.ncx" media-type="application/x-dtbncx+xml"/>
<item id="style" href="style.css" media-type="text/css"/>{items}
<item id="f1" href="fonts/B.TTF" media-type="application/vnd-ms-opentype"/></manifest>
<spine toc="ncx">{refs}</spine></package>"""
    with zipfile.ZipFile(path, "w") as z:
        z.writestr(zipfile.ZipInfo("mimetype"), "application/epub+zip", zipfile.ZIP_STORED)
        z.writestr("META-INF/container.xml",
                   '<?xml version="1.0"?><container version="1.0" '
                   'xmlns="urn:oasis:names:tc:opendocument:xmlns:container"><rootfiles>'
                   '<rootfile full-path="Ops/content.opf" media-type="application/oebps-package+xml"/>'
                   '</rootfiles></container>')
        z.writestr("Ops/content.opf", opf)
        z.writestr("Ops/toc.ncx", _WP_NCX)
        z.writestr("Ops/style.css", _WP_CSS)
        z.writestr("Ops/fonts/B.TTF", b"\0" * 64)
        for n, body in _WP_DOCS.items():
            z.writestr(f"Ops/{n}", body)
    return str(path)


@pytest.fixture(scope="module")
def wordproc(wordproc_epub, tmp_path_factory):
    out = str(tmp_path_factory.mktemp("out") / "wp.epub")
    result = optimize_epub(wordproc_epub, out)
    with zipfile.ZipFile(out) as z:
        files = {n: z.read(n).decode("utf-8", "replace") for n in z.namelist() if not n.endswith(".jpg")}
        has_cover = any(n.endswith(".jpg") for n in z.namelist())
    return result, out, files, has_cover


def test_wordproc_book_passes_audit_with_generated_cover(wordproc):
    result, out, _, has_cover = wordproc
    assert audit_epub(out).ok
    assert has_cover
    assert any("generated" in w for w in result.warnings)


def test_html_entities_become_characters(wordproc):
    _, _, files, _ = wordproc
    body = "".join(_chapters(files))
    assert "&ndash;" not in body and "&bdquo;" not in body and "&amp;" not in body
    assert "</span> – despre frații lui" in body and "„frați”" in body


def test_endnotes_with_sibling_anchor_backlinks_become_popups(wordproc):
    result, _, files, _ = wordproc
    assert result.footnotes == 2
    chapters = _chapters(files)
    foreword = next(c for c in chapters if "DIN PARTEA AUTORULUI" in c)
    assert "Prima notă." in foreword and 'href="#fn1">1</a>' in foreword  # braces dropped
    # The endnotes page itself is gone: every note found its marker.
    assert not any("A doua notă" in c and "<h1" not in c for c in chapters)


def test_contents_entries_drive_plain_and_long_headings(wordproc):
    _, _, files, _ = wordproc
    nav = files["EPUB/nav.xhtml"]
    toc = nav[nav.index('epub:type="toc"'):]
    toc = toc[:toc.index("</nav>")]
    labels = re.findall(r"<a [^>]*>([^<]+)</a>", toc)
    # "Title page" points at a page, not a heading: front matter, unlisted.
    # The foreword stays top level; the part nests its chapters.
    assert labels[0] == "DIN PARTEA AUTORULUI"
    assert labels[1] == "PARTEA ÎNTÂI"
    assert labels[2] == "I - FIODOR PAVLOVICI"
    assert labels[3].startswith("II - ÎNTÂMPLĂRI DIN VIAȚA")
    part = toc[toc.index("PARTEA ÎNTÂI"):]
    assert part.count("<ol>") == 1 and "DIN PARTEA" not in part
    # Roman-numeral chapter titles get the two-tier opening.
    body = "".join(_chapters(files))
    assert '<span class="chnum">I</span><span class="chtitle">FIODOR PAVLOVICI</span>' in body


def test_title_page_sized_on_spans_keeps_its_large_type(wordproc):
    _, _, files, _ = wordproc
    assert '<p class="center large"><strong>Autorul</strong></p>' in "".join(_chapters(files))


def test_ascii_title_regains_the_books_own_diacritics(wordproc):
    result, _, files, _ = wordproc
    assert result.title == "Frații Karamazov - Vol.1"
    assert "<dc:title>Frații Karamazov - Vol.1</dc:title>" in files["EPUB/content.opf"]


# The shape of a Calibre conversion with no NCX at all: a title page and a
# printed contents list ahead of the text, three heading tiers (two by size,
# one only centred), and a table whose cells hold two lines.
_CAL_CSS = """.big { font-size: 1.67em; text-align: center }
.mid { font-size: 1.29em; text-align: center }
.sec { font-size: 1em; text-align: center }
.toc { font-size: 1em; text-indent: -54pt; margin-left: 54pt }
.tocbig { font-size: 1.67em; text-indent: -54pt; margin-left: 54pt }
.title { font-size: 2em; text-align: center }
.verse { font-size: 1em; text-align: center }
.body { font-size: 1em; text-indent: 18pt }
"""
_PROSE = ("This paragraph is long enough to read as running prose rather than as a title-page line or a "
          "contents entry, which is what tells the front matter apart from the text. ")


def _cal_doc(body):
    return ('<html xmlns="http://www.w3.org/1999/xhtml"><head><title>x</title>'
            '<link href="stylesheet.css" rel="stylesheet" type="text/css"/></head><body>' + body + "</body></html>")


def _sec(title, n=1):
    return f'<p class="sec">{title}</p>' + f'<p class="body">{_PROSE * 2}</p>' * n


_CAL_DOCS = {
    "a.html": _cal_doc(
        '<p class="big">AN INTRODUCTION</p><p class="mid">TO LATIN</p>'
        '<p class="verse">Disce quod doceas.</p><p class="verse">Scribimus indocti.</p>'
        '<p class="title">Contents</p>'
        + "".join(f'<p class="toc">{t}</p>' for t in
                  ("Syntax", "Sentences", "Nouns", "Mood", "Tense", "Voice", "Extracts"))
        + '<p class="big">Preface</p>' + f'<p class="body">{_PROSE * 3}</p>'
        + '<p class="big">Syntax</p><p class="mid">Sentences</p>' + f'<p class="body">{_PROSE * 2}</p>'
        + '<p class="mid">Nouns</p>' + _sec("Mood") + _sec("Tense") + _sec("Voice")
        + '<table><tr><td><p>Past simple</p><p>I loved.</p></td><td>Present</td></tr></table>'
        + '<p class="big">Extracts</p>' + _sec("Visiones Perpetuae") + _sec("De bono fidei")
        + _sec("Somnium Hieronymi")),
}


@pytest.fixture(scope="module")
def calibre_epub(tmp_path_factory):
    path = tmp_path_factory.mktemp("data") / "cal.epub"
    opf = """<?xml version="1.0" encoding="utf-8"?>
<package xmlns="http://www.idpf.org/2007/opf" unique-identifier="uuid_id" version="2.0">
<metadata xmlns:dc="http://purl.org/dc/elements/1.1/" xmlns:opf="http://www.idpf.org/2007/opf">
<dc:title>An Introduction to Latin</dc:title><dc:creator>Nunn</dc:creator><dc:language>en</dc:language>
<dc:identifier id="uuid_id" opf:scheme="uuid">245f750d-0423-499f-934a-ee1ec8f29f06</dc:identifier>
<meta name="cover" content="cover"/></metadata>
<manifest><item href="a.html" id="a" media-type="application/xhtml+xml"/>
<item href="stylesheet.css" id="css" media-type="text/css"/>
<item href="cover.png" id="cover" media-type="image/png"/></manifest>
<spine><itemref idref="a"/></spine></package>"""
    with zipfile.ZipFile(path, "w") as z:
        z.writestr(zipfile.ZipInfo("mimetype"), "application/epub+zip", zipfile.ZIP_STORED)
        z.writestr("META-INF/container.xml",
                   '<?xml version="1.0"?><container version="1.0" '
                   'xmlns="urn:oasis:names:tc:opendocument:xmlns:container"><rootfiles>'
                   '<rootfile full-path="content.opf" media-type="application/oebps-package+xml"/>'
                   '</rootfiles></container>')
        z.writestr("content.opf", opf)
        z.writestr("stylesheet.css", _CAL_CSS)
        z.writestr("cover.png", _PNG)
        for n, body in _CAL_DOCS.items():
            z.writestr(n, body)
    return str(path)


@pytest.fixture(scope="module")
def calibre(calibre_epub, tmp_path_factory):
    out = str(tmp_path_factory.mktemp("out") / "cal.epub")
    result = optimize_epub(calibre_epub, out)
    with zipfile.ZipFile(out) as z:
        files = {n: z.read(n).decode("utf-8", "replace") for n in z.namelist() if not n.endswith(".png")}
    return result, out, files


def _toc_tree(files):
    nav = files["EPUB/nav.xhtml"]
    toc = nav[nav.index('epub:type="toc"'):]
    toc = toc[:toc.index("</nav>")]
    out, depth = [], 0
    for tok in re.findall(r"<ol>|</ol>|<a [^>]*>[^<]*</a>", toc):
        if tok == "<ol>":
            depth += 1
        elif tok == "</ol>":
            depth -= 1
        else:
            out.append("  " * (depth - 1) + re.sub(r"<[^>]+>", "", tok))
    return out


def test_headings_without_a_contents_are_inferred_in_three_tiers(calibre):
    result, out, files = calibre
    assert audit_epub(out).ok
    assert _toc_tree(files) == [
        "Preface",
        "Syntax",
        "  Sentences",
        "  Nouns",
        "    Mood",
        "    Tense",
        "    Voice",
        "Extracts",
        "  Visiones Perpetuae",
        "  De bono fidei",
        "  Somnium Hieronymi",
    ]
    assert any("inferred" in w for w in result.warnings)


def test_title_page_is_front_matter_and_printed_contents_is_dropped(calibre):
    _, _, files = calibre
    chapters = _chapters(files)
    front = chapters[0]
    assert "AN INTRODUCTION" in front and "Disce quod doceas." in front
    body = "".join(chapters)
    assert "Contents" not in body
    assert body.count(">Mood<") == 1  # the heading itself, not a contents line


def test_table_cells_keep_their_lines(calibre):
    _, _, files = calibre
    body = "".join(_chapters(files))
    assert "<td>Past simple<br/>I loved.</td><td>Present</td>" in body
    assert "<th>" not in body  # no header row in the source
    assert "\\n" not in body  # no literal escape sequences in the markup
