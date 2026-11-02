"""Audit an EPUB package for structural and navigation defects.

Read the OPF manifest and spine rather than assuming that every EPUB uses
ebooklib's ``EPUB/chap_###.xhtml`` naming convention. Cross-document note links
are resolved too, including books that store endnotes in a separate XHTML file.
"""

from __future__ import annotations

import posixpath
import re
import zipfile
from dataclasses import dataclass, field
from typing import Dict, List, Set
from urllib.parse import unquote, urlsplit

from lxml import etree

_FOLIO_RE = re.compile(r"^[\divxlcIVXLC]{1,6}$")
_YEAR_RE = re.compile(r"^(1[0-9]|20)\d{2}$")


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

    @property
    def ok(self) -> bool:
        return not (
            self.dead_links
            or self.dead_internal_links
            or self.malformed
            or self.missing_images
            or not self.stylesheet_linked
            or not self.has_cover
        )

    def as_dict(self) -> Dict:
        return {
            "chapters": self.chapters,
            "words": self.words,
            "noterefs": self.noterefs,
            "notes": self.notes,
            "tables": self.tables,
            "dead_links": self.dead_links,
            "unlinked_markers": self.unlinked_markers,
            "stylesheet_linked": self.stylesheet_linked,
            "has_cover": self.has_cover,
            "malformed": self.malformed,
            "missing_images": self.missing_images,
            "furniture": self.furniture,
            "pagebreaks": self.pagebreaks,
            "internal_links": self.internal_links,
            "dead_internal_links": self.dead_internal_links,
            "ok": self.ok,
        }


def _local_name(node: etree._Element) -> str:
    return etree.QName(node).localname


def _resolve(source: str, href: str) -> tuple[str, str]:
    """Return (package path, fragment) for a relative EPUB reference."""
    parts = urlsplit(href)
    target = unquote(parts.path)
    fragment = unquote(parts.fragment)
    if not target:
        return source, fragment
    return posixpath.normpath(posixpath.join(posixpath.dirname(source), target)), fragment


def _epub_type(element: etree._Element) -> Set[str]:
    """Return epub:type tokens independent of the namespace prefix."""
    for key, value in element.attrib.items():
        qname = etree.QName(key)
        if qname.localname == "type" and qname.namespace:
            return set(value.split())
    return set()


def audit_epub(path: str) -> Audit:
    a = Audit()
    with zipfile.ZiFile(path) as z:
        names_list = z.namelist()
        names = set(names_list)
        parsed: Dict[str, etree._Element] = {}

