#!/usr/bin/env python3
"""Inject NABRE psalm texts into *Praying the Psalms in Christ*.

Extracts Psalms 1-150 from *The Catholic Study Bible* (New American Bible,
Revised Edition) EPUB and places each psalm at the start of the matching
commentary in Laurence Kriegshauser's *Praying the Psalms in Christ*. The
result is a new EPUB 2 (the same format as the original book, for Send to
Kindle) with one file per psalm, a 1-150 jump grid and a
nested table of contents. Every footnote, index and contents link into the
old commentary files is rewritten to the file that now holds its target.

usage: python tools/merge_psalms.py BIBLE.epub PSALMS.epub OUT.epub
"""

from __future__ import annotations

import argparse
import copy
import posixpath
import re
import sys
import uuid
import zipfile
from dataclasses import dataclass, field
from xml.sax.saxutils import escape

from lxml import etree

XHTML = "http://www.w3.org/1999/xhtml"
NS = {"h": XHTML}
H = "{%s}" % XHTML

PARSER = etree.XMLParser(resolve_entities=False, remove_blank_text=False)

ROMAN = re.compile(r"^[IVXL]+$")
COMMENTARY = ("und-kriegshauser-0010a.html", "und-kriegshauser-0010b.html",
              "und-kriegshauser-0010c.html")

# The five books of the Psalter, used to group the navigation.
BOOKS = [("Book One", 1, 41), ("Book Two", 42, 72), ("Book Three", 73, 89),
         ("Book Four", 90, 106), ("Book Five", 107, 150)]

# Superscriptions missing from the source Bible EPUB (NABRE verse 1).
MISSING_SUPERSCRIPTIONS = {19: "For the leader. A psalm of David."}

# Typos in the source Bible text.
TYPO_FIXES = {"he had sgone in to": "he had gone in to"}

# A deliberately small stylesheet. Send to Kindle falls back to a fixed,
# PDF-like layout (error E016) when it meets formatting it cannot reflow, so
# the book uses no tables, embedded fonts, fixed widths or negative indents.
STYLE_CSS = """
p {margin:0; text-indent:1.2em;}
h2 {font-size:1.4em; font-weight:normal; margin:1em 0 0 0; text-indent:0;}
i, .theb-i, .hemb {font-style:italic;}
b, .gt, .aubiof, .row-head, .tch1 {font-weight:bold;}
.sm {font-size:80%;}
.ahkey {font-style:italic; text-indent:0; margin-bottom:1em;}
.pf, .paft, .pcon {text-indent:0; margin-top:.6em;}
.psec {margin-top:.8em;}
.ct, .cs, .ctbm, .ctfm {font-size:1.4em; font-weight:bold; text-indent:0; margin:1em 0;}
.bk, .bk1 {font-size:1.6em; text-align:center; text-indent:0; margin-top:1em;}
.bkau, .bkpub, .bkpub1, .crt, .crtf, .crt1, .crt2, .crtl, .cip, .cipf, .cip1, .cip2, .cipl,
.fig, .cover, .end, .toc, .tocfm, .tocbm, .ded, .ded1, .dedf {text-align:center; text-indent:0; margin-top:.4em;}
.in, .rf, .glo, .glof, .td, .tdp, .tdf, .tch, .row, .row-head, .aubio, .aubiof, .fn, .inhn,
.sl, .sl1, .slf, .sll, .slt, .slif, .sl1l {text-indent:0;}
.row, .rf, .glof, .in {margin-top:.3em;}
.row-head {margin-top:1em;}
.fn {font-size:.9em; margin-top:.6em;}
.fnref {font-size:.7em; vertical-align:super;}
.bqs, .ntbqs, .ntbqf, .ntbql, .bqaft, .senseline, .sl, .sl1, .slf, .sll, .slt, .slif, .sl1l,
.ntsl, .ntslf, .ntsll {margin-left:1.5em; text-indent:0;}
.blockquote {margin:.8em 0;}
img {max-width:100%;}
.nab {margin-top:1em;}
.nab-title {font-weight:bold; font-style:italic; text-indent:0; margin-bottom:.5em;}
.nab-super {font-style:italic; text-indent:0; margin-bottom:.5em;}
.nab-sec, .nab-stanza {text-align:center; text-indent:0; margin-top:.6em;}
.nab-l {text-indent:0;}
.nab-l1 {text-indent:0; margin-left:2em;}
.nab-selah {text-align:right; text-indent:0;}
.vn {font-size:.7em; vertical-align:super;}
.nab-sep {text-align:center; text-indent:0; margin:1em 0;}
.edition-note {text-indent:0; margin-top:2em;}
"""


def x(tag: str) -> str:
    return H + tag


def new_el(tag: str, attrib: dict | None = None):
    """An XHTML element in the default namespace (no html: prefix)."""
    return etree.Element(x(tag), attrib or {}, nsmap={None: XHTML})


def classes(el) -> str:
    return (el.get("class") or "").strip()


def text_of(el) -> str:
    return "".join(el.itertext()).strip()


def remove_keep_tail(el) -> None:
    parent = el.getparent()
    tail = el.tail or ""
    prev = el.getprevious()
    if prev is not None:
        prev.tail = (prev.tail or "") + tail
    else:
        parent.text = (parent.text or "") + tail
    parent.remove(el)


def unwrap(el) -> None:
    """Replace an element by its children, keeping all text."""
    parent = el.getparent()
    idx = parent.index(el)
    prev = el.getprevious()
    lead = el.text or ""
    if prev is not None:
        prev.tail = (prev.tail or "") + lead
    else:
        parent.text = (parent.text or "") + lead
    children = list(el)
    for i, child in enumerate(children):
        parent.insert(idx + i, child)
    if children:
        last = children[-1]
        last.tail = (last.tail or "") + (el.tail or "")
    else:
        anchor = parent[idx - 1] if idx > 0 else None
        if anchor is not None:
            anchor.tail = (anchor.tail or "") + (el.tail or "")
        else:
            parent.text = (parent.text or "") + (el.tail or "")
    parent.remove(el)


# --------------------------------------------------------------------------
# Bible side: extract the psalms


@dataclass
class Psalm:
    number: int
    title: str = ""
    blocks: list = field(default_factory=list)  # (kind, element)


def clean_line(p):
    """Return a cleaned copy of a Bible poetry paragraph's inline content."""
    p = copy.deepcopy(p)
    for el in list(p.iter()):
        if el is p:
            continue
        if el.getparent() is None:
            continue  # already detached with a removed ancestor
        tag = etree.QName(el).localname
        cls = classes(el)
        if tag == "a":
            if el.find(".//" + x("sup")) is not None or el.get("href", "").find("note") >= 0:
                remove_keep_tail(el)  # footnote / study-note marker
            else:
                unwrap(el)
        elif tag == "sup" and etree.QName(el.getparent()).localname != "span":
            remove_keep_tail(el)  # stray note asterisk
    for el in list(p.iter(x("span"))):
        cls = classes(el)
        if cls == "Start-Verse":
            num = text_of(el)
            for child in list(el):
                el.remove(child)
            el.text = num
            el.attrib.clear()
            el.set("class", "vn")
        elif cls == "sc":
            word = text_of(el)
            for child in list(el):
                el.remove(child)
            # Small caps the way the commentary itself sets them.
            el.attrib.clear()
            el.tag = x("span")
            first, rest = word[:1], word[1:].upper()
            tail = el.tail
            el.text = rest
            el.set("class", "sm")
            prev = el.getprevious()
            if prev is not None:
                prev.tail = (prev.tail or "") + first
            else:
                el.getparent().text = (el.getparent().text or "") + first
            el.tail = tail
        elif cls in ("extRef", "") or cls is None:
            unwrap(el)
    for el in p.iter():
        for attr in ("id",):
            el.attrib.pop(attr, None)
    # Normalise whitespace at the edges.
    if p.text:
        p.text = p.text.lstrip()
    for el in p.iter():
        for k, v in TYPO_FIXES.items():
            if el.text and k in el.text:
                el.text = el.text.replace(k, v)
            if el.tail and k in el.tail:
                el.tail = el.tail.replace(k, v)
    return p


def append_inline(dst, src) -> None:
    """Append src's inline content to dst, separated by a space."""
    if len(dst):
        last = dst[-1]
        last.tail = (last.tail or "").rstrip() + " " + (src.text or "").lstrip()
    else:
        dst.text = (dst.text or "").rstrip() + " " + (src.text or "").lstrip()
    for child in list(src):
        dst.append(child)


def find_psalms_doc(bible: zipfile.ZipFile) -> bytes:
    for name in bible.namelist():
        if name.endswith((".xhtml", ".html", ".htm")):
            data = bible.read(name)
            if b'class="title">PSALM 150<' in data:
                return data
    raise SystemExit("could not find the Book of Psalms in the Bible EPUB")


def extract_psalms(data: bytes) -> dict[int, Psalm]:
    root = etree.fromstring(data, PARSER)
    skip_containers = {"hr1", "boxa", "notegroup", "notegroupt", "droptoc",
                       "chapterfrontmatter", "tablebox"}
    psalms: dict[int, Psalm] = {}
    cur: Psalm | None = None
    seen_line = seen_sec = False
    for el in root.iter(x("h1"), x("p"), x("div")):
        cls = classes(el)
        if el.tag == x("div"):
            if cls in ("notegroup", "notegroupt"):
                break  # end of the psalm texts; notes follow
            continue
        if any(classes(a) in skip_containers for a in el.iterancestors()):
            continue
        if el.tag == x("h1"):
            title = el.find(".//h:span[@class='title']", NS)
            m = re.fullmatch(r"PSALM (\d+)", text_of(title)) if title is not None else None
            if cls == "h1" and m:
                cur = Psalm(int(m.group(1)))
                psalms[cur.number] = cur
                seen_line = seen_sec = False
            continue
        if cur is None:
            continue
        txt = text_of(el)
        if cls in ("parafl",):  # "See RG ..." reader's-guide pointer
            continue
        if cls == "paraflc":
            cur.title = txt
            continue
        if cls == "line2":
            # Print run-over of the previous line: join it back.
            if not cur.blocks:
                raise ValueError(f"Psalm {cur.number}: run-over line with nothing before it")
            append_inline(cur.blocks[-1][1], clean_line(el))
            continue
        if cls == "linec":
            if ROMAN.match(txt):
                kind = "sec"
                seen_sec = True
            elif not seen_line and not seen_sec:
                kind = "super"
            else:
                kind = "l"  # mis-tagged first line of a strophe
        elif cls in ("line", "liner"):
            only_sc = (len(el) == 1 and classes(el[0]) == "sc" and not (el.text or "").strip()
                       and not (el[0].tail or "").strip())
            kind = "stanza" if only_sc else "l"
        elif cls == "line1":
            kind = "l1"
        elif cls == "right":
            kind = "selah"
        else:
            raise ValueError(f"Psalm {cur.number}: unexpected paragraph class {cls!r}: {txt[:60]}")
        if kind in ("l", "l1"):
            seen_line = True
        cur.blocks.append((kind, clean_line(el)))
    for n, words in MISSING_SUPERSCRIPTIONS.items():
        ps = psalms.get(n)
        if ps and not any(k == "super" for k, _ in ps.blocks):
            p = new_el("p")
            vn = etree.SubElement(p, x("span"), {"class": "vn"})
            vn.text, vn.tail = "1", words
            ps.blocks.insert(0, ("super", p))
    missing = sorted(set(range(1, 151)) - set(psalms))
    if missing:
        raise SystemExit(f"psalms missing from Bible text: {missing}")
    for ps in psalms.values():
        if not any(k in ("l", "l1") for k, _ in ps.blocks):
            raise SystemExit(f"Psalm {ps.number} has no verse lines")
    return psalms


KIND_CLASS = {"super": "nab-super", "sec": "nab-sec", "stanza": "nab-stanza",
              "l": "nab-l", "l1": "nab-l1", "selah": "nab-selah"}


def psalm_block(ps: Psalm) -> list:
    div = new_el("div", {"class": "nab"})
    if ps.title:
        t = etree.SubElement(div, x("p"), {"class": "nab-title"})
        t.text = ps.title
    for kind, p in ps.blocks:
        new = copy.deepcopy(p)
        new.attrib.clear()
        new.tag = x("p")
        new.set("class", KIND_CLASS[kind])
        div.append(new)
    sep = new_el("p", {"class": "nab-sep"})
    sep.text = "* * *"
    return [div, sep]


# --------------------------------------------------------------------------
# Commentary side


def heading(ah, number: int):
    """Turn 'PSALM 1    tree' into a structured <h2>."""
    h2 = new_el("h2", {"class": "ah", "id": ah.get("id")})
    for a in ah.iter(x("a")):
        if classes(a) == "page":
            h2.append(new_el("a", {"class": "page", "id": a.get("id")}))
    tail = ""
    for sm in ah.iter(x("span")):
        tail = sm.tail or ""
    m = re.match(r"\s*(\d+)[\s ]+(.*)$", tail, re.S)
    if not m or int(m.group(1)) != number:
        raise ValueError(f"unexpected heading for Psalm {number}: {text_of(ah)!r}")
    if len(h2):
        h2[-1].tail = "P"
    else:
        h2.text = "P"
    sm = etree.SubElement(h2, x("span"), {"class": "sm"})
    sm.text = "SALM"
    sm.tail = f" {number}"
    key = new_el("p", {"class": "ahkey"})
    key.text = m.group(2).strip()
    key.tail = "\n"
    return h2, key


DOCTYPE = ('<!DOCTYPE html PUBLIC "-//W3C//DTD XHTML 1.1//EN" '
           '"http://www.w3.org/TR/xhtml11/DTD/xhtml11.dtd">')
HEAD = """<?xml version="1.0" encoding="utf-8"?>
""" + DOCTYPE + """
<html xmlns="http://www.w3.org/1999/xhtml" xml:lang="en">
<head>
<meta http-equiv="Content-Type" content="text/html; charset=utf-8"/>
<title>{title}</title>
<link href="style.css" rel="stylesheet" type="text/css"/>
</head>
<body>
"""
TAIL = "</body>\n</html>\n"


def serialize(elements) -> str:
    out = []
    for el in elements:
        s = etree.tostring(el, encoding="unicode")
        out.append(s)
    text = "\n".join(out)
    # lxml writes xmlns on every top-level fragment; the document root has it.
    return text.replace(' xmlns="http://www.w3.org/1999/xhtml"', "")


def page(title: str, body: str) -> bytes:
    return (HEAD.format(title=escape(title)) + body + "\n" + TAIL).encode("utf-8")


def inline_content(cell):
    """A table cell's inline content as (text, children), dropping <p> wrappers."""
    holder = new_el("span")
    paras = [c for c in cell if c.tag == x("p")]
    for src in (paras or [cell]):
        part = copy.deepcopy(src)
        if len(holder) or (holder.text or "").strip():
            append_inline(holder, part)
        else:
            holder.text = part.text
            for child in list(part):
                holder.append(child)
    return holder


def flatten_tables(root) -> None:
    """Replace each table by one paragraph per row: 'cell — cell — cell'."""
    for table in list(root.iter(x("table"))):
        parent = table.getparent()
        idx = parent.index(table)
        rows = []
        for tr in table.iter(x("tr")):
            cells = [inline_content(td) for td in tr if td.tag in (x("td"), x("th"))]
            cells = [c for c in cells if "".join(c.itertext()).strip()]
            if not cells:
                continue
            head = any(classes(p) in ("tch", "tch1", "th")
                       for td in tr for p in td.iter(x("p")))
            p = new_el("p", {"class": "row-head" if head else "row"})
            p.text = cells[0].text
            for child in list(cells[0]):
                p.append(child)
            for cell in cells[1:]:
                prev = "".join(p.itertext()).rstrip()
                sep = " " if prev.endswith(":") else " — "
                cell.text = sep + (cell.text or "").lstrip()
                append_inline(p, cell)
            # Keep anchors that sat on the row, its cells or their paragraphs.
            anchors = [el.get("id") for el in [tr, *tr.iter(x("td"), x("th"), x("p"))]
                       if el.get("id")]
            for k, anchor in enumerate(anchors):
                a = new_el("a", {"id": anchor})
                a.tail = p.text if k == 0 else None
                if k == 0:
                    p.text = None
                p.insert(k, a)
            p.tail = "\n"
            rows.append(p)
        tail = table.tail
        parent.remove(table)
        for k, p in enumerate(rows):
            parent.insert(idx + k, p)
        if rows:
            rows[-1].tail = (rows[-1].tail or "") + (tail or "")


def plain_images(root) -> None:
    for img in root.iter(x("img")):
        img.attrib.pop("width", None)
        img.attrib.pop("height", None)


def normalise_existing(data: bytes, title: str) -> bytes:
    """Re-head an original XHTML file as clean EPUB 3 XHTML."""
    root = etree.fromstring(data, PARSER)
    flatten_tables(root)
    plain_images(root)
    body = root.find("h:body", NS)
    inner = (body.text or "") + "".join(etree.tostring(c, encoding="unicode") for c in body)
    inner = inner.replace(' xmlns="http://www.w3.org/1999/xhtml"', "")
    return page(title, inner.strip())


@dataclass
class Section:
    number: int
    key: str
    elements: list


def split_commentary(files: list[bytes]):
    lead: list = []
    sections: list[Section] = []
    for data in files:
        root = etree.fromstring(data, PARSER)
        chapter = root.find("h:body/h:div", NS)
        flatten_tables(chapter)
        for el in chapter:
            if el.tag is etree.Comment:
                continue
            if el.tag == x("p") and classes(el) == "ah":
                n = int(re.search(r"(\d+)", (el.xpath("string(.)"))).group(1))
                h2, key = heading(el, n)
                h2.tail = "\n"
                sections.append(Section(n, key.text, [h2, key]))
            elif sections:
                sections[-1].elements.append(el)
            else:
                lead.append(el)
    nums = [s.number for s in sections]
    if nums != list(range(1, 151)):
        raise SystemExit(f"commentary sections out of order or missing: {nums[:5]}...")
    return lead, sections


def psalm_file(n: int) -> str:
    return f"psalm-{n:03d}.html"


def list_page(sections: list[Section]) -> str:
    rows = ['<div class="chapter" id="heads">',
            '<p class="ct">Psalms 1–150</p>']
    for name, lo, hi in BOOKS:
        rows.append(f'<p class="row-head">{name} (Psalms {lo}–{hi})</p>')
        for s in sections[lo - 1:hi]:
            rows.append(f'<p class="row"><a href="{psalm_file(s.number)}">Psalm {s.number}</a>'
                        f' — {escape(s.key)}</p>')
    rows.append("</div>")
    return "\n".join(rows)


# --------------------------------------------------------------------------
# Link rewriting


VOID = {"br", "hr", "img", "meta", "link", "col", "input", "wbr", "source"}


def close_empty_elements(data: bytes) -> bytes:
    """Write <a id="p13"></a> rather than <a id="p13"/>.

    A self-closed non-void element is fine as XML, but any reader that parses
    the page as HTML opens a link there that never closes.
    """
    root = etree.fromstring(data, PARSER)
    for el in root.iter():
        if (isinstance(el.tag, str) and el.text is None and len(el) == 0
                and etree.QName(el).localname not in VOID):
            el.text = ""
    body = etree.tostring(root, encoding="unicode")
    return ('<?xml version="1.0" encoding="utf-8"?>\n' + DOCTYPE + "\n" + body + "\n").encode("utf-8")


def collect_ids(files: dict[str, bytes]) -> dict[str, str]:
    ids: dict[str, str] = {}
    for name, data in files.items():
        root = etree.fromstring(data, PARSER)
        for el in root.iter():
            i = el.get("id") if isinstance(el.tag, str) else None
            if i and i not in ids:
                ids[i] = name
    return ids


def retarget(href: str, ids: dict[str, str], moved: set[str], default: str) -> str:
    path, _, frag = href.partition("#")
    if posixpath.basename(path) not in moved:
        return href
    if not frag:
        return default
    if frag not in ids:
        raise ValueError(f"link target {href} no longer exists")
    return f"{ids[frag]}#{frag}"


def rewrite_links(files: dict[str, bytes], ids, moved, default) -> dict[str, bytes]:
    pat = re.compile(rb'(href|src)="([^"]+)"')

    def sub(m):
        attr, val = m.group(1), m.group(2).decode("utf-8")
        return attr + b'="' + retarget(val, ids, moved, default).encode("utf-8") + b'"'

    return {name: pat.sub(sub, data) for name, data in files.items()}


# --------------------------------------------------------------------------
# Packaging


def nav_structure(sections: list[Section], orig_nav: list[tuple[str, str]]):
    """[(label, href, children)] mirroring the original NCX, psalms nested."""
    by_num = {s.number: s for s in sections}
    out = []
    for label, href in orig_nav:
        if label.startswith("Psalms 1"):
            books = []
            for name, lo, hi in BOOKS:
                kids = [(f"Psalm {n} — {by_num[n].key}", psalm_file(n), [])
                        for n in range(lo, hi + 1)]
                books.append((f"{name} (Psalms {lo}–{hi})", psalm_file(lo), kids))
            out.append(("Psalms 1–150", "psalms.html", books))
        else:
            out.append((label, href, []))
    return out


def build_ncx(nav, uid: str, title: str) -> bytes:
    counter = [0]
    order: dict[str, int] = {}  # NCX requires one playOrder per target

    def points(items, depth):
        s = []
        for label, href, kids in items:
            counter[0] += 1
            n = counter[0]
            play = order.setdefault(href, len(order) + 1)
            pad = "  " * depth
            s.append(f'{pad}<navPoint id="np{n}" playOrder="{play}">'
                     f'<navLabel><text>{escape(label)}</text></navLabel>'
                     f'<content src="{escape(href)}"/>')
            s.extend(points(kids, depth + 1))
            s.append(f"{pad}</navPoint>")
        return s

    body = "\n".join(points(nav, 1))
    return f"""<?xml version="1.0" encoding="utf-8"?>
<ncx xmlns="http://www.daisy.org/z3986/2005/ncx/" version="2005-1">
<head>
<meta name="dtb:uid" content="{uid}"/>
<meta name="dtb:depth" content="3"/>
<meta name="dtb:totalPageCount" content="0"/>
<meta name="dtb:maxPageNumber" content="0"/>
</head>
<docTitle><text>{escape(title)}</text></docTitle>
<navMap>
{body}
</navMap>
</ncx>
""".encode("utf-8")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("bible")
    ap.add_argument("psalms")
    ap.add_argument("output")
    ap.add_argument("--title", default="Praying the Psalms in Christ (with NABRE Psalms)")
    args = ap.parse_args(argv)

    with zipfile.ZipFile(args.bible) as bz:
        psalms = extract_psalms(find_psalms_doc(bz))

    src = zipfile.ZipFile(args.psalms)
    base = "OEBPS/"
    names = [n for n in src.namelist() if n.startswith(base)]
    get = lambda n: src.read(base + n)  # noqa: E731

    lead, sections = split_commentary([get(n) for n in COMMENTARY])

    # Read original spine order and NCX labels.
    opf = etree.fromstring(get("content.opf"))
    opf_ns = {"o": "http://www.idpf.org/2007/opf"}
    manifest = {i.get("id"): i.get("href") for i in opf.iterfind(".//o:manifest/o:item", opf_ns)}
    spine = [manifest[r.get("idref")] for r in opf.iterfind(".//o:spine/o:itemref", opf_ns)]
    ncx = etree.fromstring(get("toc.ncx"))
    ncx_ns = {"n": "http://www.daisy.org/z3986/2005/ncx/"}
    orig_nav = [(p.findtext("n:navLabel/n:text", namespaces=ncx_ns),
                 p.find("n:content", ncx_ns).get("src"))
                for p in ncx.iterfind("n:navMap/n:navPoint", ncx_ns)]

    html: dict[str, bytes] = {}
    for name in spine:
        if name in COMMENTARY:
            continue
        html[name] = normalise_existing(get(name), "Praying the Psalms in Christ")

    # Front-matter note on the copyright page.
    note = ('<p class="edition-note"><b>About this edition.</b> The text of each psalm '
            'printed before its commentary is the New American Bible, Revised Edition, '
            'as published in <i>The Catholic Study Bible</i> (Oxford University Press). '
            'Scripture texts: New American Bible, revised edition © 2010, 1991, 1986, '
            '1970 Confraternity of Christian Doctrine, Washington, D.C. All rights reserved. '
            'The commentary quotes the psalms in the author’s own translation, '
            'which often differs in wording from the NABRE.</p>')
    cp = "und-kriegshauser-0004.html"
    html[cp] = html[cp].replace(b"</body>", note.encode("utf-8") + b"\n</body>", 1)

    grid = list_page(sections)
    # Keep any page anchors that sat before the first psalm heading.
    lead_extra = [el for el in lead if not (el.tag == x("p") and classes(el) == "ct")]
    html["psalms.html"] = page("Psalms 1–150", grid + ("\n" + serialize(lead_extra) if lead_extra else ""))
    for s in sections:
        body = s.elements[:2] + psalm_block(psalms[s.number]) + s.elements[2:]
        inner = '<div class="chapter psalm">\n' + serialize(body) + "\n</div>"
        html[psalm_file(s.number)] = page(f"Psalm {s.number}", inner)

    ids = collect_ids(html)
    html = rewrite_links(html, ids, set(COMMENTARY), "psalms.html")
    nav = nav_structure(sections, [(l, retarget(h, ids, set(COMMENTARY), "psalms.html"))
                                   for l, h in orig_nav])

    uid = "urn:uuid:" + str(uuid.uuid5(uuid.NAMESPACE_URL, "praying-the-psalms-in-christ+nabre"))

    new_spine = []
    for name in spine:
        if name == COMMENTARY[0]:
            new_spine.append("psalms.html")
            new_spine.extend(psalm_file(s.number) for s in sections)
        elif name not in COMMENTARY:
            new_spine.append(name)

    # Sanity: every internal link resolves.
    ids = collect_ids(html)
    files = set(html) | {n[len(base):] for n in names}
    for name, data in html.items():
        for m in re.finditer(rb'href="([^"#:]*)(?:#([^"]*))?"', data):
            path = m.group(1).decode() or name
            frag = m.group(2).decode() if m.group(2) else None
            if path.endswith(".css"):
                continue
            if path not in files:
                raise SystemExit(f"{name}: dead link to {path}")
            if frag and ids.get(frag) != path:
                raise SystemExit(f"{name}: dead anchor {path}#{frag}")

    css = STYLE_CSS.encode("utf-8")

    date = opf.findtext(".//{http://purl.org/dc/elements/1.1/}date") or ""
    items = ['<item id="ncx" href="toc.ncx" media-type="application/x-dtbncx+xml"/>',
             '<item id="css" href="style.css" media-type="text/css"/>']
    ids_for = {}
    for i, name in enumerate(html):
        ids_for[name] = f"x{i:03d}"
        items.append(f'<item id="x{i:03d}" href="{name}" media-type="application/xhtml+xml"/>')
    others = [n[len(base):] for n in names
              if n.endswith((".jpg", ".jpeg", ".png"))]
    for i, name in enumerate(others):
        mt = {"jpg": "image/jpeg", "jpeg": "image/jpeg", "png": "image/png"}[name.rsplit(".", 1)[1]]
        iid = "cover-image" if name.endswith("-cover.jpg") else f"r{i:02d}"
        items.append(f'<item id="{iid}" href="{name}" media-type="{mt}"/>')
    itemrefs = "\n    ".join(f'<itemref idref="{ids_for[n]}"/>' for n in new_spine)
    opf_out = f"""<?xml version="1.0" encoding="utf-8"?>
<package xmlns="http://www.idpf.org/2007/opf" version="2.0" unique-identifier="uid">
  <metadata xmlns:dc="http://purl.org/dc/elements/1.1/" xmlns:opf="http://www.idpf.org/2007/opf">
    <dc:identifier id="uid" opf:scheme="UUID">{uid}</dc:identifier>
    <dc:title>{escape(args.title)}</dc:title>
    <dc:creator opf:file-as="Kriegshauser, Laurence" opf:role="aut">Laurence Kriegshauser, O.S.B.</dc:creator>
    <dc:language>en</dc:language>
    <dc:publisher>University of Notre Dame Press</dc:publisher>
    <dc:date>{date}</dc:date>
    <dc:source>urn:isbn:9780268084523</dc:source>
    <dc:subject>Bible. Psalms -- Commentaries</dc:subject>
    <dc:description>Laurence Kriegshauser’s commentary on all 150 psalms, with the New American Bible (Revised Edition) text of each psalm placed before its commentary.</dc:description>
    <dc:rights>All rights reserved</dc:rights>
    <meta name="cover" content="cover-image"/>
  </metadata>
  <manifest>
    {chr(10).join("    " + i for i in items).strip()}
  </manifest>
  <spine toc="ncx">
    {itemrefs}
  </spine>
  <guide>
    <reference type="cover" title="Cover" href="und-kriegshauser-0001.html"/>
    <reference type="toc" title="Table of Contents" href="und-kriegshauser-0006.html#toc"/>
    <reference type="text" title="Introduction" href="und-kriegshauser-0009.html"/>
  </guide>
</package>
"""

    with zipfile.ZipFile(args.output, "w") as out:
        def w(name, data, method=zipfile.ZIP_DEFLATED):
            info = zipfile.ZipInfo(name, date_time=(2026, 1, 1, 0, 0, 0))
            info.external_attr = 0o100644 << 16
            info.compress_type = method
            out.writestr(info, data)

        w("mimetype", b"application/epub+zip", zipfile.ZIP_STORED)

        w("META-INF/container.xml", src.read("META-INF/container.xml"))
        w(base + "content.opf", opf_out.encode("utf-8"))
        w(base + "toc.ncx", build_ncx(nav, uid, args.title))
        w(base + "style.css", css)
        for name, data in html.items():
            w(base + name, close_empty_elements(data))
        for name in others:
            w(base + name, get(name))

    n_lines = sum(sum(k in ("l", "l1") for k, _ in p.blocks) for p in psalms.values())
    print(f"wrote {args.output}: 150 psalms ({n_lines} poetry lines) injected")
    return 0


if __name__ == "__main__":
    sys.exit(main())
