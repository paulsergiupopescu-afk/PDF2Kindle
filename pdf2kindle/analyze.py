"""Layout analysis: turn raw extracted geometry into clean, ordered text.

Responsibilities:
  * estimate the dominant body-text font size and left margin,
  * order lines into human reading order, including simple 2-column handling,
  * detect and strip page furniture (running heads, folios, footers),
  * split each page into body lines and a trailing footnote zone.
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field
from statistics import median
from typing import List, Optional

from .model import Line, Page

_NOTE_START = re.compile(r"^\s*(?:[\*†‡§¶]|\(?\d{1,3}\)?[.\)]?)(?:\s|$)")
_DIGITS_ONLY = re.compile(r"^[\dIVXLCivxlc\s\.\-–—\[\]]+$")

# How far into the page counts as the header/footer band.
_TOP_BAND = 0.14
_BOT_BAND = 0.86
# A margin line repeating at least this many times anywhere is furniture.
_REPEAT_MIN = 3
# At most this many lines are peeled off each end of a page.
_MAX_STRIP = 3


@dataclass
class PageContent:
    number: int
    width: float
    height: float
    ocr: bool
    body_lines: List[Line] = field(default_factory=list)
    note_lines: List[Line] = field(default_factory=list)


@dataclass
class Analyzed:
    body_size: float
    line_height: float
    body_left: float = 0.0
    pages: List[PageContent] = field(default_factory=list)


# --------------------------------------------------------------------------- #
# Document-wide statistics
# --------------------------------------------------------------------------- #

def _dominant_body_size(pages: List[Page]) -> float:
    counter: Counter = Counter()
    for p in pages:
        for line in p.lines:
            for s in line.spans:
                counter[s.size] += len(s.text.strip())
    return counter.most_common(1)[0][0] if counter else 11.0


def _median_line_height(pages: List[Page]) -> float:
    heights = [line.height for p in pages for line in p.lines if line.height > 0]
    return median(heights) if heights else 12.0


def _dominant_left(pages: List[Page], body_size: float) -> float:
    counter: Counter = Counter()
    for p in pages:
        for line in p.lines:
            if abs(line.dominant_size - body_size) <= 0.6 and line.text.strip():
                counter[round(line.x0)] += 1
    return float(counter.most_common(1)[0][0]) if counter else 0.0


def _normalize_running(text: str) -> str:
    """Collapse digits so page-varying folios still match across pages."""
    t = re.sub(r"\d+", "#", text.strip().lower())
    return re.sub(r"\s+", " ", t)


def _margin_repeats(pages: List[Page]) -> Counter:
    """Count normalized text appearing in the top/bottom bands across the book."""
    counter: Counter = Counter()
    for p in pages:
        if not p.lines or p.height <= 0:
            continue
        top, bot = p.height * _TOP_BAND, p.height * _BOT_BAND
        for line in p.lines:
            txt = line.text.strip()
            if txt and (line.y1 <= top or line.y0 >= bot):
                counter[_normalize_running(txt)] += 1
    return counter


# --------------------------------------------------------------------------- #
# Page furniture
# --------------------------------------------------------------------------- #

def _is_furniture(
    line: Line,
    neighbour: Optional[Line],
    *,
    at_top: bool,
    height: float,
    body_size: float,
    line_height: float,
    repeats: Counter,
) -> bool:
    """Is this margin line a running head / folio rather than real content?"""
    txt = line.text.strip()
    if not txt:
        return True

    in_band = line.y1 <= height * _TOP_BAND if at_top else line.y0 >= height * _BOT_BAND
    if not in_band:
        return False

    # Never strip something set larger than body text — that's a real heading.
    if line.dominant_size > body_size + 0.3:
        return False

    # A bare folio ("12", "xiv", "[3]").
    if _DIGITS_ONLY.match(txt) and len(txt) <= 12:
        return True

    # Repeats elsewhere in the margins → running head/foot. This catches
    # per-chapter heads ("Introduction") that a whole-book ratio would miss.
    if repeats.get(_normalize_running(txt), 0) >= _REPEAT_MIN:
        return True

    # Otherwise: short, and set off from the text block by a clear gap.
    if neighbour is not None and len(txt.split()) <= 10:
        gap = (neighbour.y0 - line.y1) if at_top else (line.y0 - neighbour.y1)
        if gap >= line_height * 1.4:
            return True
    return False


def _strip_furniture(
    lines: List[Line], height: float, body_size: float, line_height: float, repeats: Counter
) -> List[Line]:
    kept = sorted(lines, key=lambda ln: ln.y0)
    for _ in range(_MAX_STRIP):
        if len(kept) < 2:
            break
        if _is_furniture(kept[0], kept[1], at_top=True, height=height, body_size=body_size,
                         line_height=line_height, repeats=repeats):
            kept = kept[1:]
        else:
            break
    for _ in range(_MAX_STRIP):
        if len(kept) < 2:
            break
        if _is_furniture(kept[-1], kept[-2], at_top=False, height=height, body_size=body_size,
                         line_height=line_height, repeats=repeats):
            kept = kept[:-1]
        else:
            break
    return kept


# --------------------------------------------------------------------------- #
# Reading order
# --------------------------------------------------------------------------- #

def _order_lines(lines: List[Line], width: float) -> List[Line]:
    if len(lines) < 6:
        return sorted(lines, key=lambda ln: (round(ln.y0, 1), ln.x0))
    mid = width / 2.0
    left = [ln for ln in lines if ln.x1 <= mid + width * 0.03]
    right = [ln for ln in lines if ln.x0 >= mid - width * 0.03]
    crossing = [ln for ln in lines if ln not in left and ln not in right]
    if (
        len(left) >= 4 and len(right) >= 4
        and len(crossing) <= 0.15 * len(lines)
        and 0.4 <= len(left) / (len(left) + len(right)) <= 0.6
    ):
        return (sorted(left, key=lambda ln: (round(ln.y0, 1), ln.x0))
                + sorted(right, key=lambda ln: (round(ln.y0, 1), ln.x0)))
    return sorted(lines, key=lambda ln: (round(ln.y0, 1), ln.x0))


# --------------------------------------------------------------------------- #
# Footnote zone
# --------------------------------------------------------------------------- #

def _starts_with_marker(line: Line, body_size: float) -> bool:
    if _NOTE_START.match(line.text):
        return True
    first = next((s for s in line.spans if s.text.strip()), None)
    if first is None:
        return False
    # A raised/smaller leading number is a note label even without a space.
    return (
        (first.superscript or first.size <= body_size - 1.0)
        and first.text.strip()[:1].isdigit()
    )


def _split_body_notes(
    lines: List[Line], body_size: float, height: float, line_height: float
) -> tuple[List[Line], List[Line]]:
    """Peel a trailing smaller-type footnote block off the bottom of the page."""
    if not lines:
        return [], []
    ordered = sorted(lines, key=lambda ln: ln.y0)

    notes: List[Line] = []
    i = len(ordered) - 1
    while i >= 0:
        ln = ordered[i]
        if ln.dominant_size <= body_size - 0.3 and ln.y0 >= height * 0.45:
            notes.append(ln)
            i -= 1
        else:
            break
    notes.reverse()
    if not notes:
        return ordered, []

    # The block must actually open with a note label. A gap alone is not
    # enough: captions and other small type also sit under the text block,
    # and swallowing them here would delete them from the book.
    if not _starts_with_marker(notes[0], body_size):
        return ordered, []
    return ordered[: len(ordered) - len(notes)], notes


# --------------------------------------------------------------------------- #
# Entry point
# --------------------------------------------------------------------------- #

def analyze(pages: List[Page]) -> Analyzed:
    body_size = _dominant_body_size(pages)
    line_height = _median_line_height(pages)
    body_left = _dominant_left(pages, body_size)
    repeats = _margin_repeats(pages)

    out = Analyzed(body_size=body_size, line_height=line_height, body_left=body_left)
    for p in pages:
        lines = [ln for ln in p.lines if ln.text.strip()]
        kept = _strip_furniture(lines, p.height, body_size, line_height, repeats)
        ordered = _order_lines(kept, p.width)
        body_lines, note_lines = _split_body_notes(ordered, body_size, p.height, line_height)
        out.pages.append(
            PageContent(
                number=p.number, width=p.width, height=p.height, ocr=p.ocr,
                body_lines=body_lines, note_lines=note_lines,
            )
        )
    return out
