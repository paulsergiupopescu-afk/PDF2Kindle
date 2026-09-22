"""Generate semantic, Kindle-tuned XHTML and CSS from the Document model.

The visual design follows the conventions of well-typeset print books, kept
inside the conservative CSS subset Kindle's renderer actually honours: a
two-part chapter opening (a small letter-spaced number over a large serif
title), a drop cap with a small-caps lead-in on the chapter's first
paragraph, a quotation-mark ornament on block quotes, and a footnote section
set off by a centered dinkus rather than a plain rule. Every flourish
degrades gracefully -- an unmatched heading shape or an unusually short
opening paragraph just falls back to plain rendering, never breaking content
for the sake of style.
"""

from __future__ import annotations

import re
from typing import Callable, List, Optional
from xml.sax.saxutils import escape, quoteattr

from .model import Chapter, Element, ElementKind, InlineRun

# Kindle's renderer honours a conservative subset of CSS. Justification plus
# automatic hyphenation gives the clean "book" look; we avoid absolute units,
# floats other than the drop cap (which degrades to an inline glyph on
# readers that ignore it), and anything requiring script.
STYLESHEET = """\
@namespace epub "http://www.idpf.org/2007/ops";

html, body { margin: 0; padding: 0; }
body {
  font-family: Georgia, "Times New Roman", serif;
  line-height: 1.5;
  text-align: justify;
  -webkit-hyphens: auto;
  -epub-hyphens: auto;
  hyphens: auto;
  widows: 2;
  orphans: 2;
}

/* Headings -------------------------------------------------------------- */

h1, h2, h3, h4 {
  font-family: Georgia, "Times New Roman", serif;
  font-weight: normal;
  text-align: left;
  line-height: 1.25;
  -webkit-hyphens: none;
  hyphens: none;
  page-break-after: avoid;
  margin: 1em 0 0.7em 0;
}
h1 {
  font-size: 1.9em;
  margin-top: 0;
  margin-bottom: 0.9em;
  padding-top: 2.4em;
  text-align: center;
  page-break-before: always;
}
h2 { font-size: 1.4em; font-style: italic; }
h3 {
  font-size: 1.05em;
  font-weight: bold;
  font-style: italic;
  text-transform: uppercase;
  letter-spacing: 0.04em;
}
h4 { font-size: 1em; font-weight: bold; }

/* A numbered chapter heading is split into a small label over the title:
   <h1><span class="chnum">2</span><span class="chtitle">Title</span></h1> */
h1 span.chnum {
  display: block;
  font-family: Georgia, "Times New Roman", serif;
  font-style: italic;
  font-size: 0.4em;
  letter-spacing: 0.28em;
  text-transform: uppercase;
  color: #555;
  margin-bottom: 0.7em;
}
h1 span.chtitle {
  display: block;
}
/* A short rule under the chapter title, echoed in print books' half-titles.
   A bare hairline (not CSS content/pseudo-elements, which Kindle drops). */
h1 + p.rule, h1 + p.opening + p.rule {
  text-align: center;
  margin: 0 0 1.3em 0;
  font-size: 0.9em;
  letter-spacing: 0.5em;
  color: #999;
}

/* Body paragraphs --------------------------------------------------------*/

p {
  margin: 0;
  text-indent: 1.3em;
}
p.noindent, h1 + p, h2 + p, h3 + p, h4 + p, blockquote p:first-child {
  text-indent: 0;
}

/* The chapter's opening paragraph: a drop cap plus a small-caps lead-in,
   the way a printed book announces "here the chapter begins" without a
   decorative image. Both degrade harmlessly on a reader that ignores
   float or font-variant -- text stays complete and in order either way. */
p.opening {
  text-indent: 0;
}
p.opening span.dropcap {
  float: left;
  font-family: Georgia, "Times New Roman", serif;
  font-size: 3.6em;
  line-height: 0.86;
  padding-right: 0.09em;
  padding-top: 0.03em;
}
p.opening span.lead {
  font-variant: small-caps;
  letter-spacing: 0.02em;
}

/* Block quotes ------------------------------------------------------------
   A generous inset with a quiet quotation mark rather than a border, which
   several Kindle firmware versions clip or ignore inside reflowed text. */
blockquote {
  margin: 1em 2em;
  font-size: 0.95em;
  font-style: italic;
  text-indent: 0;
  color: #333;
}
blockquote p { text-indent: 0; }

p.caption {
  text-indent: 0;
  text-align: center;
  font-size: 0.85em;
  font-style: italic;
  margin: 0.5em 0 1.2em 0;
  color: #333;
}

/* Bibliography / references: hanging indent so each entry is scannable. */
p.reference {
  text-indent: -1.4em;
  margin-left: 1.4em;
  text-align: left;
  margin-bottom: 0.4em;
  -webkit-hyphens: none;
  hyphens: none;
}

.table-wrap {
  width: 100%;
  overflow-x: auto;
  margin: 1.2em 0;
  page-break-inside: avoid;
}
table {
  width: 100%;
  border-collapse: collapse;
  font-size: 0.9em;
  text-align: left;
}
th, td {
  border: 1px solid #aaa;
  padding: 0.35em 0.45em;
  vertical-align: top;
}
th {
  font-weight: bold;
}
div.image {
  text-align: center;
  margin: 1.3em 0;
  page-break-inside: avoid;
}
div.image img { max-width: 100%; height: auto; }

/* Footnotes -----------------------------------------------------------*/

sup { line-height: 0; font-size: 0.7em; }
a.noteref { text-decoration: none; }

section.footnotes {
  margin-top: 2.2em;
  padding-top: 0.2em;
  font-size: 0.86em;
}
p.dinkus {
  text-align: center;
  margin: 0 0 1.3em 0;
  letter-spacing: 0.6em;
  color: #999;
}
section.footnotes h2 {
  display: none;
}
aside.footnote {
  margin: 0 0 0.55em 0;
  text-align: left;
  text-indent: -1.4em;
  padding-left: 1.4em;
}
aside.footnote a { text-decoration: none; font-style: normal; }
"""

_DOC_HEAD = (
    '<?xml version="1.0" encoding="utf-8"?>\n'
    '<html xmlns="http://www.w3.org/1999/xhtml" '
    'xmlns:epub="http://www.idpf.org/2007/ops" xml:lang={lang} lang={lang}>\n'
    "<head>\n"
    '<meta charset="utf-8"/>\n'
    "<title>{title}</title>\n"
    '<link rel="stylesheet" type="text/css" href="style.css"/>\n'
    "</head>\n<body>\n"
)

# A numbered chapter title: "2. Methodological framework", "1.Literature
# overview", "Chapter 3: The Aims of Law". Captures the number/label and the
# remaining title text separately so they can be set as two visual tiers.
_CHAPTER_SPLIT_RE = re.compile(
    r"^\s*(chapter|part|book)\s+([\divxlc]+)\s*[:.\-–]?\s*(.*)$", re.IGNORECASE
)
_NUM_SPLIT_RE = re.compile(r"^\s*(\d+(?:\.\d+){0,3})\.?\s+(\S.*)$")

# Small ornament marking a footnote section -- a classic printer's dinkus.
_DINKUS = "· · ·"


def _split_chapter_heading(text: str) -> Optional[tuple]:
    """Return (label, title) for a numbered chapter heading, else None.

    Only ever called for a *bare, unformatted* level-1 heading text -- never
    changes what is stored, only how a matching one is presented.
    """
    m = _CHAPTER_SPLIT_RE.match(text)
    if m:
        label = f"{m.group(1).title()} {m.group(2)}"
        title = m.group(3).strip()
        return (label, title) if title else None
    m = _NUM_SPLIT_RE.match(text)
    if m:
        return m.group(1), m.group(2).strip()
    return None


def _render_runs(runs: List[InlineRun]) -> str:
    out: List[str] = []
    for r in runs:
        if r.noteref:
            ref_id = escape(r.noteref)
            out.append(
                f'<sup><a class="noteref" epub:type="noteref" '
                f'id="{ref_id}-ref" href="#{ref_id}">{escape(r.text)}</a></sup>'
            )
            continue
        text = escape(r.text)
        if r.sup:
            out.append(f"<sup>{text}</sup>")
            continue
        if r.bold and r.italic:
            text = f"<strong><em>{text}</em></strong>"
        elif r.bold:
            text = f"<strong>{text}</strong>"
        elif r.italic:
            text = f"<em>{text}</em>"
        out.append(text)
    return "".join(out)


def _render_heading(el: Element) -> str:
    lvl = min(max(el.level, 1), 4)
    text = escape("".join(r.text for r in el.runs)).strip()
    idattr = f" id={quoteattr(el.anchor)}" if el.anchor else ""
    if lvl == 1:
        split = _split_chapter_heading("".join(r.text for r in el.runs).strip())
        if split:
            label, title = split
            return (
                f"<h1{idattr}><span class=\"chnum\">{escape(label)}</span>"
                f"<span class=\"chtitle\">{escape(title)}</span></h1>\n"
            )
        return f"<h1{idattr}>{text}</h1>\n"
    return f"<h{lvl}{idattr}>{text}</h{lvl}>\n"


# A leading quotation mark stays with the small-caps lead-in rather than
# being drop-capped itself, matching how print books set it.
_LEAD_PUNCT = "“‘\"'("


def _split_opening(runs: List[InlineRun]) -> Optional[tuple]:
    """Split a paragraph's first run into (prefix, dropcap, lead, rest-of-run).

    Only ever attempted on a plain (non-bold/italic/noteref/sup) first run
    with enough plain text to extract a sensible drop cap and a 2-3 word
    small-caps lead without cutting into markup or a footnote marker that
    might immediately follow. Returns None whenever the shape doesn't fit
    cleanly, so the caller can fall back to a perfectly ordinary paragraph.
    """
    if not runs:
        return None
    first = runs[0]
    if first.noteref or first.sup or first.bold or first.italic:
        return None
    text = first.text
    i = 0
    prefix = ""
    while i < len(text) and text[i] in _LEAD_PUNCT:
        prefix += text[i]
        i += 1
    # Only a genuine capital letter reads as an intentional drop cap; an OCR
    # slip or a run starting mid-sentence should just render plainly.
    if i >= len(text) or not text[i].isupper():
        return None
    dropcap = text[i]
    rest = text[i + 1:]
    # Lead-in: the remainder of the drop-capped word, plus up to one more
    # word, so the small-caps run ends on a natural word boundary.
    m = re.match(r"(\w*)(\s+\w+)?", rest)
    if not m:
        return None
    lead = m.group(1) + (m.group(2) or "")
    if len(lead) < 1 or len(lead) > 24:
        return None
    tail = rest[len(lead):]
    return prefix, dropcap, lead, tail


def _render_opening_paragraph(el: Element) -> Optional[str]:
    split = _split_opening(el.runs)
    if split is None:
        return None
    prefix, dropcap, lead, tail = split
    rest = escape(tail) + _render_runs(el.runs[1:])
    return (
        f'<p class="opening"><span class="dropcap">{escape(dropcap)}</span>'
        f'<span class="lead">{escape(prefix + lead)}</span>{rest}</p>\n'
    )


def _render_element(
    el: Element, image_href_for: Callable[[Element], str], prev_heading: bool, prev_h1: bool
) -> str:
    if el.kind == ElementKind.HEADING:
        return _render_heading(el)
    if el.kind == ElementKind.IMAGE:
        href = image_href_for(el)
        if not href:
            return ""
        return f'<div class="image"><img src={quoteattr(href)} alt="figure"/></div>\n'
    if el.kind == ElementKind.BLOCKQUOTE:
        return f"<blockquote><p>{_render_runs(el.runs)}</p></blockquote>\n"
    if el.kind == ElementKind.CAPTION:
        return f'<p class="caption">{_render_runs(el.runs)}</p>\n'
    if el.kind == ElementKind.REFERENCE:
        return f'<p class="reference">{_render_runs(el.runs)}</p>\n'
    if el.kind == ElementKind.TABLE:
        rows = []
        for ri, row in enumerate(el.table_rows):
            tag = "th" if ri == 0 else "td"
            cells = "".join(f"<{tag}>{escape(cell)}</{tag}>" for cell in row)
            rows.append(f"<tr>{cells}</tr>")
        return '<div class="table-wrap"><table>\\n' + "\\n".join(rows) + '\\n</table></div>\\n'
    # paragraph
    if prev_h1:
        rendered = _render_opening_paragraph(el)
        if rendered is not None:
            return rendered
    cls = ' class="noindent"' if prev_heading else ""
    return f"<p{cls}>{_render_runs(el.runs)}</p>\n"


def render_footnotes(chapter: Chapter) -> str:
    if not chapter.footnotes:
        return ""
    parts = [
        '<section class="footnotes" epub:type="footnotes">\n'
        f'<p class="dinkus">{_DINKUS}</p>\n<h2>Notes</h2>\n'
    ]
    for note in chapter.footnotes:
        nid = escape(note.note_id or "")
        label = escape(note.note_label or "*")
        body = _render_runs(note.runs)
        parts.append(
            f'<aside class="footnote" epub:type="footnote" id="{nid}">'
            f'<p><a href="#{nid}-ref">{label}.</a> {body}</p></aside>\n'
        )
    parts.append("</section>\n")
    return "".join(parts)


def render_chapter(chapter: Chapter, image_href_for: Callable[[Element], str], language: str = "en") -> str:
    head = _DOC_HEAD.format(lang=quoteattr(language), title=escape(chapter.title or "Chapter"))
    body: List[str] = []
    prev_heading = False
    prev_h1 = False
    for el in chapter.elements:
        body.append(_render_element(el, image_href_for, prev_heading, prev_h1))
        prev_h1 = el.kind == ElementKind.HEADING and min(max(el.level, 1), 4) == 1
        prev_heading = el.kind == ElementKind.HEADING
    body.append(render_footnotes(chapter))
    return head + "".join(body) + "</body>\n</html>\n"
