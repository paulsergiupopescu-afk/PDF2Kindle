"""Global document analysis and confidence-aware classification.

This module intentionally stays deterministic.  It gives later reconstruction
stages a document-wide view of page roles, typography and recurring furniture,
rather than asking each page to make isolated decisions.
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field
from enum import Enum
from statistics import median
from typing import Dict, List, Optional, Tuple

from .model import Line, Page

class PageType(str, Enum):
    UNKNOWN = "unknown"
    COVER = "cover"
    TITLE = "title"
    COPYRIGHT = "copyright"
    CONTENTS = "contents"
    PROSE = "prose"
    CHAPTER_OPENING = "chapter_opening"
    BIBLIOGRAPHY = "bibliography"
    INDEX = "index"
    TABLE_FIGURE = "table_figure"

@dataclass
class Evidence:
    signal: str
    weight: float
    detail: str = ""

@dataclass
class PageClassification:
    number: int
    page_type: PageType
    confidence: float
    evidence: List[Evidence] = field(default_factory=list)

@dataclass
class DocumentStatistics:
    body_size: float
    line_height: float
    body_left: float
    common_sizes: List[float]
    recurring_margin_text: Counter
    classifications: List[PageClassification]

    @property
    def page_types(self) -> Dict[PageType, int]:
        out = Counter(c.page_type for c in self.classifications)
        return dict(out)

_WORDS = re.compile(r"\w+", re.UNICODE)
_TOC = re.compile(r"\b(table of contents|contents|list of (figures|tables|illustrations|maps))\b", re.I)
_COPYRIGHT = re.compile(r"\b(copyright|all rights reserved|isbn)\b", re.I)
_INDEX = re.compile(r"^\s*index\s*$", re.I)
_BIB = re.compile(r"^\s*(bibliography|references|works cited|sources)\s*$", re.I)

def _text(page: Page) -> str:
    return " ".join(line.text.strip() for line in page.lines if line.text.strip())

def _body_size(pages: List[Page]) -> float:
    c = Counter()
    for p in pages:
        for line in p.lines:
            for span in line.spans:
                c[round(span.size, 1)] += len(span.text.strip())
    return float(c.most_common(1)[0][0]) if c else 11.0

def _line_height(pages: List[Page]) -> float:
    vals = [line.height for p in pages for line in p.lines if line.height > 0]
    return float(median(vals)) if vals else 12.0

def _body_left(pages: List[Page], size: float) -> float:
    c = Counter()
    for p in pages:
        for line in p.lines:
            if abs(line.dominant_size - size) <= 0.6 and line.text.strip():
                c[round(line.x0)] += 1
    return float(c.most_common(1)[0][0]) if c else 0.0

def _margin_repeats(pages: List[Page]) -> Counter:
    c = Counter()
    for p in pages:
        top = p.height * 0.14
        bottom = p.height * 0.86
        for line in p.lines:
            txt = re.sub(r"\s+", " ", line.text.strip().lower())
            if not txt:
                continue
            if line.y1 <= top or line.y0 >= bottom:
                txt = re.sub(r"\d+", "#", txt)
                c[txt] += 1
    return c

def classify_pages(pages: List[Page], stats_seed: Optional[Tuple[float,float,float]] = None) -> DocumentStatistics:
    body = stats_seed[0] if stats_seed else _body_size(pages)
    line_h = stats_seed[1] if stats_seed else _line_height(pages)
    left = stats_seed[2] if stats_seed else _body_left(pages, body)
    repeats = _margin_repeats(pages)
    sizes = Counter(round(s.size, 1) for p in pages for l in p.lines for s in l.spans)
    classifications: List[PageClassification] = []

    for p in pages:
        txt = _text(p)
        words = _WORDS.findall(txt)
        ev: List[Evidence] = []

        if p.number == 0 and (len(p.images) or len(words) < 120):
            ev.append(Evidence("first-page-cover", 0.45))

        if p.number < 4 and _TOC.search(txt):
            ev.append(Evidence("contents-keyword", 0.85))

        if p.number < 6 and _COPYRIGHT.search(txt):
            ev.append(Evidence("copyright-keyword", 0.75))

        if _INDEX.match(txt):
            ev.append(Evidence("index-heading", 0.95))

        if len(p.lines) and sum(1 for l in p.lines if l.dominant_size > body * 1.35) >= 1:
            ev.append(Evidence("large-heading", 0.55))

        short_lines = sum(1 for l in p.lines if len(l.text.split()) <= 4)
        if len(p.lines) >= 10 and short_lines / max(1, len(p.lines)) > 0.45:
            ev.append(Evidence("many-short-lines", 0.25))

        if any(_BIB.match(l.text.strip()) for l in p.lines[:8]):
            ev.append(Evidence("bibliography-heading", 0.95))

        if p.images and any(im.width > p.width * 0.55 and im.height > p.height * 0.25 for im in p.images):
            ev.append(Evidence("large-figure", 0.5))

        if not words and p.images:
            ev.append(Evidence("image-only", 0.7))

        candidates = [
            (PageType.COVER, sum(e.weight for e in ev if e.signal == "first-page-cover")),
            (PageType.CONTENTS, sum(e.weight for e in ev if e.signal == "contents-keyword") +
             (0.25 if "many-short-lines" in {e.signal for e in ev} else 0)),
            (PageType.COPYRIGHT, sum(e.weight for e in ev if e.signal == "copyright-keyword")),
            (PageType.INDEX, sum(e.weight for e in ev if e.signal == "index-heading")),
            (PageType.BIBLIOGRAPHY, sum(e.weight for e in ev if e.signal == "bibliography-heading")),
            (PageType.CHAPTER_OPENING, sum(e.weight for e in ev if e.signal == "large-heading")),
            (PageType.TABLE_FIGURE, sum(e.weight for e in ev if e.signal == "large-figure" and e.signal != "first-page-cover")),
        ]
        page_type, score = max(candidates, key=lambda x: x[1])
        if score < 0.55:
            page_type = PageType.PROSE
            score = 0.55
        score = min(0.99, score)
        classifications.append(PageClassification(p.number, page_type, score, ev))

    return DocumentStatistics(
        body_size=body,
        line_height=line_h,
        body_left=left,
        common_sizes=[s for s, _ in sizes.most_common(6)],
        recurring_margin_text=repeats,
        classifications=classifications,
    )
