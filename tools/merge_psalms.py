#!/usr/bin/env python3
"""Inject NABRE psalm texts into *Praying the Psalms in Christ*.

Extracts Psalms 1-150 from *The Catholic Study Bible* (New American Bible,
Revised Edition) EPUB and places each psalm at the start of the matching
commentary in Laurence Kriegshauser's *Praying the Psalms in Christ*. The
result is a new EPUB 3 with one file per psalm, a 1-150 jump grid and a
nested table of contents. Every footnote, index and contents link into the
old commentary files is rewritten to the file that now holds its target.

usage: python tools/merge_psalms.py BIBLE.epub PSALMS.epub OUT.epub
"""

from __future__ import annotations

import argparse
import copy
import datetime as _dt
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

NAB_CSS = """
/* ---- NABRE psalm texts (added) ---- */
h2.ah	{font-size:x-large; font-weight:normal; margin-top:1em; margin-bottom:1.2em; text-align:left; text-indent:0; page-break-after:avoid;}
h2.ah .ahkey	{display:block; font-size:65%; font-style:italic; margin-top:.25em; color:#555;}
.nab	{margin:0 0 .5em 0;}
.nab p	{text-align:left; margin-top:0; margin-bottom:0;}
.nab .nab-title	{font-weight:bold; font-style:italic; text-indent:0; margin-bottom:.6em; page-break-after:avoid;}
.nab .nab-super	{font-style:italic; font-size:90%; text-indent:0; margin-bottom:.6em;}
.nab .nab-sec	{text-align:center; text-indent:0; font-size:90%; letter-spacing:.15em; margin-top:.7em; margin-bottom:.3em; page-break-after:avoid;}
.nab .nab-stanza	{text-align:center; text-indent:0; margin-top:.9em; margin-bottom:.3em; page-break-after:avoid;}
.nab .nab-l	{margin-left:1.6em; text-indent:-1.6em;}
.nab .nab-l1	{margin-left:3.2em; text-indent:-1.6em;}
.nab .nab-selah	{text-align:right; text-indent:0; margin-right:1em;}
.nab .vn	{font-size:60%; vertical-align:super; line-height:0; color:#777; padding-right:.25em;}
.nab-src	{text-align:right; text-indent:0; font-size:70%; color:#777; margin-top:.6em;}
hr.nab-rule	{width:30%; margin:1.2em auto 1.4em auto; border:0; border-top:1px solid #999; height:0;}
.psalm-grid-book	{font-size:medium; font-weight:bold; text-align:left; text-indent:0; margin-top:1.4em; margin-bottom:.4em;}
table.psalm-grid	{border-collapse:collapse; margin:0 auto; width:100%;}
table.psalm-grid td	{text-align:center; padding:.35em 0; width:10%;}
.edition-note	{font-size:small; text-align:left; text-indent:0; margin-top:2em;}
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
    rule = new_el("hr", {"class": "nab-rule"})
    return [div, rule]


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
    key = etree.SubElement(h2, x("span"), {"class": "ahkey"})
    key.text = m.group(2).strip()
    return h2, key.text


HEAD = """<?xml version="1.0" encoding="utf-8"?>
<!DOCTYPE html>
<html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops" xml:lang="en" lang="en">
<head>
<meta charset="utf-8"/>
<title>{title}</title>
<link href="und.css" rel="stylesheet" type="text/css"/>
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


def modernise_tables(root) -> None:
    """Move EPUB 2 era table/col widths into CSS and wrap cols in a colgroup."""
    for el in root.iter(x("table"), x("col")):
        w = el.attrib.pop("width", None)
        if w:
            w = w if w.endswith("%") else w + "px"
            el.set("style", f"width:{w};" + (el.get("style") or ""))
    for table in root.iter(x("table")):
        cols = [c for c in table if c.tag == x("col")]
        if cols:
            group = new_el("colgroup")
            table.insert(table.index(cols[0]), group)
            for c in cols:
                group.append(c)


def normalise_existing(data: bytes, title: str) -> bytes:
    """Re-head an original XHTML file as clean EPUB 3 XHTML."""
    root = etree.fromstring(data, PARSER)
    modernise_tables(root)
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
        for el in chapter:
            if el.tag is etree.Comment:
                continue
            modernise_tables(el)
            if el.tag == x("p") and classes(el) == "ah":
                n = int(re.search(r"(\d+)", (el.xpath("string(.)"))).group(1))
                h2, key = heading(el, n)
                h2.tail = "\n"
                sections.append(Section(n, key, [h2]))
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


def grid_page(sections: list[Section]) -> str:
    rows = ['<div class="chapter" id="heads">',
            '<p class="ct">Psalms 1–150</p>']
    for name, lo, hi in BOOKS:
        rows.append(f'<p class="psalm-grid-book">{name} · Psalms {lo}–{hi}</p>')
        rows.append('<table class="psalm-grid">')
        nums = list(range(lo, hi + 1))
        for i in range(0, len(nums), 10):
            chunk = nums[i:i + 10]
            cells = "".join(f'<td><a href="{psalm_file(n)}">{n}</a></td>' for n in chunk)
            cells += "<td></td>" * (10 - len(chunk))
            rows.append(f"<tr>{cells}</tr>")
        rows.append("</table>")
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
    return etree.tostring(root.getroottree(), xml_declaration=True, encoding="utf-8",
                          doctype="<!DOCTYPE html>")


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


def build_nav(nav, landmarks) -> bytes:
    def ol(items, depth):
        pad = "  " * depth
        s = [f"{pad}<ol>"]
        for label, href, kids in items:
            if kids:
                s.append(f'{pad}  <li><a href="{escape(href)}">{escape(label)}</a>')
                s.extend(ol(kids, depth + 2))
                s.append(f"{pad}  </li>")
            else:
                s.append(f'{pad}  <li><a href="{escape(href)}">{escape(label)}</a></li>')
        s.append(f"{pad}</ol>")
        return s

    lm = "\n".join(f'    <li><a epub:type="{t}" href="{escape(h)}">{escape(l)}</a></li>'
                   for t, h, l in landmarks)
    body = ('<nav epub:type="toc" id="toc">\n<h1>Contents</h1>\n'
            + "\n".join(ol(nav, 0)) + "\n</nav>\n"
            + f'<nav epub:type="landmarks" hidden="">\n  <ol>\n{lm}\n  </ol>\n</nav>')
    return page("Contents", body)


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

    grid = grid_page(sections)
    # Keep any page anchors that sat before the first psalm heading.
    lead_extra = [el for el in lead if not (el.tag == x("p") and classes(el) == "ct")]
    html["psalms.html"] = page("Psalms 1–150", grid + ("\n" + serialize(lead_extra) if lead_extra else ""))
    for s in sections:
        body = [s.elements[0]] + psalm_block(psalms[s.number]) + s.elements[1:]
        inner = '<div class="chapter psalm">\n' + serialize(body) + "\n</div>"
        html[psalm_file(s.number)] = page(f"Psalm {s.number}", inner)

    ids = collect_ids(html)
    html = rewrite_links(html, ids, set(COMMENTARY), "psalms.html")
    nav = nav_structure(sections, [(l, retarget(h, ids, set(COMMENTARY), "psalms.html"))
                                   for l, h in orig_nav])

    uid = "urn:uuid:" + str(uuid.uuid5(uuid.NAMESPACE_URL, "praying-the-psalms-in-christ+nabre"))
    html["nav.xhtml"] = build_nav(nav, [
        ("cover", "und-kriegshauser-0001.html", "Cover"),
        ("toc", "und-kriegshauser-0006.html", "Contents"),
        ("bodymatter", "und-kriegshauser-0009.html", "Introduction"),
    ])

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

    css = get("und.css") + NAB_CSS.encode("utf-8")

    date = opf.findtext(".//{http://purl.org/dc/elements/1.1/}date") or ""
    modified = _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    items = ['<item id="ncx" href="toc.ncx" media-type="application/x-dtbncx+xml"/>',
             '<item id="nav" href="nav.xhtml" media-type="application/xhtml+xml" properties="nav"/>',
             '<item id="css" href="und.css" media-type="text/css"/>']
    ids_for = {}
    for i, name in enumerate(n for n in html if n != "nav.xhtml"):
        ids_for[name] = f"x{i:03d}"
        items.append(f'<item id="x{i:03d}" href="{name}" media-type="application/xhtml+xml"/>')
    others = [n[len(base):] for n in names
              if n.endswith((".jpg", ".jpeg", ".png", ".otf", ".ttf"))]
    for i, name in enumerate(others):
        mt = {"jpg": "image/jpeg", "jpeg": "image/jpeg", "png": "image/png",
              "otf": "font/otf", "ttf": "font/ttf"}[name.rsplit(".", 1)[1]]
        props = ' properties="cover-image"' if name.endswith("-cover.jpg") else ""
        iid = "cover-image" if props else f"r{i:02d}"
        items.append(f'<item id="{iid}" href="{name}" media-type="{mt}"{props}/>')
    itemrefs = "\n    ".join(f'<itemref idref="{ids_for[n]}"/>' for n in new_spine)
    opf_out = f"""<?xml version="1.0" encoding="utf-8"?>
<package xmlns="http://www.idpf.org/2007/opf" version="3.0" unique-identifier="uid" xml:lang="en">
  <metadata xmlns:dc="http://purl.org/dc/elements/1.1/">
    <dc:identifier id="uid">{uid}</dc:identifier>
    <dc:title>{escape(args.title)}</dc:title>
    <dc:creator id="aut">Laurence Kriegshauser, O.S.B.</dc:creator>
    <meta refines="#aut" property="file-as">Kriegshauser, Laurence</meta>
    <meta refines="#aut" property="role" scheme="marc:relators">aut</meta>
    <dc:language>en</dc:language>
    <dc:publisher>University of Notre Dame Press</dc:publisher>
    <dc:date>{date}</dc:date>
    <dc:source>urn:isbn:9780268084523</dc:source>
    <dc:subject>Bible. Psalms -- Commentaries</dc:subject>
    <dc:description>Laurence Kriegshauser’s commentary on all 150 psalms, with the New American Bible (Revised Edition) text of each psalm placed before its commentary.</dc:description>
    <dc:rights>All rights reserved</dc:rights>
    <meta property="dcterms:modified">{modified}</meta>
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
        out.writestr(zipfile.ZipInfo("mimetype"), b"application/epub+zip",
                     compress_type=zipfile.ZIP_STORED)

        def w(name, data):
            out.writestr(name, data, compress_type=zipfile.ZIP_DEFLATED)

        w("META-INF/container.xml", src.read("META-INF/container.xml"))
        w(base + "content.opf", opf_out.encode("utf-8"))
        w(base + "toc.ncx", build_ncx(nav, uid, args.title))
        w(base + "und.css", css)
        for name, data in html.items():
            w(base + name, close_empty_elements(data))
        for name in others:
            w(base + name, get(name))

    n_lines = sum(sum(k in ("l", "l1") for k, _ in p.blocks) for p in psalms.values())
    print(f"wrote {args.output}: 150 psalms ({n_lines} poetry lines) injected")
    return 0


if __name__ == "__main__":
    sys.exit(main())
