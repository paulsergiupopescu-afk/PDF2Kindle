"""Re-typeset an existing EPUB for Kindle.

Plenty of EPUBs in the wild -- old Calibre/Sigil exports, retailer files cut
from InDesign -- are valid but read badly on a Kindle: every paragraph pins
its own embedded font, pixel indents and pure-black colour (so the reader's
font, margin and dark-mode settings stop working), chapter titles are styled
``<p>``s the table of contents never sees, and footnotes are bare link pairs
instead of pop-ups. This module reads such a book back into the same
:class:`~pdf2kindle.model.Document` the PDF pipeline produces, so it is
written out by the same :func:`~pdf2kindle.epub.build_epub` -- one
stylesheet, real headings, a nested contents, pop-up notes -- and audited
the same way.

Everything is recovered from what the book itself declares: its stylesheet
(which classes are big and centred, which are indented insets or verse), its
NCX/nav (where the front matter ends and what the top-level sections are),
and its link structure (a marker and a note that link to each other are a
footnote pair). Nothing is keyed to a particular publisher's class names.
"""

from __future__ import annotations

import io
import posixpath
import re
import unicodedata
import zipfile
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Set, Tuple
from urllib.parse import unquote

from lxml import etree

from .epub import build_epub
from .model import Chapter, Document, Element, ElementKind, ImageBlock, InlineRun, SubHead

_NS = {
    "opf": "http://www.idpf.org/2007/opf",
    "dc": "http://purl.org/dc/elements/1.1/",
    "ncx": "http://www.daisy.org/z3986/2005/ncx/",
    "xhtml": "http://www.w3.org/1999/xhtml",
    "epub": "http://www.idpf.org/2007/ops",
    "container": "urn:oasis:names:tc:opendocument:xmlns:container",
}
_XLINK_HREF = "{http://www.w3.org/1999/xlink}href"
_EPUB_TYPE = "{http://www.idpf.org/2007/ops}type"

_BLOCK_LEAVES = {"p", "h1", "h2", "h3", "h4", "h5", "h6", "li", "dt", "dd", "pre", "figcaption", "caption", "td", "th"}
_CONTAINERS = {"body", "div", "section", "article", "aside", "blockquote", "figure", "main", "header", "footer",
               "ul", "ol", "dl", "table", "tbody", "thead", "tfoot", "tr", "nav"}
_SKIP = {"head", "script", "style", "title", "meta", "link"}

# A heading is noticeably larger than body text and short enough to be a
# title rather than a paragraph someone set in a bigger size.
_HEADING_MIN_SIZE = 1.15
_HEADING_MAX_CHARS = 100
# A short line set smaller than the body (a song quoted in dialogue, a
# signature under a letter) reads as verse, not as a run-on paragraph.
_VERSE_MAX_CHARS = 90
# A paragraph of nothing but leader dots/stars is a printed separator line;
# set full-width it wraps on a phone-sized screen, so it is shortened.
_SEPARATOR_RE = re.compile(r"^[\s.·•*…_\-–—]{5,}$")
_DECORATION = "~*·•–—-_  "
_WS_RE = re.compile(r"[ \t\r\n]+")

# Romanian is written with comma-below ș/ț; legacy files use the cedilla
# forms because old fonts lacked the right glyphs. Kindle search and
# dictionary lookup only match the correct letters.
_RO_DIACRITICS = str.maketrans({"ş": "ș", "ţ": "ț", "Ş": "Ș", "Ţ": "Ț"})


@dataclass
class OptimizeOptions:
    title: str = ""
    author: str = ""
    language: str = ""
    fix_diacritics: bool = True  # normalize legacy cedilla ş/ţ in Romanian books


@dataclass
class OptimizeResult:
    output_path: str
    title: str
    author: str
    chapters: int = 0
    footnotes: int = 0
    images: int = 0
    dropped_fonts: int = 0
    warnings: List[str] = field(default_factory=list)


# --------------------------------------------------------------------------
# CSS: just enough to answer "how big / how aligned / how indented is this?"


@dataclass
class _Style:
    size: float = 1.0  # relative to the parent
    align: str = ""
    indent: Optional[bool] = None  # text-indent non-zero
    inset: Optional[bool] = None  # margin-left non-zero
    italic: Optional[bool] = None
    bold: Optional[bool] = None
    sup: bool = False


_RULE_RE = re.compile(r"([^{}]+)\{([^{}]*)\}")
_SIMPLE_SEL_RE = re.compile(r"^([a-zA-Z][\w-]*)?((?:\.[\w-]+)*)$")
_KEYWORD_SIZES = {"xx-small": 0.6, "x-small": 0.75, "small": 0.89, "medium": 1.0, "large": 1.2,
                  "x-large": 1.5, "xx-large": 2.0, "smaller": 0.83, "larger": 1.2}


def _strip_at_rules(css: str) -> str:
    css = re.sub(r"/\*.*?\*/", "", css, flags=re.S)
    css = re.sub(r"@(namespace|import|charset)[^;]*;", "", css)
    # Drop nested at-blocks (@media, @font-face, @page) wholesale.
    out, depth, i = [], 0, 0
    while i < len(css):
        if depth == 0 and css[i] == "@":
            j = css.find("{", i)
            if j < 0:
                break
            depth, i = 1, j + 1
            while i < len(css) and depth:
                depth += {"{": 1, "}": -1}.get(css[i], 0)
                i += 1
            continue
        out.append(css[i])
        i += 1
    return "".join(out)


def _length_nonzero(value: str) -> bool:
    m = re.match(r"\s*(-?[\d.]+)", value)
    return bool(m) and abs(float(m.group(1))) > 0.01


def _font_size(value: str) -> Optional[float]:
    value = value.strip().lower()
    if value in _KEYWORD_SIZES:
        return _KEYWORD_SIZES[value]
    m = re.match(r"(-?[\d.]+)\s*(em|rem|%|px|pt)?", value)
    if not m:
        return None
    n, unit = float(m.group(1)), m.group(2) or "em"
    return {"em": n, "rem": n, "%": n / 100, "px": n / 16, "pt": n / 12}[unit]


def _parse_decls(text: str) -> _Style:
    st = _Style()
    for decl in text.split(";"):
        if ":" not in decl:
            continue
        prop, value = (s.strip().lower() for s in decl.split(":", 1))
        value = value.replace("!important", "").strip()
        if prop == "font-size":
            size = _font_size(value)
            if size:
                st.size = size
        elif prop == "text-align":
            st.align = value
        elif prop == "text-indent":
            st.indent = _length_nonzero(value)
        elif prop == "margin-left":
            st.inset = _length_nonzero(value)
        elif prop == "margin" and value:
            parts = value.split()
            st.inset = _length_nonzero(parts[{1: 0, 2: 1, 3: 1}.get(len(parts), 3)])
        elif prop == "font-style":
            st.italic = value in ("italic", "oblique")
        elif prop == "font-weight":
            st.bold = value in ("bold", "bolder") or (value.isdigit() and int(value) >= 600)
        elif prop == "vertical-align":
            st.sup = value in ("super", "top")
    return st


class _Sheet:
    """Rules from a document's stylesheets, matched by tag and class."""

    def __init__(self, css: str):
        # (tag or "", frozenset(classes), order, style)
        self.rules: List[Tuple[str, frozenset, int, _Style]] = []
        for order, (sels, body) in enumerate(_RULE_RE.findall(_strip_at_rules(css))):
            st = _parse_decls(body)
            for sel in sels.split(","):
                m = _SIMPLE_SEL_RE.match(sel.strip())
                if m and (m.group(1) or m.group(2)):
                    classes = frozenset(c for c in m.group(2).split(".") if c)
                    self.rules.append(((m.group(1) or "").lower(), classes, order, st))

    def style_for(self, tag: str, classes: Set[str], inline: str = "") -> _Style:
        matched = [r for r in self.rules if (not r[0] or r[0] == tag) and r[1] and r[1] <= classes]
        matched += [r for r in self.rules if r[0] == tag and not r[1]]
        # Lower specificity first, then source order -- later wins.
        matched.sort(key=lambda r: (len(r[1]) + (1 if r[0] else 0), r[2]))
        out = _Style()
        for *_, st in matched + [("", frozenset(), 0, _parse_decls(inline))]:
            if st.size != 1.0:
                out.size = st.size
            for attr in ("align", "indent", "inset", "italic", "bold"):
                if getattr(st, attr) not in (None, ""):
                    setattr(out, attr, getattr(st, attr))
            out.sup = out.sup or st.sup
        return out


# --------------------------------------------------------------------------
# Reading the package


def _local(el) -> str:
    return etree.QName(el).localname.lower() if isinstance(el.tag, str) else ""


def _parse_xml(data: bytes):
    parser = etree.XMLParser(recover=True, resolve_entities=False, huge_tree=True)
    root = etree.fromstring(data, parser)
    if root is None:  # not even recoverable XML: fall back to the HTML parser
        from lxml import html as lxml_html

        root = lxml_html.fromstring(data)
    return root


def _resolve(base_file: str, href: str) -> Tuple[str, str]:
    """Resolve *href* (relative to *base_file*) to (zip path, fragment)."""
    href = href.strip()
    path, _, frag = href.partition("#")
    if not path:
        return base_file, unquote(frag)
    target = posixpath.normpath(posixpath.join(posixpath.dirname(base_file), unquote(path)))
    return target, unquote(frag)


@dataclass
class _Package:
    opf_path: str
    metadata: etree._Element
    manifest: Dict[str, Tuple[str, str, str]]  # id -> (zip path, media type, properties)
    spine: List[str]  # zip paths of XHTML documents, reading order
    toc: List[Tuple[str, str, str, int]]  # (label, zip path, fragment, depth)
    cover_path: str = ""


def _read_package(z: zipfile.ZipFile) -> _Package:
    container = _parse_xml(z.read("META-INF/container.xml"))
    opf_path = container.find(".//container:rootfile", _NS).get("full-path")
    opf = _parse_xml(z.read(opf_path))
    metadata = opf.find("opf:metadata", _NS)
    manifest: Dict[str, Tuple[str, str, str]] = {}
    for item in opf.iterfind("opf:manifest/opf:item", _NS):
        path, _ = _resolve(opf_path, item.get("href", ""))
        manifest[item.get("id")] = (path, item.get("media-type", ""), item.get("properties", ""))

    spine_el = opf.find("opf:spine", _NS)
    spine = [manifest[ref.get("idref")][0] for ref in spine_el.iterfind("opf:itemref", _NS)
             if ref.get("idref") in manifest and "html" in manifest[ref.get("idref")][1]]

    toc: List[Tuple[str, str, str, int]] = []
    nav = next((v for v in manifest.values() if "nav" in v[2].split()), None)
    ncx_id = spine_el.get("toc")
    if nav and nav[0] in z.namelist():
        root = _parse_xml(z.read(nav[0]))
        for navel in root.iter("{%s}nav" % _NS["xhtml"]):
            if navel.get(_EPUB_TYPE) != "toc":
                continue
            for a in navel.iter("{%s}a" % _NS["xhtml"]):
                depth = sum(1 for anc in a.iterancestors() if _local(anc) == "ol")
                path, frag = _resolve(nav[0], a.get("href", ""))
                toc.append((" ".join("".join(a.itertext()).split()), path, frag, depth))
    elif ncx_id in manifest and manifest[ncx_id][0] in z.namelist():
        ncx_path = manifest[ncx_id][0]
        root = _parse_xml(z.read(ncx_path))
        for np in root.iter("{%s}navPoint" % _NS["ncx"]):
            label = np.find("ncx:navLabel/ncx:text", _NS)
            content = np.find("ncx:content", _NS)
            if content is None:
                continue
            depth = sum(1 for anc in np.iterancestors() if _local(anc) == "navpoint") + 1
            path, frag = _resolve(ncx_path, content.get("src", ""))
            toc.append((" ".join((label.text or "").split()) if label is not None else "", path, frag, depth))

    cover_path = ""
    cover_meta = metadata.find("opf:meta[@name='cover']", _NS) if metadata is not None else None
    if cover_meta is not None and cover_meta.get("content") in manifest:
        cover_path = manifest[cover_meta.get("content")][0]
    else:
        cover_path = next((v[0] for v in manifest.values() if "cover-image" in v[2].split()), "")
    return _Package(opf_path, metadata, manifest, spine, toc, cover_path)


# --------------------------------------------------------------------------
# Flattening XHTML into blocks


@dataclass
class _Link:
    run: int  # index into the block's runs
    target: Tuple[str, str]  # (zip path, fragment)
    own_ids: Set[str]  # ids on the <a> and its ancestors inside the block
    noteref: bool  # declared epub:type="noteref"


@dataclass
class _Block:
    doc: str
    tag: str
    runs: List[InlineRun]
    style: _Style
    size: float  # effective font size relative to the root
    ids: Set[str] = field(default_factory=set)
    links: List[_Link] = field(default_factory=list)
    image: Optional[ImageBlock] = None
    image_path: str = ""
    signature: str = ""  # tag + classes, to group a multi-paragraph note

    @property
    def text(self) -> str:
        return "".join(r.text for r in self.runs).strip()

    @property
    def blank(self) -> bool:
        return self.image is None and not self.text.replace(" ", "").strip()


class _DocReader:
    def __init__(self, z: zipfile.ZipFile, path: str, images: Dict[str, ImageBlock]):
        self.z, self.path, self.images = z, path, images
        self.root = _parse_xml(z.read(path))
        css = []
        for el in self.root.iter():
            tag = _local(el)
            if tag == "link" and "stylesheet" in (el.get("rel") or "") and el.get("href"):
                href, _ = _resolve(path, el.get("href"))
                if href in z.namelist():
                    css.append(z.read(href).decode("utf-8", "replace"))
            elif tag == "style":
                css.append(el.text or "")
        self.sheet = _Sheet("\n".join(css))
        self.blocks: List[_Block] = []
        self.title = " ".join("".join(t.itertext()) for t in self.root.iter() if _local(t) == "title").strip()

    def _style(self, el) -> _Style:
        return self.sheet.style_for(_local(el), set((el.get("class") or "").split()), el.get("style") or "")

    def _effective_size(self, el) -> float:
        size = 1.0
        for node in [el, *el.iterancestors()]:
            if _local(node) in ("html",):
                break
            size *= self._style(node).size
        return size

    def read(self) -> List[_Block]:
        body = next((el for el in self.root.iter() if _local(el) == "body"), self.root)
        self._walk(body)
        return self.blocks

    def _has_block_child(self, el) -> bool:
        return any(_local(d) in _BLOCK_LEAVES or _local(d) in _CONTAINERS for d in el.iterdescendants())

    def _walk(self, el) -> None:
        if el.text and el.text.strip():
            self._emit_loose(el.text, el)
        for child in el:
            tag = _local(child)
            if tag in _SKIP or not tag:
                pass
            elif tag in _BLOCK_LEAVES or (tag in _CONTAINERS and not self._has_block_child(child)):
                self._emit(child)
            elif tag in _CONTAINERS:
                self._walk(child)
            else:  # inline content sitting directly in a container
                self._emit(child)
            if child.tail and child.tail.strip():
                self._emit_loose(child.tail, el)

    def _emit_loose(self, text: str, parent) -> None:
        self.blocks.append(_Block(self.path, "p", [InlineRun(text)], self._style(parent),
                                  self._effective_size(parent)))

    def _emit(self, el) -> None:
        style = self._style(el)
        block = _Block(self.path, _local(el), [], style, self._effective_size(el),
                       signature=_local(el) + "." + ".".join(sorted((el.get("class") or "").split())))
        found_images: List[str] = []
        self._inline(el, block, bold=bool(style.bold), italic=bool(style.italic), sup=False,
                     ids=set(), images=found_images)
        block.runs = _tidy_runs(block.runs, block.links)
        if block.text or not found_images:
            self.blocks.append(block)
        for src in found_images:
            img = self.images.get(src)
            if img is not None:
                self.blocks.append(_Block(self.path, "img", [], style, 1.0, image=img, image_path=src))

    def _inline(self, el, block: _Block, bold: bool, italic: bool, sup: bool, ids: Set[str], images: List[str]):
        """Append *el*'s content (not its tail) to *block* as styled runs."""
        tag = _local(el)
        if el.get("id"):
            ids = ids | {el.get("id")}
            block.ids.add(el.get("id"))
        if tag in _SKIP:
            return
        if tag in ("img", "image"):
            src = el.get("src") or el.get(_XLINK_HREF) or el.get("href") or ""
            if src:
                images.append(_resolve(self.path, src)[0])
            return
        if tag == "br":
            block.runs.append(InlineRun("", br=True))
            return
        if tag not in _BLOCK_LEAVES and tag not in _CONTAINERS:
            st = self._style(el)
            bold = bold or tag in ("b", "strong") or bool(st.bold)
            italic = (italic or tag in ("i", "em", "cite")) if st.italic is None else st.italic
            sup = sup or tag == "sup" or st.sup
        if tag == "a" and el.get("href") and not re.match(r"^[a-z]+:", el.get("href")):
            # A same-book link: keep its text as one run and remember where it
            # points, so footnote pairs can be recognized once every block of
            # the book is known.
            inner = {d.get("id") for d in el.iterdescendants() if d.get("id")}
            block.ids |= inner
            block.links.append(_Link(len(block.runs), _resolve(self.path, el.get("href")), ids | inner,
                                     "noteref" in (el.get(_EPUB_TYPE) or "")))
            block.runs.append(InlineRun("".join(el.itertext()), bold=bold, italic=italic, sup=sup))
            return
        if el.text:
            block.runs.append(InlineRun(el.text, bold=bold, italic=italic, sup=sup))
        for child in el:
            self._inline(child, block, bold, italic, sup, ids, images)
            if child.tail:
                block.runs.append(InlineRun(child.tail, bold=bold, italic=italic, sup=sup))


def _tidy_runs(runs: List[InlineRun], links: List[_Link]) -> List[InlineRun]:
    """Collapse source whitespace and merge adjacent same-style runs.

    Link runs are never merged away: the indices in *links* are remapped to
    their new positions.
    """
    link_runs = {lk.run for lk in links}
    out: List[InlineRun] = []
    remap: Dict[int, int] = {}
    for i, r in enumerate(runs):
        text = _WS_RE.sub(" ", r.text)
        if r.br:
            if out and not out[-1].br:
                out[-1].text = out[-1].text.rstrip(" ")
            out.append(InlineRun("", br=True))
            continue
        if out and out[-1].br:
            text = text.lstrip(" ")
        if not text and i not in link_runs:
            continue
        prev = out[-1] if out else None
        if (prev is not None and i not in link_runs and (len(out) - 1) not in remap.values()
                and not prev.br and (prev.bold, prev.italic, prev.sup) == (r.bold, r.italic, r.sup)):
            prev.text += text
            continue
        if i in link_runs:
            remap[i] = len(out)
        out.append(InlineRun(text, bold=r.bold, italic=r.italic, sup=r.sup))
    # Trim the block's edges (and stray breaks there).
    while out and (out[0].br or not out[0].text.strip()) and 0 not in remap.values():
        out.pop(0)
        remap = {k: v - 1 for k, v in remap.items()}
    while out and (out[-1].br or not out[-1].text.strip()) and (len(out) - 1) not in remap.values():
        out.pop()
    if out:
        out[0].text = out[0].text.lstrip()
        out[-1].text = out[-1].text.rstrip()
    for lk in links:
        lk.run = remap.get(lk.run, -1)
    return out


# --------------------------------------------------------------------------
# Book-level reconstruction


def _fold(s: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFKD", s) if not unicodedata.combining(c)).lower()


def _clean_label(text: str) -> str:
    return " ".join(text.strip(_DECORATION).split()) or text.strip()


def _load_images(z: zipfile.ZipFile, pkg: _Package) -> Dict[str, ImageBlock]:
    images: Dict[str, ImageBlock] = {}
    for path, media, _ in pkg.manifest.values():
        if not media.startswith("image/") or media == "image/svg+xml" or path not in z.namelist():
            continue
        data = z.read(path)
        width = height = 0
        try:
            from PIL import Image

            with Image.open(io.BytesIO(data)) as im:
                width, height = im.size
        except Exception:
            pass
        ext = media.split("/")[1].replace("jpeg", "jpg")
        images[path] = ImageBlock(data=data, ext=ext, bbox=(0, 0, width, height), width=width, height=height)
    return images


def _link_footnotes(blocks: List[_Block]) -> Dict[int, Tuple[str, List[InlineRun]]]:
    """Find marker/note link pairs; return {note block index: (note id, runs)}.

    A marker and its note link to each other (the note's number links back to
    the marker), or the marker declares ``epub:type="noteref"``. Ids are only
    unique within a document, so everything is keyed by (document, id).
    """
    where: Dict[Tuple[str, str], int] = {}
    for i, b in enumerate(blocks):
        for ident in b.ids:
            where.setdefault((b.doc, ident), i)
    for i, b in enumerate(blocks):
        where.setdefault((b.doc, ""), i)  # a bare file href means its first block

    notes: Dict[int, Tuple[str, List[InlineRun]]] = {}
    count = 0
    for i, b in enumerate(blocks):
        if i in notes:  # a note's own number links back to its marker
            continue
        for lk in b.links:
            if lk.run < 0:
                continue
            t = where.get(lk.target)
            if t is None or t == i or t in notes:
                continue
            target = blocks[t]
            backlink = next((tl for tl in target.links
                             if tl.target[0] == b.doc and tl.target[1] in lk.own_ids), None)
            if backlink is None and not lk.noteref:
                continue
            count += 1
            nid = f"fn{count}"
            runs = [InlineRun(r.text, bold=r.bold, italic=r.italic, sup=r.sup, br=r.br)
                    for k, r in enumerate(target.runs) if backlink is None or k != backlink.run]
            if runs:
                runs[0].text = runs[0].text.lstrip(" .) ")
            # A note continued over further paragraphs of the same style.
            j = t + 1
            while (j < len(blocks) and blocks[j].doc == target.doc and blocks[j].signature == target.signature
                   and not any(tl.target[0] == b.doc for tl in blocks[j].links) and blocks[j].text):
                runs += [InlineRun("", br=True)] + blocks[j].runs
                notes[j] = ("", [])
                j += 1
            notes[t] = (nid, runs)
            marker = b.runs[lk.run]
            marker.noteref, marker.sup = nid, False
            marker.text = marker.text.strip() or "*"
    return notes


def _paragraph_classes(block: _Block, body_size: float) -> List[str]:
    st, rel, text = block.style, block.size / body_size, block.text
    classes: List[str] = []
    if st.align == "center":
        classes.append("center")
    elif st.align == "right":
        classes.append("right")
    elif rel < 0.98 and len(text) <= _VERSE_MAX_CHARS and not st.inset:
        classes.append("verse")
    elif st.inset:
        classes.append("inset")
    if st.indent is False and not classes:
        classes.append("noindent")
    if rel >= _HEADING_MIN_SIZE and "center" in classes:
        classes.append("large")
    return classes


def optimize_epub(input_path: str, output_path: str, options: Optional[OptimizeOptions] = None) -> OptimizeResult:
    """Re-typeset the EPUB at *input_path* into a Kindle-ready EPUB."""
    opts = options or OptimizeOptions()
    with zipfile.ZipFile(input_path) as z:
        _refuse_encrypted(z)
        pkg = _read_package(z)
        images = _load_images(z, pkg)
        dropped_fonts = sum(1 for _, media, _ in pkg.manifest.values()
                            if "font" in media or media.endswith(("opentype", "truetype")))
        doc_blocks: List[Tuple[str, str, List[_Block]]] = []
        for path in pkg.spine:
            if path not in z.namelist():
                continue
            reader = _DocReader(z, path, images)
            doc_blocks.append((path, reader.title, reader.read()))

    md = pkg.metadata
    def dc(name: str) -> List[etree._Element]:
        return md.findall(f"dc:{name}", _NS) if md is not None else []

    language = opts.language or next((e.text.strip() for e in dc("language") if e.text), "") or "en"
    fix = opts.fix_diacritics and language.lower().startswith("ro")
    tr = (lambda s: s.translate(_RO_DIACRITICS)) if fix else (lambda s: s)

    blocks = [b for _, _, bs in doc_blocks for b in bs]
    notes = _link_footnotes(blocks)

    # Cover: drop documents that hold nothing but the cover image.
    cover = images.get(pkg.cover_path)
    skip_docs = {path for path, _, bs in doc_blocks
                 if bs and all(b.blank or (b.image is not None and b.image_path == pkg.cover_path) for b in bs)
                 and any(b.image_path == pkg.cover_path for b in bs)}
    if cover is None:
        first = next((b for b in blocks if b.image is not None), None)
        if first is not None and first.doc in skip_docs:
            cover = first.image

    # Body text size: the font size carrying the most characters.
    weight: Dict[float, int] = {}
    for i, b in enumerate(blocks):
        if b.image is None and i not in notes:
            weight[round(b.size, 2)] = weight.get(round(b.size, 2), 0) + len(b.text)
    body_size = max(weight, key=weight.get) if weight else 1.0

    # Front matter ends where the book's own contents first points.
    index: Dict[Tuple[str, str], int] = {}
    for i, b in enumerate(blocks):
        index.setdefault((b.doc, ""), i)
        for ident in b.ids:
            index.setdefault((b.doc, ident), i)
    toc_targets: Dict[int, int] = {}
    for _label, path, frag, depth in pkg.toc:
        i = index.get((path, frag), index.get((path, "")))
        if i is not None:
            toc_targets.setdefault(i, depth)
    start = min(toc_targets) if toc_targets else 0
    while start < len(blocks) and (blocks[start].doc in skip_docs):
        start += 1

    # Headings: real <hN> tags, or short lines set clearly larger than body.
    head_size: Dict[int, float] = {}
    for i in range(start, len(blocks)):
        b = blocks[i]
        if b.image is not None or b.blank or i in notes:
            continue
        if b.tag in ("h1", "h2", "h3", "h4", "h5", "h6"):
            head_size[i] = 10.0 - int(b.tag[1])
        elif b.size / body_size >= _HEADING_MIN_SIZE and len(b.text) <= _HEADING_MAX_CHARS:
            head_size[i] = b.size / body_size
        elif i in toc_targets and len(b.text) <= _HEADING_MAX_CHARS:
            head_size[i] = 0.0  # placed below, at its contents depth
    for i, depth in toc_targets.items():
        if i in head_size and depth == 1:
            head_size[i] = max(head_size.values())
    ranks = sorted({round(s, 2) for s in head_size.values()}, reverse=True)
    level = {i: ranks.index(round(s, 2)) + 1 for i, s in head_size.items()}

    # Two levels where every top-level heading is straight away followed by a
    # second-level one are parts over chapters: the part gets a title page of
    # its own and the chapters nest under it in the contents. Otherwise the
    # top level is the chapter and anything deeper is a section within it.
    def next_heading(i: int) -> Optional[int]:
        for j in range(i + 1, min(i + 6, len(blocks))):
            if j in level:
                return j
            if not blocks[j].blank and len(blocks[j].text) > 200:
                return None
        return None

    tops = [i for i, lv in level.items() if lv == 1]
    parts = bool(tops) and len(ranks) >= 2 and all(
        (n := next_heading(i)) is not None and level[n] == 2 for i in tops)
    chapter_level = 2 if parts else 1

    title = tr(opts.title or _choose_title(pkg))
    author = tr(opts.author or next((e.text.strip() for e in dc("creator") if e.text), ""))

    chapters: List[Chapter] = []
    current: Optional[Chapter] = None
    gap = False
    images_used = 0
    in_part = False

    def element_for(i: int, b: _Block) -> Optional[Element]:
        nonlocal gap, images_used
        if b.image is not None:
            gap = False
            images_used += 1
            return Element(ElementKind.IMAGE, image=b.image)
        if b.blank:
            gap = True
            return None
        runs = [InlineRun(tr(r.text), bold=r.bold, italic=r.italic, sup=r.sup, noteref=r.noteref, br=r.br)
                for r in b.runs]
        classes = _paragraph_classes(b, body_size)
        if _SEPARATOR_RE.match(b.text):
            glyph = next(c for c in b.text if not c.isspace())
            runs, classes = [InlineRun(" ".join(glyph * 8))], ["center"]
        if gap:
            classes.append("gap")
            gap = False
        return Element(ElementKind.PARAGRAPH, runs=runs, classes=classes)

    # Front matter: one unlisted page per source document.
    front_doc = None
    for i in range(0, start):
        b = blocks[i]
        if b.doc in skip_docs or i in notes:
            continue
        if b.doc != front_doc:
            front_doc, gap = b.doc, False
            current = Chapter(title=title or "Front matter", toc_depth=-1)
            chapters.append(current)
        el = element_for(i, b)
        if el is not None:
            current.elements.append(el)

    current, gap = None, False
    for i in range(start, len(blocks)):
        b = blocks[i]
        if b.doc in skip_docs:
            continue
        if i in notes:
            continue
        if i in level:
            lv = level[i]
            heading_runs = [InlineRun(tr(" ".join(b.text.split())))]
            label = _clean_label(tr(b.text))
            if lv <= chapter_level:
                is_part = parts and lv == 1
                current = Chapter(title=label, toc_depth=1 if (in_part and not is_part) else 0)
                in_part = in_part or is_part
                chapters.append(current)
                current.elements.append(Element(ElementKind.HEADING, runs=heading_runs, level=1))
            else:
                if current is None:
                    current = Chapter(title=label)
                    chapters.append(current)
                anchor = f"s{i}"
                current.elements.append(Element(ElementKind.HEADING, runs=heading_runs, level=2, anchor=anchor))
                current.subheads.append(SubHead(anchor=anchor, title=label, level=2))
            gap = False
            continue
        if current is None:
            current = Chapter(title=title or "Text")
            chapters.append(current)
        el = element_for(i, b)
        if el is not None:
            current.elements.append(el)

    # Each note goes to the chapter holding its marker, in marker order --
    # not wherever its block sat: a note at the end of a source file belongs
    # to an earlier chapter whenever a chapter break falls inside that file.
    bodies = {nid: runs for nid, runs in notes.values() if nid}
    for ch in chapters:
        for el in ch.elements:
            for r in el.runs:
                if r.noteref in bodies:
                    ch.footnotes.append(Element(
                        ElementKind.FOOTNOTE, note_id=r.noteref, note_label=r.text,
                        runs=[InlineRun(tr(n.text), bold=n.bold, italic=n.italic, sup=n.sup, br=n.br)
                              for n in bodies.pop(r.noteref)]))

    doc = Document(chapters=chapters, title=title, author=author, language=language, cover=cover,
                   identifier=_identifier(md), metadata=_extra_metadata(md, tr))
    doc.author_sort = next((e.get("{%s}file-as" % _NS["opf"]) for e in dc("creator")
                            if e.get("{%s}file-as" % _NS["opf"])), "")
    build_epub(doc, output_path)

    warnings: List[str] = []
    if not pkg.toc:
        warnings.append("source has no table of contents; front matter could not be told apart")
    if not any(ch.toc_depth >= 0 for ch in chapters):
        warnings.append("no chapter headings recognized")
    return OptimizeResult(
        output_path=output_path, title=title, author=author,
        chapters=sum(1 for ch in chapters if ch.toc_depth >= 0),
        footnotes=sum(len(ch.footnotes) for ch in chapters),
        images=images_used, dropped_fonts=dropped_fonts, warnings=warnings,
    )


def _refuse_encrypted(z: zipfile.ZipFile) -> None:
    """Fail clearly on a DRM-encrypted book rather than emit garbled text.

    Obfuscated fonts are listed in encryption.xml too, but fonts are dropped
    anyway, so only an encrypted text or image file stops the run.
    """
    if "META-INF/encryption.xml" not in z.namelist():
        return
    root = _parse_xml(z.read("META-INF/encryption.xml"))
    for ref in root.iter():
        if _local(ref) == "cipherreference":
            uri = unquote(ref.get("URI", ""))
            if not uri.lower().endswith((".ttf", ".otf", ".woff", ".woff2")):
                raise ValueError(f"the EPUB is DRM-encrypted ({uri}); only DRM-free books can be optimized")


def _choose_title(pkg: _Package) -> str:
    """The dc:title, unless the book spells the same title better elsewhere.

    Old exports often carry an ASCII-folded dc:title ("Crima si pedeapsa")
    while the NCX or Calibre's sort title keeps the real one ("Crimă şi
    pedeapsă"); the accented spelling of the *same* words wins.
    """
    md = pkg.metadata
    title = next((e.text.strip() for e in (md.findall("dc:title", _NS) if md is not None else []) if e.text), "")
    candidates = []
    if md is not None:
        candidates += [m.get("content", "") for m in md.findall("opf:meta[@name='calibre:title_sort']", _NS)]
    for cand in candidates:
        cand = cand.strip()
        if cand and title and _fold(cand) == _fold(title) and cand != title:
            return cand
    return title


def _identifier(md) -> str:
    if md is None:
        return ""
    for e in md.findall("dc:identifier", _NS):
        text = (e.text or "").strip()
        scheme = (e.get("{%s}scheme" % _NS["opf"]) or "").lower()
        if text and (scheme == "uuid" or text.startswith("urn:uuid:")):
            return text if text.startswith("urn:") else f"urn:uuid:{text}"
    return ""


def _extra_metadata(md, tr) -> List[Tuple[str, str, Dict[str, str]]]:
    """Publisher, date, ISBN, rights, subject and contributors worth keeping."""
    if md is None:
        return []
    out: List[Tuple[str, str, Dict[str, str]]] = []
    for name in ("publisher", "rights", "subject", "description"):
        for e in md.findall(f"dc:{name}", _NS):
            if e.text and e.text.strip():
                out.append((name, tr(e.text.strip()), {}))
    date = next((e for e in md.findall("dc:date", _NS)
                 if (e.get("{%s}event" % _NS["opf"]) or "publication") == "publication" and e.text), None)
    if date is not None:
        out.append(("date", date.text.strip(), {}))
    for e in md.findall("dc:identifier", _NS):
        if (e.get("{%s}scheme" % _NS["opf"]) or "").lower() == "isbn" and e.text:
            out.append(("identifier", f"urn:isbn:{e.text.strip()}", {"id": "isbn"}))
    for k, e in enumerate(md.findall("dc:contributor", _NS)):
        role = e.get("{%s}role" % _NS["opf"]) or ""
        if e.text and role and role != "bkp":  # bkp = the tool that made the file
            out.append(("contributor", tr(e.text.strip()), {"id": f"contrib{k}", "role": role}))
    return out
