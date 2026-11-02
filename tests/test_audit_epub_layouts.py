"""Regression tests for EPUB auditing across package layouts."""
import zipfile

from pdf2kindle.audit import audit_epub


def _write_epub(path, *, bad_note=False, bad_link=False):
    opf = b"""<?xml version="1.0"?>
    <package xmlns="http://www.idpf.org/2007/opf"
             xmlns:dc="http://purl.org/dc/elements/1.1/" version="3.0"
             unique-identifier="bookid">
      <metadata><dc:identifier id="bookid">urn:test</dc:identifier>
        <dc:title>Test</dc:title><dc:language>en</dc:language>
        <meta property="dcterms:modified">2026-11-02T00:00:00Z</meta>
      </metadata>
      <manifest>
        <item id="nav" href="nav.xhtml" media-type="application/xhtml+xml" properties="nav"/>
        <item id="css" href="style.css" media-type="text/css"/>
        <item id="cover-img" href="images/cover.jpg" media-type="image/jpeg" properties="cover-image"/>
        <item id="chapter" href="c001.xhtml" media-type="application/xhtml+xml"/>
        <item id="notes" href="notes.xhtml" media-type="application/xhtml+xml"/>
      </manifest>
      <spine><itemref idref="chapter"/><itemref idref="notes"/></spine>
    </package>"""
    chapter = b"""<?xml version="1.0"?>
    <html xmlns="http://www.w3.org/1999/xhtml"
          xmlns:epub="http://www.idpf.org/2007/ops">
      <head><title>Chapter</title><link rel="stylesheet" href="style.css"/></head>
      <body><h1 id="start">Chapter</h1>
        <p>A Latin text with an endnote.</p>
        <p><sup><a epub:type="noteref" href="notes.xhtml#n1">1</a></sup></p>
        <a href="c001.xhtml#start">Back to chapter</a>
      </body>
    </html>"""
    if bad_note:
        chapter = chapter.replace(b"notes.xhtml#n1", b"notes.xhtml#missing")
    if bad_link:
        chapter = chapter.replace(b"c001.xhtml#start", b"c001.xhtml#missing")
    notes = b"""<?xml version="1.0"?>
    <html xmlns="http://www.w3.org/1999/xhtml"
          xmlns:epub="http://www.idpf.org/2007/ops">
      <head><title>Endnotes</title><link rel="stylesheet" href="style.css"/></head>
      <body><section epub:type="endnotes">
        <aside epub:type="endnote" id="n1"><p>Note body.</p></aside>
      </section></body>
    </html>"""
    nav = b"""<?xml version="1.0"?>
    <html xmlns="http://www.w3.org/1999/xhtml"
          xmlns:epub="http://www.idpf.org/2007/ops">
      <body><nav epub:type="toc"><ol><li><a href="c001.xhtml#start">Chapter</a></li></ol></nav></body>
    </html>"""
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("mimetype", "application/epub+zip", compress_type=zipfile.ZIP_STORED)
        z.writestr("META-INF/container.xml",
                   '<container xmlns="urn:oasis:names:tc:opendocument:xmlns:container">'
                   '<rootfiles><rootfile full-path="OEBPS/content.opf" '
                   'media-type="application/oebps-package+xml"/></rootfiles></container>')
        z.writestr("OEBPS/content.opf", opf)
        z.writestr("OEBPS/nav.xhtml", nav)
        z.writestr("OEBPS/style.css", "body { text-align: justify; }")
        z.writestring("OEBPS/images/cover.jpg", b"cover")
        z.writestr("OEBPS/c001.xhtml", chapter)
        z.writestr("OEBPS/notes.xhtml", notes)


def test_audit_resolves_cross_document_endnote_links(tmp_path):
    epub = tmp_path / "book.epub"
    _write_epub(epub)
    report = audit_epub(str(epub))
    assert report.chapters == 2
    assert report.notes == 1
    assert report.noterefs == 1
    assert not report.dead_links
    assert not report.dead_internal_links
    assert report.stylesheet_linked
    assert report.has_cover
    assert report.ok


def test_audit_reports_dead_cross_document_note_reference(tmp_path):
    epub = tmp_path / "bad-note.epub"
    _write_epub(epub, bad_note=True)
    report = audit_epub(str(epub))
    assert report.dead_links == ["OEBPS/c001.xhtml -> notes.xhtml#missing"]
    assert not report.ok


def test_audit_reports_dead_cross_document_fragment_link(tmp_path):
    epub = tmp_path / "bad-link.epub"
    _write_epub(epub, bad_link=True)
    report = audit_epub(str(epub))
    assert report.dead_internal_links == ["OEBPS/c001.xhtml -> c001.xhtml#missing"]
    assert not report.ok



def test_audit_checks_navigation_targets(tmp_path):
    epub = tmp_path / "bad-nav.epub"
    _write_epub(epub)
    with zipfile.ZipFile(epub, "r") as source:
        members = {name: source.read(name) for name in source.namelist()}
    members["OEBPS/nav.xhtml"] = members["OEBPS/nav.xhtml"].replace(
        b"c001.xhtml#start", b"c001.xhtml#no-such-heading"
    )
    with zipfile.ZipFile(epub, "w") as target:
        target.writestr("mimetype", members.pop("mimetype"),
                        compress_type=zipfile.ZIP_STORED)
        for name, content in members.items():
            target.writestr(name, content)
    report = audit_epub(str(epub))
    assert report.dead_internal_links == [
        "OEBPS/nav.xhtml -> c001.xhtml#no-such-heading"
    ]
    assert not report.ok
