"""Layout analysis: turn raw extracted geometry into clean, ordered text.

Responsibilities:
  * estimate the dominant body-text font size (used everywhere downstream),
  * order lines into human reading order, including simple 2-column handling,
  * detect and strip repeating running heads / footers / page numbers,
  * split each page into body lines and a trailing footnote zone.
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field
from statistics import median
from typing import List

from .model import Line, Page

_NOTE_START = re.compile(r"^\s*(?:[\*†‡§]|\(?\d{1,3}\)?[.\)]?)\s")
_DIGITS_ONLY = re.compile(r"^[\dIVXLCivxlc\s\.\-–—]+$")


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
    body_left: float = 0.0  # dominant left text margin (points)
    pages: List[PageContent] = field(default_factory=list)


def _dominant_body_size(pages: List[Page]) -> float:
    """Most common span size, weighted by number of characters."""
    counter: Counter = Counter()
    for p in pages:
        for line in p.lines:
            for s in line.spans:
                counter[s.size] += len(s.text.strip())
    if not counter:
        return 11.0
    return counter.most_common(1)[0][0]


def _median_line_height(pages: List[Page]) -> float:
    heights = [line.height for p in pages for line in p.lines if line.height > 0]
    return median(heights) if heights else 12.0


def _dominant_left(pages: List[Page], body_size: float) -> float:
    """Most common left edge of body-size lines — the body text margin."""
    counter: Counter = Counter()
    for p in pages:
        for line in p.lines:
            if abs(line.dominant_size - body_size) <= 0.6 and line.text.strip():
                counter[round(line.x0)] += 1
    if not counter:
        return 0.0
    return float(counter.most_common(1)[0][0])


def _normalize_running(text: str) -> str:
    """Collapse a header/footer candidate so page-varying digits don't defeat matching."""
    t = re.sub(r"\d+", "#", text.strip().lower())
    t = re.sub(r"\s+", " ", t)
    return t


def _detect_running_heads(pages: List[Page]) -> set:
    """Find normalized header/footer strings that repeat across many pages."""
    top_counter: Counter = Counter()
    bot_counter: Counter = Counter()
    n = len(pages)
    for p in pages:
        if not p.lines:
            continue
        top_zone = p.height * 0.08
        bot_zone = p.height * 0.92
        for line in p.lines:
            txt = line.text.strip()
            if not txt:
                continue
            if line.y1 <= top_zone:
                top_counter[_normalize_running(txt)] += 1
            elif line.y0 >= bot_zone:
                bot_counter[_normalize_running(txt)] += 1
    threshold = max(2, int(n * 0.25))
    running = set()
    for norm, count in list(top_counter.items()) + list(bot_counter.items()):
        if count >= threshold and norm:
            running.add(norm)
    return running


def _order_lines(lines: List[Line], width: float) -> List[Line]:
    """Reading order, with a conservative 2-column detector."""
    if len(lines) < 6:
        return sorted(lines, key=lambda ln: (round(ln.y0, 1), ln.x0))

    mid = width / 2.0
    left = [ln for ln in lines if ln.x1 <= mid + width * 0.03]
    right = [ln for ln in lines if ln.x0 >= mid - width * 0.03]
    crossing = [ln for ln in lines if ln not in left and ln not in right]

    # Treat as two columns only when the split is clean and balanced.
    if (
        len(left) >= 4
        and len(right) >= 4
        and len(crossing) <= 0.15 * len(lines)
        and 0.4 <= len(left) / (len(left) + len(right)) <= 0.6
    ):
        left_sorted = sorted(left, key=lambda ln: (round(ln.y0, 1), ln.x0))
        right_sorted = sorted(right, key=lambda ln: (round(ln.y0, 1), ln.x0))
        return left_sorted + right_sorted

    return sorted(lines, key=lambda ln: (round(ln.y0, 1), ln.x0))


def _split_body_notes(lines: List[Line], body_size: float, height: float) -> tuple[List[Line], List[Line]]:
    """Peel a trailing small-font footnote block off the bottom of the page."""
    if not lines:
        return [], []
    ordered = sorted(lines, key=lambda ln: ln.y0)
    note_start = height * 0.55  # notes only live in the lower part of the page

    # Walk up from the bottom collecting consecutive smaller-than-body lines.
    notes: List[Line] = []
    i = len(ordered) - 1
    while i >= 0:
        ln = ordered[i]
        if ln.dominant_size <= body_size - 0.6 and ln.y0 >= note_start:
            notes.append(ln)
            i -= 1
        else:
            break
    notes.reverse()

    if not notes:
        return ordered, []

    # Only accept as footnotes if the block actually opens with a note marker.
    if not _NOTE_START.match(notes[0].text):
        return ordered, []

    body = ordered[: len(ordered) - len(notes)]
    return body, notes


def analyze(pages: List[Page]) -> Analyzed:
    body_size = _dominant_body_size(pages)
    line_height = _median_line_height(pages)
    body_left = _dominant_left(pages, body_size)
    running = _detect_running_heads(pages)

    out = Analyzed(body_size=body_size, line_height=line_height, body_left=body_left)
    for p in pages:
        top_zone = p.height * 0.08
        bot_zone = p.height * 0.92
        kept: List[Line] = []
        for line in p.lines:
            txt = line.text.strip()
            if not txt:
                continue
            in_margin = line.y1 <= top_zone or line.y0 >= bot_zone
            if in_margin:
                if _normalize_running(txt) in running:
                    continue  # running head/foot
                if _DIGITS_ONLY.match(txt) and len(txt) <= 8:
                    continue  # bare page number
            kept.append(line)

        ordered = _order_lines(kept, p.width)
        body_lines, note_lines = _split_body_notes(ordered, body_size, p.height)
        out.pages.append(
            PageContent(
                number=p.number,
                width=p.width,
                height=p.height,
                ocr=p.ocr,
                body_lines=body_lines,
                note_lines=note_lines,
            )
        )
    return out
