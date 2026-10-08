"""Audit a produced EPUB and report objective quality signals.

Conversion fails quietly: a stylesheet that is packaged but never linked, a
footnote marker pointing at an id that does not exist, a running head left in
the text. None of these raise. This module measures them so the converter can
report its own quality instead of assuming it.
"""

from __future__ import annotations

import hashlib
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
# A short line with a page number at one end: "Classic teaching on original
# sin 13", "x Foreword". Furniture when its words are a heading's words.
_HEAD_FOLIO_RE = re.compile(r"^(?:([\divxlc]{1,6})\s+(.{3,80})|(.{3,80}?)\s+([\divxlc]{1,6}))$")
_HEADING_RE = re.compile(r"<h([1-4])[^>]*>(.*?)</h\1>", re.S)
_BLOCK_RE = re.compile(r"<(p|blockquote)[^>]*>(.*?)</\1>", re.S)


@dataclass
class Audit:
    chapters: int = 0
    words: int = 0
    noterefs: int = 0
    notes: int = 0
    tables: int = 0
    dead_links: List[str] = field(default_factory=list)
    unlinked_markers: int = 0
    stylesheet_linked: bool = False
    has_cover: bool = False
    malformed: List[str] = field(default_factory=list)
    missing_images: List[str] = field(default_factory=list)
    furniture: List[str] = field(default_factory=list)
    pagebreaks: int = 0
    internal_links: int = 0
    dead_internal_links: List[str] = field(default_factory=list)
    split_headings: List[str] = field(default_factory=list)
    duplicate_images: List[str] = field(default_factory=list)

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
            "noterefs": self.noterefs, "notes": self.notes, "tables": self.tables,
            "dead_links": self.dead_links, "unlinked_markers": self.unlinked_markers,
            "stylesheet_linked": self.stylesheet_linked, "has_cover": self.has_cover,
            "malformed": self.malformed, "missing_images": self.missing_images,
            "furniture": self.furniture, "split_headings": self.split_headings,
            "duplicate_images": self.duplicate_images, "ok": self.ok,
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

        texts = {n: z.read(n).decode("utf-8", "replace") for n in sorted(chapters)}
        headings = {
            _norm(_TAG_RE.sub("", h)) for c in texts.values() for _, h in _HEADING_RE.findall(c)
        }
        headings |= {_norm(t) for c in texts.values() for t in re.findall(r"<title>(.*?)</title>", c)}

        seen_images: Dict[bytes, str] = {}
        for n in names:
            if n.startswith("EPUB/images/"):
                key = hashlib.sha1(z.read(n)).digest()
                if key in seen_images:
                    a.duplicate_images.append(f"{n} = {seen_images[key]}")
                else:
                    seen_images[key] = n

        for n, c in texts.items():
            if "style.css" not in c:
                linked = False
            refs = set(_NOTEREF_RE.findall(c))
            notes = set(_NOTE_RE.findall(c))
            a.noterefs += len(refs)
            a.notes += len(notes)
            a.tables += len(re.findall(r"<table(?:\s|>)", c, re.IGNORECASE))
            a.dead_links += [f"{n}#{r}" for r in sorted(refs - notes)]
            a.unlinked_markers += len(re.findall(r"<sup>(?!<a)", c))
            a.pagebreaks += len(re.findall(r'epub:type="pagebreak"', c))
            links = re.findall(r'<a[^>]+href="#([^"]+)"', c)
            a.internal_links += len(links)
            ids = set(re.findall(r'\bid="([^"]+)"', c))
            a.dead_internal_links += [f"{n}#{target}" for target in links if target not in ids and target not in notes]

            body = c[: c.find("<section")] if "<section" in c else c
            for p in _PARA_RE.findall(body):
                t = _TAG_RE.sub("", p).strip()
                a.words += len(t.split())
                if t and _FOLIO_RE.match(t) and not _YEAR_RE.match(t):
                    a.furniture.append(f"{n}: {t!r}")

            for _, block in _BLOCK_RE.findall(body):
                t = " ".join(_TAG_RE.sub("", block).split())
                m = _HEAD_FOLIO_RE.match(t)
                words = _norm(m.group(2) or m.group(3)) if m else ""
                if words and any(words in h for h in headings if len(words) >= 0.5 * len(h)):
                    a.furniture.append(f"{n}: {t!r}")

            # A heading continued in lowercase is one title split in two.
            levels = list(_HEADING_RE.finditer(body))
            for h1, h2 in zip(levels, levels[1:]):
                second = _TAG_RE.sub("", h2.group(2)).strip()
                if (h1.end() + 2 >= h2.start() and h1.group(1) == h2.group(1)
                        and second[:1].islower()):
                    a.split_headings.append(f"{n}: {second!r}")

            for src in re.findall(r'<img[^>]*src="([^"]+)"', c):
                target = "EPUB/" + src.lstrip("./")
                if target not in names:
                    a.missing_images.append(f"{n} -> {src}")

        a.stylesheet_linked = linked and any(n.endswith("style.css") for n in names)
    return a


def _norm(text: str) -> str:
    """Lowercase words only, minus a leading article, for comparing heads."""
    words = re.findall(r"[^\W\d_]+", text.lower())
    if words and words[0] in ("the", "a", "an"):
        words = words[1:]
    return " ".join(words)


def format_audit(a: Audit) -> str:
    lines = [
        f"  chapters   {a.chapters}",
        f"  words      {a.words:,}",
        f"  tables     {a.tables}",
        f"  pagebreaks {a.pagebreaks}",
        f"  int. links {a.internal_links}",
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
        ("dead internal links", a.dead_internal_links),
        ("headings split in two", a.split_headings),
        ("duplicate images", a.duplicate_images),
    ):
        if items:
            lines.append(f"  ! {len(items)} {label}: {', '.join(items[:3])}"
                         + (" ..." if len(items) > 3 else ""))
    lines.append(f"  status     {'OK' if a.ok else 'ISSUES FOUND'}")
    return "\n".join(lines)
