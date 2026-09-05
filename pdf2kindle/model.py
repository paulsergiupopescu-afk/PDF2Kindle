"""Shared data model for the conversion pipeline.

A PDF is extracted into a tree of pages → blocks → lines → spans that mirrors
what PyMuPDF gives us, but as typed objects the rest of the pipeline can reason
about. Later stages progressively turn that geometry into semantic elements
(paragraphs, headings, footnotes, images) grouped into chapters.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import List, Optional, Tuple

BBox = Tuple[float, float, float, float]  # (x0, y0, x1, y1)

# PyMuPDF span flag bits.
FLAG_SUPERSCRIPT = 1 << 0
FLAG_ITALIC = 1 << 1
FLAG_SERIF = 1 << 2
FLAG_MONO = 1 << 3
FLAG_BOLD = 1 << 4


@dataclass
class Span:
    """A run of text sharing one font/size/style."""

    text: str
    font: str
    size: float
    flags: int
    color: int
    bbox: BBox
    origin: Tuple[float, float]

    @property
    def bold(self) -> bool:
        return bool(self.flags & FLAG_BOLD) or "bold" in self.font.lower() or "black" in self.font.lower()

    @property
    def italic(self) -> bool:
        return bool(self.flags & FLAG_ITALIC) or "italic" in self.font.lower() or "oblique" in self.font.lower()

    @property
    def superscript(self) -> bool:
        return bool(self.flags & FLAG_SUPERSCRIPT)

    @property
    def mono(self) -> bool:
        return bool(self.flags & FLAG_MONO)


@dataclass
class Line:
    spans: List[Span]
    bbox: BBox

    @property
    def text(self) -> str:
        return "".join(s.text for s in self.spans)

    @property
    def x0(self) -> float:
        return self.bbox[0]

    @property
    def x1(self) -> float:
        return self.bbox[2]

    @property
    def y0(self) -> float:
        return self.bbox[1]

    @property
    def y1(self) -> float:
        return self.bbox[3]

    @property
    def height(self) -> float:
        return self.bbox[3] - self.bbox[1]

    @property
    def dominant_size(self) -> float:
        """Font size of the longest span on the line."""
        if not self.spans:
            return 0.0
        return max(self.spans, key=lambda s: len(s.text)).size


@dataclass
class ImageBlock:
    data: bytes
    ext: str
    bbox: BBox
    width: int
    height: int


@dataclass
class Page:
    number: int  # 0-based
    width: float
    height: float
    lines: List[Line] = field(default_factory=list)
    images: List[ImageBlock] = field(default_factory=list)
    ocr: bool = False  # True if this page's text came from OCR


class ElementKind(str, Enum):
    HEADING = "heading"
    PARAGRAPH = "paragraph"
    BLOCKQUOTE = "blockquote"
    IMAGE = "image"
    FOOTNOTE = "footnote"  # a collected note body (rendered at chapter end)


@dataclass
class InlineRun:
    """A styled fragment of text inside a paragraph/heading."""

    text: str
    bold: bool = False
    italic: bool = False
    # If set, this run is a footnote reference marker with the given note id.
    noteref: Optional[str] = None


@dataclass
class Element:
    kind: ElementKind
    runs: List[InlineRun] = field(default_factory=list)
    level: int = 0  # heading level (1..6)
    # image payload
    image: Optional[ImageBlock] = None
    # footnote payload
    note_id: Optional[str] = None
    note_label: str = ""

    @property
    def text(self) -> str:
        return "".join(r.text for r in self.runs)


@dataclass
class Chapter:
    title: str
    elements: List[Element] = field(default_factory=list)
    footnotes: List[Element] = field(default_factory=list)


@dataclass
class Document:
    chapters: List[Chapter] = field(default_factory=list)
    title: str = ""
    author: str = ""
    language: str = "en"
    cover: Optional[ImageBlock] = None
