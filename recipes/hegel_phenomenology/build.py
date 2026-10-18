"""Build the annotated study edition of the Phenomenology, Part One (¶1-230).

Inputs
  src.json        written by parse_source.py from the OUP EPUB (Inwood's text)
  ann/uNN.txt     one annotation file per reading unit (see FORMAT below)
  matter/*.txt    editor's front and back matter, in the same light markup
  style.css       the stylesheet

Output
  an EPUB 3 file with Kindle pop-up notes (aside epub:type="footnote") and
  an NCX for older readers.  Chapter files are named EPUB/chap_NNN.xhtml so
  that pdf2kindle.audit can check them.

FORMAT of an annotation file.  A line beginning with "@" opens a block; the
block's body runs to the next "@" line.  Bodies are paragraphs separated by
blank lines; "- " starts a bullet; *x* is italic, **x** bold.

  @unit 12 | Short title | 111-131     header (required, first line)
  @title Display title                 heading shown at the top of the unit
  @heading 6 | Text                    a heading set above ¶6 (Hegel's own
                                       analytic contents, for the Preface)
  @guide                               "Before You Read"
  @friendly Title                      "A Friendlier Explanation"
  @c 17                                commentary on ¶17 (pop-up from "¶17")
  @g 17 | term                         gloss on the first "term" in ¶17
  @u 17 | Title                        "Pause and Unpack" box after ¶17
  @key                                 "Key Ideas" bullets
  @cross Title                         "Critical Crossroads" box
  @q                                   study questions, one per line:
                                         check: question || hint
                                         think: question
                                         push: question

Usage:  python3 -I build.py src.json OUT.epub
"""

from __future__ import annotations

import datetime
import glob
import html
import io
import json
import os
import re
import sys
import uuid
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))

BOOK_TITLE = "Phenomenology of Spirit, Part One"
BOOK_SUBTITLE = "Preface, Introduction, Consciousness, Self-Consciousness (¶1–230). An Annotated Study Edition"
AUTHOR = "G. W. F. Hegel"
TRANSLATOR = "Michael Inwood"

# --------------------------------------------------------------------------
# Inline markup
# --------------------------------------------------------------------------

def esc(s: str) -> str:
    return html.escape(s, quote=False)


def inline(s: str) -> str:
    """Escape text and turn *x* / **x** into <i> / <b>."""
    s = esc(s)
    s = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", s)
    s = re.sub(r"(?<![\w*])\*(?!\s)(.+?)(?<!\s)\*(?![\w*])", r"<i>\1</i>", s)
    return s


def blocks(body: str, pclass: str = "") -> str:
    """Render a body of paragraphs and bullet lists."""
    out = []
    cls = f' class="{pclass}"' if pclass else ""
    paras = re.split(r"\n\s*\n", body.strip())
    for para in paras:
        lines = [l.rstrip() for l in para.strip().split("\n") if l.strip()]
        if not lines:
            continue
        if all(l.lstrip().startswith("- ") for l in lines):
            items = "".join(f"<li>{inline(l.lstrip()[2:])}</li>" for l in lines)
            out.append(f"<ul>{items}</ul>")
        elif lines[0].startswith("> "):
            text = " ".join(l[2:] if l.startswith("> ") else l for l in lines)
            out.append(f'<blockquote><p>{inline(text)}</p></blockquote>')
        else:
            out.append(f"<p{cls}>{inline(' '.join(lines))}</p>")
    return "\n".join(out)


def plain(s: str) -> str:
    return re.sub(r"\*+", "", s)


# --------------------------------------------------------------------------
# Annotation files
# --------------------------------------------------------------------------

class Unit:
    def __init__(self, path: str):
        self.path = path
        self.id = ""
        self.short = ""
        self.first = self.last = 0
        self.title = ""
        self.headings: dict[int, list[str]] = {}
        self.guide = ""
        self.friendly: tuple[str, str] | None = None
        self.comm: dict[int, str] = {}
        self.gloss: dict[int, list[tuple[str, str]]] = {}
        self.unpack: dict[int, list[tuple[str, str]]] = {}
        self.key = ""
        self.cross: list[tuple[str, str]] = []
        self.questions: list[tuple[str, str, str]] = []
        self._parse()

    def _parse(self) -> None:
        text = open(self.path, encoding="utf-8").read()
        chunks = re.split(r"(?m)^@", text)
        for chunk in chunks[1:]:
            head, _, body = chunk.partition("\n")
            kind, _, arg = head.strip().partition(" ")
            arg = arg.strip()
            body = body.strip()
            if kind == "unit":
                uid, short, rng = [x.strip() for x in arg.split("|")]
                self.id, self.short = uid, short
                a, b = re.split(r"[-–]", rng)
                self.first, self.last = int(a), int(b)
            elif kind == "title":
                self.title = arg
            elif kind == "heading":
                n, _, t = arg.partition("|")
                self.headings.setdefault(int(n), []).append(t.strip())
            elif kind == "guide":
                self.guide = body
            elif kind == "friendly":
                self.friendly = (arg, body)
            elif kind == "c":
                self.comm[int(arg)] = body
            elif kind == "g":
                n, _, term = arg.partition("|")
                self.gloss.setdefault(int(n), []).append((term.strip(), body))
            elif kind == "u":
                n, _, t = arg.partition("|")
                self.unpack.setdefault(int(n), []).append((t.strip(), body))
            elif kind == "key":
                self.key = body
            elif kind == "cross":
                self.cross.append((arg, body))
            elif kind == "q":
                for line in body.split("\n"):
                    line = line.strip()
                    if not line:
                        continue
                    tier, _, rest = line.partition(":")
                    q, _, hint = rest.partition("||")
                    self.questions.append((tier.strip(), q.strip(), hint.strip()))
            else:
                raise SystemExit(f"{self.path}: unknown block @{kind}")
        if not self.id:
            raise SystemExit(f"{self.path}: missing @unit line")


# --------------------------------------------------------------------------
# XHTML pages
# --------------------------------------------------------------------------

def page(title: str, body: str, extra_class: str = "") -> str:
    cls = f' class="{extra_class}"' if extra_class else ""
    return (
        '<?xml version="1.0" encoding="utf-8"?>\n'
        '<!DOCTYPE html>\n'
        '<html xmlns="http://www.w3.org/1999/xhtml" '
        'xmlns:epub="http://www.idpf.org/2007/ops" lang="en" xml:lang="en">\n'
        f"<head>\n<meta charset=\"utf-8\"/>\n<title>{esc(title)}</title>\n"
        '<link rel="stylesheet" type="text/css" href="style.css"/>\n'
        f"</head>\n<body{cls}>\n{body}\n</body>\n</html>\n"
    )


class Notes:
    """Collects the pop-up notes of one chapter file."""

    def __init__(self, prefix: str):
        self.prefix = prefix
        self.items: list[tuple[str, str]] = []
        self.count = 0

    def add(self, body_html: str) -> str:
        self.count += 1
        nid = f"{self.prefix}n{self.count}"
        self.items.append((nid, body_html))
        return nid

    def render(self) -> str:
        if not self.items:
            return ""
        out = ['<section class="notes" epub:type="footnotes">',
               '<p class="dinkus">· · ·</p>',
               '<h2 class="notes-head">Notes to this section</h2>']
        for nid, body in self.items:
            out.append(
                f'<aside class="note" epub:type="footnote" id="{nid}">'
                f'<div class="note-body">{body}'
                f' <a class="back" href="#{nid}r">↩</a></div></aside>')
        out.append("</section>")
        return "\n".join(out)


def ref(nid: str, label: str, cls: str) -> str:
    return (f'<a class="{cls}" epub:type="noteref" id="{nid}r" '
            f'href="#{nid}">{label}</a>')


GLOSS_LETTERS = "abcdefghijklmnopqrstuvwxyz"


def gloss_label(i: int) -> str:
    if i < 26:
        return GLOSS_LETTERS[i]
    return GLOSS_LETTERS[i // 26 - 1] + GLOSS_LETTERS[i % 26]


def attach_gloss(html_text: str, term: str, marker: str) -> tuple[str, bool]:
    """Put marker right after the first occurrence of term in text nodes."""
    pieces = re.split(r"(<[^>]+>|\{\{n:[^}]+\}\})", html_text)
    # Search across the concatenated text, ignoring tags, by matching inside
    # single text pieces first (the common case).
    pat = re.compile(re.escape(esc(term)) + r"(?![\w])", re.I)
    for i, piece in enumerate(pieces):
        if piece.startswith("<") or piece.startswith("{{"):
            continue
        m = pat.search(piece)
        if m:
            pieces[i] = piece[: m.end()] + marker + piece[m.end():]
            return "".join(pieces), True
    # Term split by markup (e.g. partly italic): match on the stripped text,
    # then place the marker after the tag that closes the match.
    flat = ""
    owners = []
    for i, piece in enumerate(pieces):
        if piece.startswith("<") or piece.startswith("{{"):
            continue
        for ch in piece:
            flat += ch
            owners.append(i)
    m = pat.search(flat)
    if m:
        last_piece = owners[m.end() - 1]
        j = last_piece + 1
        while j < len(pieces) and pieces[j].startswith("</"):
            j += 1
        pieces.insert(j, marker)
        return "".join(pieces), True
    return html_text, False


class Builder:
    def __init__(self, src_path: str):
        self.src = json.load(open(src_path, encoding="utf-8"))
        self.paras = {p["n"]: p for p in self.src["paras"]}
        self.inwood = self.src["notes"]
        self.files: list[tuple[str, str, str]] = []   # (name, title, xhtml)
        self.toc: list = []                            # nested nav entries
        self.counter = 0
        self.gloss_index = 0
        self.warnings: list[str] = []
        self.stats = {"commentary": 0, "gloss": 0, "unpack": 0, "inwood": 0,
                      "questions": 0, "hints": 0, "cross": 0}

    # -- files ---------------------------------------------------------------
    def new_file(self, title: str) -> str:
        name = f"chap_{self.counter:03d}.xhtml"
        self.counter += 1
        return name

    def add(self, name: str, title: str, xhtml: str) -> None:
        self.files.append((name, title, xhtml))

    # -- matter (front/back) -------------------------------------------------
    def matter(self, path: str, toc_parent: list | None = None) -> None:
        """Render a matter file.  '# ' starts a new chapter file."""
        text = open(path, encoding="utf-8").read()
        sections = re.split(r"(?m)^# ", text)
        for sec in sections[1:]:
            title, _, body = sec.partition("\n")
            title = title.strip()
            name = self.new_file(title)
            notes = Notes("m%03d" % self.counter)
            content, subs = self.render_matter(body, name, notes)
            xhtml = page(plain(title),
                         f'<section epub:type="chapter" class="matter">\n'
                         f'<h1 class="matter-title">{inline(title)}</h1>\n'
                         f"{content}\n{notes.render()}\n</section>")
            self.add(name, plain(title), xhtml)
            entry = (plain(title), name, subs)
            (toc_parent if toc_parent is not None else self.toc).append(entry)

    def render_matter(self, body: str, fname: str, notes: Notes):
        out = []
        subs = []
        lines = body.split("\n")
        i = 0
        buf: list[str] = []

        def flush():
            if buf:
                out.append(blocks("\n".join(buf)))
                buf.clear()

        while i < len(lines):
            line = lines[i]
            if line.startswith("## "):
                flush()
                t = line[3:].strip()
                aid = f"s{len(subs) + 1}"
                subs.append((plain(t), f"{fname}#{aid}", []))
                out.append(f'<h2 id="{aid}">{inline(t)}</h2>')
            elif line.startswith("### "):
                flush()
                out.append(f"<h3>{inline(line[4:].strip())}</h3>")
            elif line.startswith("@box"):
                flush()
                kind, _, title = line[4:].strip().partition("|")
                kind = kind.strip() or "plain"
                j = i + 1
                inner = []
                while j < len(lines) and not lines[j].startswith("@end"):
                    inner.append(lines[j])
                    j += 1
                label = f'<p class="box-label">{inline(title.strip())}</p>' if title.strip() else ""
                out.append(f'<div class="box box-{kind}">{label}'
                           f'{blocks(chr(10).join(inner))}</div>')
                i = j
            elif line.startswith("|"):
                flush()
                rows = []
                j = i
                while j < len(lines) and lines[j].startswith("|"):
                    cells = [c.strip() for c in lines[j].strip().strip("|").split("|")]
                    if not all(re.fullmatch(r":?-+:?", c) for c in cells):
                        rows.append(cells)
                    j += 1
                head = "".join(f"<th>{inline(c)}</th>" for c in rows[0])
                body_rows = "".join(
                    "<tr>" + "".join(f"<td>{inline(c)}</td>" for c in r) + "</tr>"
                    for r in rows[1:])
                out.append(f'<table class="grid"><thead><tr>{head}</tr></thead>'
                           f"<tbody>{body_rows}</tbody></table>")
                i = j - 1
            elif line.startswith("@raw"):
                flush()
                j = i + 1
                inner = []
                while j < len(lines) and not lines[j].startswith("@end"):
                    inner.append(lines[j])
                    j += 1
                out.append("\n".join(inner))
                i = j
            else:
                buf.append(line)
            i += 1
        flush()
        return "\n".join(out), subs

    # -- Hegel units ---------------------------------------------------------
    def hegel_paragraph(self, n: int, unit: Unit, notes: Notes) -> str:
        p = self.paras[n]
        body = p["html"]
        more = list(p["more"])
        # Glosses first (they need the raw text), then Inwood's anchors.
        glosses = unit.gloss.get(n, [])
        for term, gtext in glosses:
            label = gloss_label(self.gloss_index)
            self.gloss_index += 1
            nid = notes.add(f'<p><span class="nlabel gloss-l">{esc(label)}</span> '
                            f'<b>{inline(term)}</b>. {inline_par(gtext)}</p>')
            marker = f'<sup class="gl">{ref(nid, label, "gref")}</sup>'
            body, ok = attach_gloss(body, term, marker)
            for k, blk in enumerate(more):
                if ok:
                    break
                more[k], ok = attach_gloss(blk, term, marker)
            if not ok:
                self.warnings.append(f"unit {unit.id}: gloss term not found in ¶{n}: {term!r}")
                body += f' <span class="gl-miss">[{esc(term)}]</span>{marker}'
            self.stats["gloss"] += 1

        def inwood_sub(m):
            nid_src = m.group(1)
            note = self.inwood[nid_src]
            num = note["num"] or "*"
            nid = notes.add(f'<p><span class="nlabel inwood-l">Inwood</span> '
                            f'<b>¶{note["para"]}, n.&#160;{esc(num)}.</b> {note["html"]}</p>')
            self.stats["inwood"] += 1
            return f'<sup class="iw">{ref(nid, esc(num), "iref")}</sup>'

        body = re.sub(r"\{\{n:([^}]+)\}\}", inwood_sub, body)
        more = [re.sub(r"\{\{n:([^}]+)\}\}", inwood_sub, blk) for blk in more]

        comm = unit.comm.get(n)
        if comm:
            nid = notes.add(f'<p class="comm-head"><span class="nlabel comm-l">Commentary</span> '
                            f'<b>on ¶{n}</b></p>{blocks(comm)}')
            num = ref(nid, f"¶{n}", "pref")
            self.stats["commentary"] += 1
        else:
            num = f"¶{n}"
            self.warnings.append(f"unit {unit.id}: no commentary for ¶{n}")
        out = [f'<p class="hegel" id="p{n}"><span class="pnum">{num}</span> {body}</p>']
        out += [f'<p class="hegel cont">{blk}</p>' for blk in more]
        for title, ub in unit.unpack.get(n, []):
            out.append(
                '<div class="box box-unpack">'
                f'<p class="box-label">Pause and Unpack</p>'
                f'<p class="box-title">{inline(title)}</p>{blocks(ub)}</div>')
            self.stats["unpack"] += 1
        return "\n".join(out)

    def unit_file(self, unit: Unit) -> tuple[str, str]:
        name = self.new_file(unit.title)
        notes = Notes(f"u{unit.id}")
        self.gloss_index = 0
        first = self.paras[unit.first]
        crumbs = " · ".join(x for x in (first["part"], first["chapter"]) if x)
        words = sum(len(self.paras[n]["text"].split())
                    for n in range(unit.first, unit.last + 1))
        minutes = max(5, round(words / 120 / 5) * 5)   # slow, careful reading
        parts = ['<section epub:type="chapter" class="unit">',
                 f'<p class="crumbs">{esc(crumbs)}</p>',
                 f'<h1 class="unit-title">{inline(unit.title)}</h1>',
                 f'<p class="unit-range">Paragraphs ¶{unit.first}–{unit.last} · '
                 f'about {words:,} words of Hegel · allow {minutes} minutes</p>']
        if unit.guide:
            parts.append('<div class="box box-guide"><p class="box-label">Before You Read</p>'
                         f"{blocks(unit.guide)}</div>")
        if unit.friendly:
            t, b = unit.friendly
            parts.append('<div class="box box-friendly"><p class="box-label">A Friendlier Explanation</p>'
                         f'<p class="box-title">{inline(t)}</p>{blocks(b)}</div>')
        parts.append('<div class="text-start" id="text"></div>')
        prev_section = None
        prev_chapter = None
        for n in range(unit.first, unit.last + 1):
            p = self.paras[n]
            if p["chapter"] and p["chapter"] != prev_chapter and \
                    (n == unit.first or p["chapter"] != self.paras[n - 1]["chapter"]):
                parts.append(f'<h2 class="hegel-head">{esc(p["chapter"])}</h2>')
            if p["section"] and (n == unit.first or p["section"] != self.paras[n - 1]["section"]):
                parts.append(f'<h3 class="hegel-sub">{esc(p["section"])}</h3>')
            prev_chapter, prev_section = p["chapter"], p["section"]
            for h in unit.headings.get(n, []):
                parts.append(f'<p class="analytic-head"><span class="ah-label">Hegel’s contents</span> {inline(h)}</p>')
            parts.append(self.hegel_paragraph(n, unit, notes))
        if unit.key:
            parts.append('<div class="box box-key"><p class="box-label">Key Ideas</p>'
                         f"{blocks(unit.key)}</div>")
        for t, b in unit.cross:
            parts.append('<div class="box box-cross"><p class="box-label">Critical Crossroads</p>'
                         f'<p class="box-title">{inline(t)}</p>{blocks(b)}</div>')
            self.stats["cross"] += 1
        if unit.questions:
            tiers = {"check": "Check your understanding",
                     "think": "Think it through",
                     "push": "Push back"}
            qparts = ['<div class="box box-questions"><p class="box-label">Questions for Study</p>']
            for tier in ("check", "think", "push"):
                qs = [q for q in unit.questions if q[0] == tier]
                if not qs:
                    continue
                qparts.append(f'<p class="q-tier">{tiers[tier]}</p><ol class="q">')
                for _, q, hint in qs:
                    h = ""
                    if hint:
                        nid = notes.add(f'<p><span class="nlabel hint-l">Hint</span> {inline(hint)}</p>')
                        h = f' <span class="hint">{ref(nid, "hint", "href")}</span>'
                        self.stats["hints"] += 1
                    qparts.append(f"<li>{inline(q)}{h}</li>")
                    self.stats["questions"] += 1
                qparts.append("</ol>")
            qparts.append("</div>")
            parts.append("".join(qparts))
        for n in unit.comm:
            if not unit.first <= n <= unit.last:
                self.warnings.append(f"unit {unit.id}: commentary for ¶{n} outside range")
        parts.append(notes.render())
        parts.append("</section>")
        title = plain(unit.title)
        self.add(name, title, page(title, "\n".join(parts)))
        return name, title


def inline_par(s: str) -> str:
    """Inline markup for a short multi-paragraph note (paragraphs joined)."""
    paras = [p.strip() for p in re.split(r"\n\s*\n", s.strip()) if p.strip()]
    return "</p><p>".join(inline(" ".join(p.split("\n"))) for p in paras)


# --------------------------------------------------------------------------
# Packaging
# --------------------------------------------------------------------------

def nav_xhtml(toc) -> str:
    def li(entries):
        out = ["<ol>"]
        for title, href, kids in entries:
            out.append(f'<li><a href="{href}">{esc(title)}</a>')
            if kids:
                out.append(li(kids))
            out.append("</li>")
        out.append("</ol>")
        return "\n".join(out)
    body = ('<nav epub:type="toc" id="toc"><h1>Contents</h1>\n' + li(toc) + "\n</nav>\n"
            '<nav epub:type="landmarks" hidden=""><ol>'
            '<li><a epub:type="cover" href="cover.xhtml">Cover</a></li>'
            '<li><a epub:type="toc" href="nav.xhtml">Contents</a></li>'
            '<li><a epub:type="bodymatter" href="chap_000.xhtml">Begin</a></li>'
            "</ol></nav>")
    return page("Contents", body)


def ncx(toc, uid: str) -> str:
    counter = [0]

    def points(entries):
        out = []
        for title, href, kids in entries:
            counter[0] += 1
            n = counter[0]
            out.append(f'<navPoint id="np{n}" playOrder="{n}"><navLabel><text>{esc(title)}</text>'
                       f'</navLabel><content src="{href}"/>')
            out.append(points(kids))
            out.append("</navPoint>")
        return "".join(out)
    pts = points(toc)
    return ('<?xml version="1.0" encoding="utf-8"?>\n'
            '<ncx xmlns="http://www.daisy.org/z3986/2005/ncx/" version="2005-1">'
            f'<head><meta name="dtb:uid" content="{uid}"/><meta name="dtb:depth" content="3"/>'
            '<meta name="dtb:totalPageCount" content="0"/><meta name="dtb:maxPageNumber" content="0"/></head>'
            f"<docTitle><text>{esc(BOOK_TITLE)}</text></docTitle><navMap>{pts}</navMap></ncx>")


def opf(files, uid: str) -> str:
    now = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    manifest = [
        '<item id="nav" href="nav.xhtml" media-type="application/xhtml+xml" properties="nav"/>',
        '<item id="ncx" href="toc.ncx" media-type="application/x-dtbncx+xml"/>',
        '<item id="css" href="style.css" media-type="text/css"/>',
        '<item id="cover-img" href="cover.jpg" media-type="image/jpeg" properties="cover-image"/>',
        '<item id="cover" href="cover.xhtml" media-type="application/xhtml+xml"/>',
    ]
    spine = ['<itemref idref="cover" linear="yes"/>', '<itemref idref="nav" linear="yes"/>']
    for name, _, _ in files:
        iid = name.replace(".xhtml", "")
        manifest.append(f'<item id="{iid}" href="{name}" media-type="application/xhtml+xml"/>')
        spine.append(f'<itemref idref="{iid}"/>')
    return ('<?xml version="1.0" encoding="utf-8"?>\n'
            '<package xmlns="http://www.idpf.org/2007/opf" version="3.0" unique-identifier="uid" xml:lang="en">\n'
            '<metadata xmlns:dc="http://purl.org/dc/elements/1.1/">\n'
            f'<dc:identifier id="uid">urn:uuid:{uid}</dc:identifier>\n'
            f"<dc:title>{esc(BOOK_TITLE)}</dc:title>\n"
            f'<dc:creator id="c1">{esc(AUTHOR)}</dc:creator>\n'
            f'<dc:contributor id="c2">{esc(TRANSLATOR)}</dc:contributor>\n'
            '<meta refines="#c2" property="role" scheme="marc:relators">trl</meta>\n'
            "<dc:language>en</dc:language>\n"
            f"<dc:description>{esc(BOOK_SUBTITLE)}</dc:description>\n"
            f'<meta property="dcterms:modified">{now}</meta>\n'
            '<meta name="cover" content="cover-img"/>\n'
            "</metadata>\n<manifest>\n" + "\n".join(manifest) + "\n</manifest>\n"
            '<spine toc="ncx">\n' + "\n".join(spine) + "\n</spine>\n</package>\n")


def cover_xhtml() -> str:
    return page("Cover", '<div class="cover"><img src="cover.jpg" alt="Cover"/></div>', "cover-page")


def write_epub(out_path: str, files, toc, css: str, cover: bytes) -> None:
    uid = str(uuid.uuid5(uuid.NAMESPACE_URL, "hegel-phenomenology-part-one-study-edition"))
    with zipfile.ZipFile(out_path, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr(zipfile.ZipInfo("mimetype"), "application/epub+zip", zipfile.ZIP_STORED)
        z.writestr("META-INF/container.xml",
                   '<?xml version="1.0" encoding="utf-8"?>\n'
                   '<container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">'
                   '<rootfiles><rootfile full-path="EPUB/content.opf" '
                   'media-type="application/oebps-package+xml"/></rootfiles></container>')
        z.writestr("EPUB/content.opf", opf(files, uid))
        z.writestr("EPUB/nav.xhtml", nav_xhtml(toc))
        z.writestr("EPUB/toc.ncx", ncx(toc, uid))
        z.writestr("EPUB/style.css", css)
        z.writestr("EPUB/cover.jpg", cover)
        z.writestr("EPUB/cover.xhtml", cover_xhtml())
        for name, _, xhtml in files:
            z.writestr(f"EPUB/{name}", xhtml)


# --------------------------------------------------------------------------
# Inwood's own front matter, reproduced at the back
# --------------------------------------------------------------------------

def original_matter(b: Builder, parent: list) -> None:
    front = b.src["front"]
    for key, title in (("introduction", "Translator’s Introduction (Michael Inwood)"),
                       ("translation", "Note on the Translation and Commentary"),
                       ("abbreviations", "Abbreviations Used in Inwood’s Notes"),
                       ("glossary", "Inwood’s German–English Glossary")):
        name = b.new_file(title)
        frag = front[key]
        frag = re.sub(r"<h2>.*?</h2>", "", frag, count=1, flags=re.S)
        xhtml = page(title, f'<section epub:type="chapter" class="matter original">'
                            f'<h1 class="matter-title">{esc(title)}</h1>\n{frag}\n</section>')
        b.add(name, title, xhtml)
        parent.append((title, name, []))


# --------------------------------------------------------------------------

def main(src_path: str, out_path: str) -> None:
    b = Builder(src_path)
    units = [Unit(p) for p in sorted(glob.glob(os.path.join(HERE, "ann", "u*.txt")))]
    units.sort(key=lambda u: u.first)

    # Front matter
    b.matter(os.path.join(HERE, "matter", "01_front.txt"))
    intro_parent: list = []
    b.toc.append(("Editor’s Introduction: Reading the Phenomenology", None, intro_parent))
    b.matter(os.path.join(HERE, "matter", "02_introduction.txt"), intro_parent)
    b.toc[-1] = (b.toc[-1][0], intro_parent[0][1], intro_parent)
    b.matter(os.path.join(HERE, "matter", "03_tools.txt"))

    # Hegel's text, grouped as in the original
    groups = [("Preface: On Scientific Cognition", 1, 72),
              ("Introduction", 73, 89),
              ("A. Consciousness", 90, 165),
              ("B. Self-Consciousness", 166, 230)]
    text_parent: list = []
    for gtitle, a, z in groups:
        kids = []
        for u in units:
            if a <= u.first <= z:
                name, title = b.unit_file(u)
                kids.append((title, name, []))
        if kids:
            text_parent.append((gtitle, kids[0][1], kids))
    if text_parent:
        b.toc.append(("The Phenomenology of Spirit, ¶1–230", text_parent[0][1], text_parent))

    # Back matter
    b.matter(os.path.join(HERE, "matter", "04_back.txt"))
    orig: list = []
    original_matter(b, orig)
    b.toc.append(("From the Original Edition", orig[0][1], orig))

    covered = sorted(n for u in units for n in range(u.first, u.last + 1))
    if covered and covered != list(range(covered[0], covered[-1] + 1)):
        b.warnings.append("units overlap or leave gaps")

    css = open(os.path.join(HERE, "style.css"), encoding="utf-8").read()
    cover_path = os.path.join(HERE, "cover.jpg")
    if not os.path.exists(cover_path):
        import make_cover  # noqa: F401  (writes cover.jpg)
        make_cover.main(cover_path)
    cover = open(cover_path, "rb").read()
    write_epub(out_path, b.files, b.toc, css, cover)
    for w in b.warnings:
        print("WARN", w)
    print(json.dumps(b.stats))
    print(f"wrote {out_path}: {len(b.files)} files, units {len(units)}")


if __name__ == "__main__":
    sys.path.insert(0, HERE)
    main(sys.argv[1], sys.argv[2])
