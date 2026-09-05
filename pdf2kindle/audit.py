"""Audit a produced EPUB and report objective quality signals.

Conversion fails quietly: a stylesheet that is packaged but never linked, a
footnote marker pointing at an id that does not exist, a running head left in
the text. None of these raise. This module measures them so the converter can
report its own quality instead of assuming it.
"""

from __future__ import annotations

import re
import zipfile
from dataclasses import dataclass, field
from typing import Dict, List

from lxml import etree

_CHAP_RE = re.compile(r"^EPUB/chap_\d+\.xhtml$")
_NOTEREF_RE = re.compile(r'epub:type="noteref"[^>]*href="#([^"]+)"')
_NOTE_RE = re.compile(r'epub:type="footnote" id="([^"]+)"')
_PARA_RE = re.compile(r"<p[^>]*>(.*?)</p>", re.S)
_TAG_RE = re.compile(r"<[^>]+>")
_FOLIO_RE = re.compile(r"^[\divxlcIVXLC]{1,6}$")
_YEAR_RE = re.compile(r"^(1[0-9]|20)\d{2}$")  # a year is content, not a folio


@dataclass
class Audit:
    chapters: int = 0
    words: int = 0
    noterefs: int = 0
    notes: int = 0
    dead_links: List[str] = field(default_factory=list)
    unlinked_markers: int = 0
    stylesheet_linked: bool = False
    has_cover: bool = False
    malformed: List[str] = field(default_factory=list)
    missing_images: List[str] = field(default_factory=list)
    furniture: List[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return (
            not self.dead_links
            and not self.malformed
            and not self.missing_images
            and self.stylesheet_linked
            and self.has_cover
        )

    def as_dict(self) -> Dict:
        return {
            "chapters": self.chapters, "words": self.words,
            "noterefs": self.noterefs, "notes": self.notes,
            "dead_links": self.dead_links, "unlinked_markers": self.unlinked_markers,
            "stylesheet_linked": self.stylesheet_linked, "has_cover": self.has_cover,
            "malformed": self.malformed, "missing_images": self.missing_images,
            "furniture": self.furniture, "ok": self.ok,
        }


def audit_epub(path: str) -> Audit:
    a = Audit()
    with zipfile.ZipFile(path) as z:
        names = z.namelist()
        a.has_cover = any("cover" in n.lower() for n in names)
        chapters = [n for n in names if _CHAP_RE.match(n)]
        a.chapters = len(chapters)
        linked = True

        for n in names:
            if n.endswith((".xhtml", ".opf", ".ncx", ".xml")):
                try:
                    etree.fromstring(z.read(n))
                except Exception as exc:
                    a.malformed.append(f"{n}: {exc}")

        for n in sorted(chapters):
            c = z.read(n).decode("utf-8", "replace")
            if "style.css" not in c:
                linked = False
            refs = set(_NOTEREF_RE.findall(c))
            notes = set(_NOTE_RE.findall(c))
            a.noterefs += len(refs)
            a.notes += len(notes)
            a.dead_links += [f"{n}#{r}" for r in sorted(refs - notes)]
            a.unlinked_markers += len(re.findall(r"<sup>(?!<a)", c))

            body = c[: c.find("<section")] if "<section" in c else c
            for p in _PARA_RE.findall(body):
                t = _TAG_RE.sub("", p).strip()
                a.words += len(t.split())
                if t and _FOLIO_RE.match(t) and not _YEAR_RE.match(t):
                    a.furniture.append(f"{n}: {t!r}")

            for src in re.findall(r'<img[^>]*src="([^"]+)"', c):
                target = "EPUB/" + src.lstrip("./")
                if target not in names:
                    a.missing_images.append(f"{n} -> {src}")

        a.stylesheet_linked = linked and any(n.endswith("style.css") for n in names)
    return a


def format_audit(a: Audit) -> str:
    lines = [
        f"  chapters   {a.chapters}",
        f"  words      {a.words:,}",
        f"  notes      {a.notes} bodies / {a.noterefs} linked markers"
        + (f", {a.unlinked_markers} unlinked" if a.unlinked_markers else ""),
        f"  stylesheet {'linked' if a.stylesheet_linked else 'MISSING'}",
        f"  cover      {'present' if a.has_cover else 'MISSING'}",
    ]
    for label, items in (
        ("dead note links", a.dead_links),
        ("malformed XML", a.malformed),
        ("missing images", a.missing_images),
        ("page furniture left in text", a.furniture),
    ):
        if items:
            lines.append(f"  ! {len(items)} {label}: {', '.join(items[:3])}"
                         + (" ..." if len(items) > 3 else ""))
    lines.append(f"  status     {'OK' if a.ok else 'ISSUES FOUND'}")
    return "\n".join(lines)
