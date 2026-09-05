"""Generate semantic, Kindle-tuned XHTML and CSS from the Document model."""

from __future__ import annotations

from typing import Callable, List
from xml.sax.saxutils import escape, quoteattr

from .model import Chapter, Element, ElementKind, InlineRun

# Kindle's renderer honours a conservative subset of CSS. Justification plus
# automatic hyphenation gives the clean "book" look; we avoid absolute units.
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

h1, h2, h3, h4 {
  font-family: "Helvetica Neue", Arial, sans-serif;
  text-align: left;
  line-height: 1.25;
  -webkit-hyphens: none;
  hyphens: none;
  page-break-after: avoid;
  margin: 1em 0 0.6em 0;
}
h1 { font-size: 1.8em; margin-top: 1.4em; page-break-before: always; }
h2 { font-size: 1.45em; }
h3 { font-size: 1.2em; }
h4 { font-size: 1.05em; }

p {
  margin: 0;
  text-indent: 1.3em;
}
p.noindent, h1 + p, h2 + p, h3 + p, h4 + p, blockquote p:first-child {
  text-indent: 0;
}

blockquote {
  margin: 0.9em 1.6em;
  font-size: 0.94em;
  text-indent: 0;
  color: #222;
}
blockquote p { text-indent: 0; }

p.caption {
  text-indent: 0;
  text-align: center;
  font-size: 0.85em;
  font-style: italic;
  margin: 0.4em 0 1em 0;
  color: #333;
}

/* Bibliography / references: hanging indent so each entry is scannable. */
p.reference {
  text-indent: -1.4em;
  margin-left: 1.4em;
  text-align: left;
  margin-bottom: 0.35em;
  -webkit-hyphens: none;
  hyphens: none;
}

div.image {
  text-align: center;
  margin: 1em 0;
  page-break-inside: avoid;
}
div.image img { max-width: 100%; height: auto; }

sup { line-height: 0; font-size: 0.75em; }
a.noteref { text-decoration: none; }

section.footnotes {
  margin-top: 2em;
  border-top: 1px solid #999;
  padding-top: 0.6em;
  font-size: 0.85em;
}
section.footnotes h2 {
  font-size: 1em;
  text-transform: uppercase;
  letter-spacing: 0.05em;
}
aside.footnote { margin: 0.4em 0; text-align: left; text-indent: 0; }
aside.footnote a { text-decoration: none; }
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
        if r.bold and r.italic:
            text = f"<strong><em>{text}</em></strong>"
        elif r.bold:
            text = f"<strong>{text}</strong>"
        elif r.italic:
            text = f"<em>{text}</em>"
        out.append(text)
    return "".join(out)


def _render_element(el: Element, image_href_for: Callable[[Element], str], prev_heading: bool) -> str:
    if el.kind == ElementKind.HEADING:
        lvl = min(max(el.level, 1), 4)
        # Headings carry their own weight from CSS; drop inline emphasis.
        text = escape("".join(r.text for r in el.runs)).strip()
        idattr = f" id={quoteattr(el.anchor)}" if el.anchor else ""
        return f"<h{lvl}{idattr}>{text}</h{lvl}>\n"
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
    # paragraph
    cls = ' class="noindent"' if prev_heading else ""
    return f"<p{cls}>{_render_runs(el.runs)}</p>\n"


def render_footnotes(chapter: Chapter) -> str:
    if not chapter.footnotes:
        return ""
    parts = ['<section class="footnotes" epub:type="footnotes">\n<h2>Notes</h2>\n']
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
    for el in chapter.elements:
        body.append(_render_element(el, image_href_for, prev_heading))
        prev_heading = el.kind == ElementKind.HEADING
    body.append(render_footnotes(chapter))
    return head + "".join(body) + "</body>\n</html>\n"
