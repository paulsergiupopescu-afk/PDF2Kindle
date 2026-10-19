"""Build the annotated study edition of the Phenomenology, Volume One (¶1-131).

This is the "v2" study template: every aid belongs to one of four layers
(Orient, Read, Argue, Consolidate) and is labelled as such, so a reader can
choose how deep to go.  Hegel's text (Inwood's translation) is never altered.

Inputs
  src.json        written by parse_source.py from the OUP EPUB
  ann/uNN.txt     one annotation file per reading unit (FORMAT below)
  matter/*.txt    editor's front and back matter (MATTER below)
  style.css       the stylesheet; cover.jpg (made by make_cover.py if absent)

Output
  an EPUB 3 file with Kindle pop-up notes (aside epub:type="footnote") and an
  NCX for older readers.  Chapter files are named EPUB/chap_NNN.xhtml so that
  pdf2kindle.audit can check them.

FORMAT of an annotation file.  A line beginning with "@" opens a block whose
body runs to the next "@" line.  Bodies are paragraphs separated by blank
lines; "- " starts a bullet; *x* is italic, **x** bold.  Any "¶N" (N <= LAST_PARA)
in the editor's prose becomes a link to that paragraph.  In labelled bodies a
paragraph may begin with "label:" (e.g. "gist:"), which becomes a run-in head;
unlabelled paragraphs continue the previous label.

  @unit 12 | Tracker name | 111-131    header (required)
  @title Display title
  @heading 6 | Text                    Hegel's own analytic heading above ¶6
  ORIENT
  @where                               "Where We Are" (one paragraph)
  @guide                               "Before You Read"
  @shape                               "The Shape at a Glance", lines
                                         certain: / standard: / test: /
                                         learns: / behind: / next:
  @map                                 "Argument Map", lines thesis: /
                                         target: / step: (repeatable) /
                                         conclusion: / stakes:
  @friendly Title                      "A Friendlier Explanation"
  READ
  @c 17 | move | voice                 commentary on ¶17; paragraphs
                                         labelled gist: and why:
  @g 17 | term                         gloss on the first "term" in ¶17
  @l 17 | phrase                       line note on the first "phrase" in ¶17
                                         (lettered together with the glosses,
                                         in reading order)
  @note 17                             "Reading note", printed visibly right
                                         after ¶17
  @u 17 | Title                        "Pause and Unpack" after ¶17; labels
                                         step: example: misreading: returns:
  @arg 17 | Title                      "Argument Reconstruction" after ¶17;
                                         lines P1: P2: ... C: weak:
  @lens 17 | Title                     "Translation Lens" after ¶17
  ARGUE
  @cross Title                         "Critical Crossroads"
  @obj Title                           "Objections and Replies"; labels
                                         objection: reply: verdict:
  @now Title                           "Then and Now"
  CONSOLIDATE
  @key                                 "Key Ideas"
  @q                                   one question per line, tiers
                                         check: question || model answer
                                         think: / push: / write:
  @retell                              model five-sentence retelling
  @carry                               "Carry Forward" bullets

MATTER files: "# " starts a chapter file, "## " a section (listed in the
contents), "### " a sub-head; "@box kind | Title" ... "@end" draws a box;
lines starting "|" make a table; "@tracker" inserts the Dialectic Tracker
built from all @shape cards.

Usage:  python3 -I build.py src.json OUT.epub
"""

from __future__ import annotations

import datetime
import glob
import html
import json
import os
import re
import sys
import uuid
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))

BOOK_TITLE = "Phenomenology of Spirit, Volume One"
BOOK_SUBTITLE = ("Preface, Introduction, Sensory Certainty, Perception (¶1–131). "
                 "An Annotated Study Edition")
AUTHOR = "G. W. F. Hegel"
TRANSLATOR = "Michael Inwood"
LAST_PARA = 131          # last paragraph of this volume (end of Perception)

# --------------------------------------------------------------------------
# Inline markup
# --------------------------------------------------------------------------

XREF = re.compile(r"(¶¶?)(\d+)((?:\s?[–-]\s?\d+)?)")


def esc(s: str) -> str:
    return html.escape(s, quote=False)


def link_paras(s: str) -> str:
    """Turn ¶N references in escaped prose into placeholder links."""
    def sub(m):
        n = int(m.group(2))
        if 1 <= n <= LAST_PARA:
            return f'<a class="xref" href="@P{n}@">{m.group(0)}</a>'
        return m.group(0)
    return XREF.sub(sub, s)


def inline(s: str, links: bool = True) -> str:
    """Escape text and turn *x* / **x** into <i> / <b>; link ¶N."""
    s = esc(s)
    s = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", s)
    s = re.sub(r"(?<![\w*])\*(?!\s)(.+?)(?<!\s)\*(?![\w*])", r"<i>\1</i>", s)
    return link_paras(s) if links else s


def blocks(body: str, pclass: str = "") -> str:
    """Render a body of paragraphs, bullet lists and quotations."""
    out = []
    cls = f' class="{pclass}"' if pclass else ""
    for para in re.split(r"\n\s*\n", body.strip()):
        lines = [l.rstrip() for l in para.strip().split("\n") if l.strip()]
        if not lines:
            continue
        if all(l.lstrip().startswith("- ") for l in lines):
            items = "".join(f"<li>{inline(l.lstrip()[2:])}</li>" for l in lines)
            out.append(f"<ul>{items}</ul>")
        elif all(re.match(r"\d+\.\s", l.lstrip()) for l in lines):
            items = "".join(f"<li>{inline(re.sub(r'^\d+\.\s', '', l.lstrip()))}</li>"
                            for l in lines)
            out.append(f"<ol>{items}</ol>")
        elif lines[0].startswith("> "):
            text = " ".join(l[2:] if l.startswith("> ") else l for l in lines)
            out.append(f"<blockquote><p>{inline(text)}</p></blockquote>")
        else:
            out.append(f"<p{cls}>{inline(' '.join(lines))}</p>")
    return "\n".join(out)


def labelled(body: str, labels: dict[str, str]) -> str:
    """Render paragraphs that may open with 'key:' as run-in heads."""
    out = []
    for para in re.split(r"\n\s*\n", body.strip()):
        text = " ".join(l.strip() for l in para.strip().split("\n") if l.strip())
        if not text:
            continue
        m = re.match(r"([a-z]+):\s*(.*)", text, re.S)
        if m and m.group(1) in labels:
            out.append(f'<p><span class="runin">{labels[m.group(1)]}</span> '
                       f"{inline(m.group(2))}</p>")
        elif text.startswith("- "):
            out.append(blocks(para))
        else:
            out.append(f"<p>{inline(text)}</p>")
    return "\n".join(out)


def keyed_lines(body: str) -> list[tuple[str, str]]:
    """Parse 'key: value' lines; continuation lines join the previous value."""
    items: list[list[str]] = []
    for line in body.split("\n"):
        line = line.strip()
        if not line:
            continue
        m = re.match(r"([A-Za-z][A-Za-z0-9]*):\s*(.*)", line)
        if m:
            items.append([m.group(1), m.group(2)])
        elif items:
            items[-1][1] += " " + line
    return [(k, v) for k, v in items]


def plain(s: str) -> str:
    return re.sub(r"\*+", "", s)


def layer_label(layer: str, name: str) -> str:
    return (f'<p class="box-label"><span class="layer">{layer}</span>'
            f'<span class="layer-sep"> · </span>{name}</p>')


COMM_LABELS = {"gist": "Gist.", "why": "Why it matters."}
UNPACK_LABELS = {"step": "Step by step.", "example": "In everyday terms.",
                 "misreading": "Misreading to avoid.", "returns": "Where it returns."}
OBJ_LABELS = {"objection": "Objection.", "reply": "Reply.", "verdict": "Your verdict."}
SHAPE_LABELS = [("certain", "It is certain that"),
                ("standard", "Its standard of truth"),
                ("test", "The test"),
                ("learns", "What it learns (for it)"),
                ("behind", "What we see behind its back (for us)"),
                ("next", "What comes next")]
MAP_LABELS = {"thesis": "Thesis", "target": "Target", "conclusion": "Conclusion",
              "stakes": "What is at stake"}
VOICES = {"hegel": "Hegel in his own voice",
          "c": "consciousness itself (“for it”)",
          "we": "the observing “we” (“for us”)",
          "mixed": "consciousness and the “we” in turn",
          "sc": "self-consciousness itself (“for it”)"}


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
        self.where = ""
        self.guide = ""
        self.shape: list[tuple[str, str]] = []
        self.map: list[tuple[str, str]] = []
        self.friendly: tuple[str, str] | None = None
        self.comm: dict[int, tuple[str, str, str]] = {}
        self.gloss: dict[int, list[tuple[str, str, str]]] = {}  # kind, term, body
        self.reading: dict[int, str] = {}
        self.after: dict[int, list[tuple[str, str, str]]] = {}   # kind, title, body
        self.cross: list[tuple[str, str]] = []
        self.obj: list[tuple[str, str]] = []
        self.now: list[tuple[str, str]] = []
        self.key = ""
        self.questions: list[tuple[str, str, str]] = []
        self.retell = ""
        self.carry = ""
        self._parse()

    def _parse(self) -> None:
        text = open(self.path, encoding="utf-8").read()
        for chunk in re.split(r"(?m)^@", text)[1:]:
            head, _, body = chunk.partition("\n")
            kind, _, arg = head.strip().partition(" ")
            arg, body = arg.strip(), body.strip()
            parts = [x.strip() for x in arg.split("|")]
            if kind == "unit":
                self.id, self.short = parts[0], parts[1]
                a, b = re.split(r"[-–]", parts[2])
                self.first, self.last = int(a), int(b)
            elif kind == "title":
                self.title = arg
            elif kind == "heading":
                self.headings.setdefault(int(parts[0]), []).append(parts[1])
            elif kind == "where":
                self.where = body
            elif kind == "guide":
                self.guide = body
            elif kind == "shape":
                self.shape = keyed_lines(body)
            elif kind == "map":
                self.map = keyed_lines(body)
            elif kind == "friendly":
                self.friendly = (arg, body)
            elif kind == "c":
                move = parts[1] if len(parts) > 1 else ""
                voice = parts[2] if len(parts) > 2 else ""
                self.comm[int(parts[0])] = (move, voice, body)
            elif kind in ("g", "l"):
                self.gloss.setdefault(int(parts[0]), []).append((kind, parts[1], body))
            elif kind == "note":
                self.reading[int(parts[0])] = body
            elif kind in ("u", "arg", "lens"):
                self.after.setdefault(int(parts[0]), []).append((kind, parts[1], body))
            elif kind == "cross":
                self.cross.append((arg, body))
            elif kind == "obj":
                self.obj.append((arg, body))
            elif kind == "now":
                self.now.append((arg, body))
            elif kind == "key":
                self.key = body
            elif kind == "q":
                for line in body.split("\n"):
                    line = line.strip()
                    if not line:
                        continue
                    tier, _, rest = line.partition(":")
                    q, _, ans = rest.partition("||")
                    self.questions.append((tier.strip(), q.strip(), ans.strip()))
            elif kind == "retell":
                self.retell = body
            elif kind == "carry":
                self.carry = body
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
        "<!DOCTYPE html>\n"
        '<html xmlns="http://www.w3.org/1999/xhtml" '
        'xmlns:epub="http://www.idpf.org/2007/ops" lang="en" xml:lang="en">\n'
        f'<head>\n<meta charset="utf-8"/>\n<title>{esc(title)}</title>\n'
        '<link rel="stylesheet" type="text/css" href="style.css"/>\n'
        f"</head>\n<body{cls}>\n{body}\n</body>\n</html>\n"
    )


class Notes:
    """Collects the pop-up notes of one chapter file."""

    def __init__(self, prefix: str):
        self.prefix = prefix
        self.items: list[tuple[str, str]] = []

    def add(self, body_html: str) -> str:
        nid = f"{self.prefix}n{len(self.items) + 1}"
        self.items.append((nid, body_html))
        return nid

    def render(self) -> str:
        if not self.items:
            return ""
        out = ['<section class="notes" epub:type="footnotes">',
               '<p class="dinkus">· · ·</p>',
               '<h2 class="notes-head">Notes to this section</h2>']
        for nid, body in self.items:
            out.append(f'<aside class="note" epub:type="footnote" id="{nid}">'
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


def phrase_end(html_blocks: list[str], term: str) -> tuple[int, int] | None:
    """(block, offset) of the end of term's first occurrence, as attach_gloss finds it."""
    pat = re.compile(r"(?<![\w])" + re.escape(esc(term)) + r"(?![\w])", re.I)
    for k, html_text in enumerate(html_blocks):
        flat = "".join(x for x in re.split(r"(<[^>]+>|\{\{n:[^}]+\}\})", html_text)
                       if not (x.startswith("<") or x.startswith("{{")))
        m = pat.search(flat)
        if m:
            return (k, m.end())
    return None


def attach_gloss(html_text: str, term: str, marker: str) -> tuple[str, bool]:
    """Put marker right after the first occurrence of term in text nodes."""
    pieces = re.split(r"(<[^>]+>|\{\{n:[^}]+\}\})", html_text)
    pat = re.compile(r"(?<![\w])" + re.escape(esc(term)) + r"(?![\w])", re.I)
    for i, piece in enumerate(pieces):
        if piece.startswith("<") or piece.startswith("{{"):
            continue
        m = pat.search(piece)
        if m:
            pieces[i] = piece[: m.end()] + marker + piece[m.end():]
            return "".join(pieces), True
    # The term is split by markup (e.g. partly italic).
    flat, owners = "", []
    for i, piece in enumerate(pieces):
        if piece.startswith("<") or piece.startswith("{{"):
            continue
        for ch in piece:
            flat += ch
            owners.append(i)
    m = pat.search(flat)
    if m:
        j = owners[m.end() - 1] + 1
        while j < len(pieces) and pieces[j].startswith("</"):
            j += 1
        pieces.insert(j, marker)
        return "".join(pieces), True
    return html_text, False


def inline_par(s: str) -> str:
    """Inline markup for a short multi-paragraph note (paragraphs joined)."""
    paras = [p.strip() for p in re.split(r"\n\s*\n", s.strip()) if p.strip()]
    return "</p><p>".join(inline(" ".join(p.split("\n"))) for p in paras)


class Builder:
    def __init__(self, src_path: str, units: list[Unit]):
        self.src = json.load(open(src_path, encoding="utf-8"))
        self.paras = {p["n"]: p for p in self.src["paras"]}
        self.inwood = self.src["notes"]
        self.units = units
        self.files: list[list[str]] = []          # [name, title, xhtml]
        self.toc: list = []
        self.para_file: dict[int, str] = {}
        self.unit_files: dict[str, str] = {}
        self.counter = 0
        self.gloss_index = 0
        self.warnings: list[str] = []
        self.stats = {k: 0 for k in ("commentary", "gloss", "unpack", "arg", "lens",
                                     "inwood", "cross", "obj", "now", "questions",
                                     "answers", "shape", "map", "reading", "line")}

    def new_file(self) -> str:
        name = f"chap_{self.counter:03d}.xhtml"
        self.counter += 1
        return name

    def add(self, name: str, title: str, xhtml: str) -> None:
        self.files.append([name, title, xhtml])

    # -- matter --------------------------------------------------------------
    def matter(self, path: str, toc_parent: list | None = None) -> None:
        text = open(path, encoding="utf-8").read()
        for sec in re.split(r"(?m)^# ", text)[1:]:
            title, _, body = sec.partition("\n")
            title = title.strip()
            name = self.new_file()
            notes = Notes("m%03d" % self.counter)
            content, subs = self.render_matter(body, name, notes)
            xhtml = page(plain(title),
                         '<section epub:type="chapter" class="matter">\n'
                         f'<h1 class="matter-title">{inline(title)}</h1>\n'
                         f"{content}\n{notes.render()}\n</section>")
            self.add(name, plain(title), xhtml)
            (toc_parent if toc_parent is not None else self.toc).append(
                (plain(title), name, subs))

    def render_matter(self, body: str, fname: str, notes: Notes):
        out, subs, buf = [], [], []
        lines = body.split("\n")

        def flush():
            if buf:
                out.append(blocks("\n".join(buf)))
                buf.clear()

        i = 0
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
                j, inner = i + 1, []
                while j < len(lines) and not lines[j].startswith("@end"):
                    inner.append(lines[j])
                    j += 1
                label = (f'<p class="box-label">{inline(title.strip())}</p>'
                         if title.strip() else "")
                out.append(f'<div class="box box-{kind}">{label}'
                           f"{blocks(chr(10).join(inner))}</div>")
                i = j
            elif line.startswith("@tracker"):
                flush()
                out.append(self.tracker())
            elif line.startswith("|"):
                flush()
                rows, j = [], i
                while j < len(lines) and lines[j].startswith("|"):
                    cells = [c.strip() for c in lines[j].strip().strip("|").split("|")]
                    if not all(re.fullmatch(r":?-+:?", c) for c in cells):
                        rows.append(cells)
                    j += 1
                head = "".join(f"<th>{inline(c)}</th>" for c in rows[0])
                rows_html = "".join(
                    "<tr>" + "".join(f"<td>{inline(c)}</td>" for c in r) + "</tr>"
                    for r in rows[1:])
                out.append(f'<table class="grid"><thead><tr>{head}</tr></thead>'
                           f"<tbody>{rows_html}</tbody></table>")
                i = j - 1
            else:
                buf.append(line)
            i += 1
        flush()
        return "\n".join(out), subs

    def tracker(self) -> str:
        out = []
        for u in self.units:
            if not u.shape:
                continue
            fields = dict(u.shape)
            out.append(f'<div class="box box-shape tracker">'
                       f'<p class="box-title">{inline(u.short)} '
                       f'<a class="xref" href="@P{u.first}@">(¶{u.first}–{u.last})</a></p>')
            for key, label in SHAPE_LABELS:
                if key in fields:
                    out.append(f'<p class="sg"><span class="sg-l">{label}:</span> '
                               f"{inline(fields[key])}</p>")
            out.append("</div>")
        return "\n".join(out)

    # -- Hegel's paragraphs ----------------------------------------------------
    def hegel_paragraph(self, n: int, unit: Unit, notes: Notes) -> str:
        p = self.paras[n]
        body, more = p["html"], list(p["more"])
        # Glosses and line notes share one lettered series.  Letters follow
        # the order of the markers in the text; markers are inserted from the
        # last to the first so that no insertion falls inside a later phrase.
        found, missing = [], []
        for item in unit.gloss.get(n, []):
            pos = phrase_end([body] + more, item[1])
            (found if pos else missing).append((pos, item))
        found.sort(key=lambda x: x[0])
        labelled_items = []
        for pos, (kind, term, gtext) in found + missing:
            label = gloss_label(self.gloss_index)
            self.gloss_index += 1
            if kind == "l":
                nid = notes.add(f'<p><span class="nlabel">Note</span> '
                                f"<b>“{inline(term, False)}”</b> {inline_par(gtext)}</p>")
                self.stats["line"] += 1
            else:
                nid = notes.add(f'<p><span class="nlabel">Gloss</span> '
                                f"<b>{inline(term, False)}</b>. {inline_par(gtext)}</p>")
                self.stats["gloss"] += 1
            labelled_items.append((pos, term, f'<sup class="gl">{ref(nid, label, "gref")}</sup>'))
        blocks_ = [body] + more
        for pos, term, marker in sorted(labelled_items, key=lambda x: x[0] or (-1, -1),
                                        reverse=True):
            if pos:
                blocks_[pos[0]], ok = attach_gloss(blocks_[pos[0]], term, marker)
            else:
                ok = False
            if not ok:
                self.warnings.append(f"unit {unit.id}: gloss term not in ¶{n}: {term!r}")
                blocks_[0] += f' <span class="gl-miss">[{esc(term)}]</span>{marker}'
        body, more = blocks_[0], blocks_[1:]

        def inwood_sub(m):
            note = self.inwood[m.group(1)]
            num = note["num"] or "*"
            nid = notes.add(f'<p><span class="nlabel">Inwood</span> '
                            f'<b>¶{note["para"]}, n.&#160;{esc(num)}.</b> {note["html"]}</p>')
            self.stats["inwood"] += 1
            return f'<sup class="iw">{ref(nid, esc(num), "iref")}</sup>'

        body = re.sub(r"\{\{n:([^}]+)\}\}", inwood_sub, body)
        more = [re.sub(r"\{\{n:([^}]+)\}\}", inwood_sub, blk) for blk in more]

        if n in unit.comm:
            move, voice, cbody = unit.comm[n]
            tags = []
            if move:
                tags.append(f"Move: <i>{esc(move)}</i>")
            if voice:
                tags.append(f"Voice: <i>{esc(VOICES.get(voice, voice))}</i>")
            tagline = f'<p class="comm-tags">{" · ".join(tags)}</p>' if tags else ""
            nid = notes.add(f'<p class="comm-head"><span class="nlabel">Commentary</span> '
                            f"<b>on ¶{n}</b></p>{tagline}{labelled(cbody, COMM_LABELS)}")
            num = ref(nid, f"¶{n}", "pref")
            self.stats["commentary"] += 1
        else:
            num = f"¶{n}"
            self.warnings.append(f"unit {unit.id}: no commentary for ¶{n}")
        # Keep adjacent markers (gloss letter, Inwood number) visibly apart.
        body = body.replace("</sup><sup", "</sup>&#8202;<sup")
        more = [blk.replace("</sup><sup", "</sup>&#8202;<sup") for blk in more]
        out = [f'<p class="hegel" id="p{n}"><span class="pnum">{num}</span> {body}</p>']
        out += [f'<p class="hegel cont">{blk}</p>' for blk in more]
        if n in unit.reading:
            self.stats["reading"] += 1
            out.append('<div class="reading-note"><p class="rn-label">Reading note</p>'
                       f"{blocks(unit.reading[n])}</div>")
        for kind, title, abody in unit.after.get(n, []):
            out.append(self.after_box(kind, title, abody, unit))
        return "\n".join(out)

    def after_box(self, kind: str, title: str, body: str, unit: Unit) -> str:
        if kind == "u":
            self.stats["unpack"] += 1
            return ('<div class="box box-unpack">' + layer_label("Read", "Pause and Unpack") +
                    f'<p class="box-title">{inline(title)}</p>'
                    f"{labelled(body, UNPACK_LABELS)}</div>")
        if kind == "lens":
            self.stats["lens"] += 1
            return ('<div class="box box-lens">' + layer_label("Read", "Translation Lens") +
                    f'<p class="box-title">{inline(title)}</p>{blocks(body)}</div>')
        # Argument reconstruction
        self.stats["arg"] += 1
        prem, concl, weak, extra = [], [], [], []
        for key, val in keyed_lines(body):
            if re.fullmatch(r"P\d+", key):
                prem.append((key, val))
            elif key == "C" or re.fullmatch(r"C\d+", key):
                concl.append((key, val))
            elif key == "weak":
                weak.append(val)
            else:
                extra.append(f"{key}: {val}")
        rows = "".join(f'<p class="arg-line"><span class="arg-k">{k}</span> {inline(v)}</p>'
                       for k, v in prem)
        rows += "".join(f'<p class="arg-line arg-c"><span class="arg-k">∴ {k}</span> '
                        f"{inline(v)}</p>" for k, v in concl)
        rows += "".join(f'<p class="arg-weak"><span class="runin">Weakest step.</span> '
                        f"{inline(w)}</p>" for w in weak)
        if extra:
            self.warnings.append(f"unit {unit.id}: unparsed argument lines {extra}")
        return ('<div class="box box-arg">' + layer_label("Read", "Argument Reconstruction") +
                f'<p class="box-title">{inline(title)}</p>{rows}</div>')

    # -- a reading unit -------------------------------------------------------
    def unit_file(self, unit: Unit) -> tuple[str, str]:
        name = self.new_file()
        self.unit_files[unit.id] = name
        for n in range(unit.first, unit.last + 1):
            self.para_file[n] = name
        notes = Notes(f"u{unit.id}")
        self.gloss_index = 0
        first = self.paras[unit.first]
        crumbs = " · ".join(x for x in (first["part"], first["chapter"]) if x)
        words = sum(len(self.paras[n]["text"].split())
                    for n in range(unit.first, unit.last + 1))
        minutes = max(10, round(words / 100 / 5) * 5)
        parts = ['<section epub:type="chapter" class="unit">',
                 f'<p class="crumbs">{esc(crumbs)}</p>',
                 f'<h1 class="unit-title">{inline(unit.title)}</h1>',
                 f'<p class="unit-range">¶{unit.first}–{unit.last} · '
                 f"{words:,} words of Hegel · allow about {minutes} minutes for the text</p>"]
        # ORIENT
        if unit.where:
            parts.append('<div class="box box-where">' + layer_label("Orient", "Where We Are") +
                         f"{blocks(unit.where)}</div>")
        if unit.guide:
            parts.append('<div class="box box-guide">' + layer_label("Orient", "Before You Read") +
                         f"{blocks(unit.guide)}</div>")
        if unit.shape:
            self.stats["shape"] += 1
            fields = dict(unit.shape)
            rows = "".join(f'<p class="sg"><span class="sg-l">{label}:</span> '
                           f"{inline(fields[k])}</p>" for k, label in SHAPE_LABELS if k in fields)
            parts.append('<div class="box box-shape">' +
                         layer_label("Orient", "The Shape at a Glance") +
                         f'<p class="box-title">{inline(unit.short)}</p>{rows}</div>')
        if unit.map:
            self.stats["map"] += 1
            rows, steps = [], []
            for k, v in unit.map:
                if k == "step":
                    steps.append(v)
                    continue
                if steps:
                    rows.append("<ol class=\"steps\">" +
                                "".join(f"<li>{inline(s)}</li>" for s in steps) + "</ol>")
                    steps = []
                rows.append(f'<p class="sg"><span class="sg-l">{MAP_LABELS.get(k, k)}:</span> '
                            f"{inline(v)}</p>")
                if k == "target":
                    rows.append('<p class="sg"><span class="sg-l">Steps:</span></p>')
            if steps:
                rows.append("<ol class=\"steps\">" +
                            "".join(f"<li>{inline(s)}</li>" for s in steps) + "</ol>")
            parts.append('<div class="box box-shape">' + layer_label("Orient", "Argument Map") +
                         "".join(rows) + "</div>")
        if unit.friendly:
            t, b = unit.friendly
            parts.append('<div class="box box-friendly">' +
                         layer_label("Orient", "A Friendlier Explanation") +
                         f'<p class="box-title">{inline(t)}</p>{blocks(b)}</div>')
        # READ
        parts.append('<p class="text-start">· Hegel’s text ·</p>')
        for n in range(unit.first, unit.last + 1):
            p = self.paras[n]
            prev = self.paras.get(n - 1)
            if p["chapter"] and (n == unit.first or prev["chapter"] != p["chapter"]):
                parts.append(f'<h2 class="hegel-head">{esc(p["chapter"])}</h2>')
            if p["section"] and (n == unit.first or prev["section"] != p["section"]):
                parts.append(f'<h3 class="hegel-sub">{esc(p["section"])}</h3>')
            for h in unit.headings.get(n, []):
                parts.append(f'<p class="analytic-head"><span class="ah-label">Hegel’s '
                             f"contents</span> {inline(h)}</p>")
            parts.append(self.hegel_paragraph(n, unit, notes))
        # ARGUE
        for t, b in unit.cross:
            self.stats["cross"] += 1
            parts.append('<div class="box box-cross">' + layer_label("Argue", "Critical Crossroads") +
                         f'<p class="box-title">{inline(t)}</p>{blocks(b)}</div>')
        for t, b in unit.obj:
            self.stats["obj"] += 1
            parts.append('<div class="box box-obj">' + layer_label("Argue", "Objections and Replies") +
                         f'<p class="box-title">{inline(t)}</p>{labelled(b, OBJ_LABELS)}</div>')
        for t, b in unit.now:
            self.stats["now"] += 1
            parts.append('<div class="box box-now">' + layer_label("Argue", "Then and Now") +
                         f'<p class="box-title">{inline(t)}</p>{blocks(b)}</div>')
        # CONSOLIDATE
        if unit.key:
            parts.append('<div class="box box-key">' + layer_label("Consolidate", "Key Ideas") +
                         f"{blocks(unit.key)}</div>")
        if unit.questions:
            tiers = [("check", "Check your understanding"), ("think", "Think it through"),
                     ("push", "Push back"), ("write", "Write")]
            q = ['<div class="box box-questions">' + layer_label("Consolidate", "Questions for Study")]
            for tier, tname in tiers:
                qs = [x for x in unit.questions if x[0] == tier]
                if not qs:
                    continue
                q.append(f'<p class="q-tier">{tname}</p><ol class="q">')
                for _, question, ans in qs:
                    a = ""
                    if ans:
                        nid = notes.add(f'<p><span class="nlabel">Model answer</span></p>'
                                        f"<p>{inline_par(ans)}</p>")
                        a = f' <span class="hint">{ref(nid, "answer", "href")}</span>'
                        self.stats["answers"] += 1
                    q.append(f"<li>{inline(question)}{a}</li>")
                    self.stats["questions"] += 1
                q.append("</ol>")
            q.append("</div>")
            parts.append("".join(q))
            unknown = {x[0] for x in unit.questions} - {t for t, _ in tiers}
            if unknown:
                self.warnings.append(f"unit {unit.id}: unknown question tiers {unknown}")
        if unit.retell:
            nid = notes.add(f'<p><span class="nlabel">Model retelling</span></p>{blocks(unit.retell)}')
            parts.append('<div class="box box-retell">' + layer_label("Consolidate", "Retell It") +
                         "<p>Close the book and explain this section to a friend in five "
                         "sentences: what was claimed, how it was tested, what went wrong, "
                         "and what was learned. Then compare your version with the "
                         f'{ref(nid, "model retelling", "href")}.</p></div>')
        if unit.carry:
            parts.append('<div class="box box-carry">' + layer_label("Consolidate", "Carry Forward") +
                         f"{blocks(unit.carry)}</div>")
        for n in unit.comm:
            if not unit.first <= n <= unit.last:
                self.warnings.append(f"unit {unit.id}: commentary for ¶{n} outside range")
        for n in list(unit.gloss) + list(unit.after) + list(unit.reading):
            if not unit.first <= n <= unit.last:
                self.warnings.append(f"unit {unit.id}: annotation for ¶{n} outside range")
        parts.append(notes.render())
        parts.append("</section>")
        title = plain(unit.title)
        self.add(name, title, page(title, "\n".join(parts)))
        return name, title

    # -- cross-references ------------------------------------------------------
    def resolve_links(self) -> None:
        for entry in self.files:
            name = entry[0]

            def sub(m):
                n = int(m.group(1))
                target = self.para_file.get(n)
                if not target:
                    self.warnings.append(f"{name}: link to ¶{n} not in this edition")
                    return "#"
                return f"#p{n}" if target == name else f"{target}#p{n}"
            entry[2] = re.sub(r"@P(\d+)@", sub, entry[2])


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
    orders: dict[str, int] = {}
    ids = [0]

    def points(entries):
        out = []
        for title, href, kids in entries:
            if href not in orders:
                counter[0] += 1
                orders[href] = counter[0]
            ids[0] += 1
            n = ids[0]
            out.append(f'<navPoint id="np{n}" playOrder="{orders[href]}"><navLabel><text>{esc(title)}'
                       f'</text></navLabel><content src="{href}"/>')
            out.append(points(kids))
            out.append("</navPoint>")
        return "".join(out)
    pts = points(toc)
    return ('<?xml version="1.0" encoding="utf-8"?>\n'
            '<ncx xmlns="http://www.daisy.org/z3986/2005/ncx/" version="2005-1">'
            f'<head><meta name="dtb:uid" content="urn:uuid:{uid}"/><meta name="dtb:depth" content="3"/>'
            '<meta name="dtb:totalPageCount" content="0"/>'
            '<meta name="dtb:maxPageNumber" content="0"/></head>'
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
            '<package xmlns="http://www.idpf.org/2007/opf" version="3.0" '
            'unique-identifier="uid" xml:lang="en">\n'
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


def write_epub(out_path: str, files, toc, css: str, cover: bytes) -> None:
    uid = str(uuid.uuid5(uuid.NAMESPACE_URL, "hegel-phenomenology-part-one-study-edition-v2"))
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
        z.writestr("EPUB/cover.xhtml",
                   page("Cover", '<div class="cover"><img src="cover.jpg" alt="Cover"/></div>',
                        "cover-page"))
        for name, _, xhtml in files:
            z.writestr(f"EPUB/{name}", xhtml)


def original_matter(b: Builder, parent: list) -> None:
    """Inwood's own front matter, reproduced at the back."""
    front = b.src["front"]
    for key, title in (("introduction", "Translator’s Introduction (Michael Inwood)"),
                       ("translation", "Note on the Translation and Commentary"),
                       ("abbreviations", "Abbreviations Used in Inwood’s Notes"),
                       ("glossary", "Inwood’s German–English Glossary")):
        name = b.new_file()
        frag = re.sub(r"<h2>.*?</h2>", "", front[key], count=1, flags=re.S)
        b.add(name, title, page(title, '<section epub:type="chapter" class="matter original">'
                                       f'<h1 class="matter-title">{esc(title)}</h1>\n'
                                       f"{frag}\n</section>"))
        parent.append((title, name, []))


# --------------------------------------------------------------------------

def main(src_path: str, out_path: str) -> None:
    units = [Unit(p) for p in sorted(glob.glob(os.path.join(HERE, "ann", "u*.txt")))]
    units.sort(key=lambda u: u.first)
    b = Builder(src_path, units)
    mdir = os.path.join(HERE, "matter")

    b.matter(os.path.join(mdir, "01_front.txt"))
    intro: list = []
    b.matter(os.path.join(mdir, "02_introduction.txt"), intro)
    if intro:
        b.toc.append(("Editor’s Introduction: Reading the Phenomenology", intro[0][1], intro))
    b.matter(os.path.join(mdir, "03_tools.txt"))

    groups = [("Preface: On Scientific Cognition", 1, 72), ("Introduction", 73, 89),
              ("A. Consciousness", 90, 165), ("B. Self-Consciousness", 166, 230)]
    text: list = []
    for gtitle, a, z in groups:
        kids = []
        for u in units:
            if a <= u.first <= z:
                name, title = b.unit_file(u)
                kids.append((title, name, []))
        if kids:
            text.append((gtitle, kids[0][1], kids))
    if text:
        b.toc.append((f"The Phenomenology of Spirit, ¶1–{LAST_PARA}", text[0][1], text))

    b.matter(os.path.join(mdir, "04_back.txt"))
    orig: list = []
    original_matter(b, orig)
    b.toc.append(("From the Original Edition", orig[0][1], orig))

    covered = sorted(n for u in units for n in range(u.first, u.last + 1))
    if covered != list(range(1, LAST_PARA + 1)):
        missing = sorted(set(range(1, LAST_PARA + 1)) - set(covered))
        b.warnings.append(f"paragraphs not covered by units: {missing[:10]}"
                          f"{'...' if len(missing) > 10 else ''} ({len(missing)})")
    b.resolve_links()

    css = open(os.path.join(HERE, "style.css"), encoding="utf-8").read()
    cover_path = os.path.join(HERE, "cover.jpg")
    if not os.path.exists(cover_path):
        import make_cover
        make_cover.main(cover_path)
    write_epub(out_path, b.files, b.toc, css, open(cover_path, "rb").read())
    for w in b.warnings:
        print("WARN", w)
    print(json.dumps(b.stats))
    print(f"wrote {out_path}: {len(b.files)} files, {len(units)} units")


if __name__ == "__main__":
    sys.path.insert(0, HERE)
    main(sys.argv[1], sys.argv[2])
