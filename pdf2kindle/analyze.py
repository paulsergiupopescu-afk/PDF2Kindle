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
_ENDNOTE_HEAD = re.compile(r"^\s*(notes|endnotes|notes to chapter\s*\d*)\s*$", re.IGNORECASE)
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

def _is_debris(line: Line) -> bool:
    """Is this whole line a stray mark rather than text?

    A speck on a scanned page, a printer's rule, or the edge of a neighbouring
    sheet comes back as a line holding a single glyph -- "Λ", "■", "·" --
    wherever on the page the mark happened to fall. Two things go wrong if it
    is kept: it reads as gibberish, and where the mark landed between the two
    halves of a word broken over a line end it stops them from being rejoined,
    because what follows the hyphen is then the mark instead of the rest of
    the word.

    Deliberately narrow. A digit is left alone -- a folio is dealt with in the
    margins, where its position is the evidence -- and so is anything holding
    a Latin letter, which may be a list marker or a drop cap.
    """
    txt = line.text.strip()
    if not txt or len(txt) > 2:
        return False
    return not any(c.isdigit() or ("a" <= c.lower() <= "z") for c in txt)


def _is_furniture(
    line: Line,
    neighbour: Optional[Line],
    *,
    at_top: bool,
    height: float,
    body_size: float,
    line_height: float,
    repeats: Counter,
    page_has_body: bool = True,
) -> bool:
    """Is this margin line a running head / folio rather than real content?"""
    txt = line.text.strip()
    if not txt:
        return True

    in_band = line.y1 <= height * _TOP_BAND if at_top else line.y0 >= height * _BOT_BAND
    if not in_band:
        return False

    # A bare folio ("12", "xiv", "[3]") -- checked before the "larger than
    # body text" guard below, because a page number is routinely set a
    # point or two bigger than body text for its own visual styling (a
    # book's own choice, not a signal of anything else), and that guard
    # would otherwise mistake it for a heading and leave it standing in the
    # text. Capped well under the ratio (1.8) structure.py's own heading
    # detection requires of a *real* bare-number heading (a chapter number
    # standing alone above its title), so an actual chapter-opening numeral
    # is never at risk of being read as a folio instead.
    if _DIGITS_ONLY.match(txt) and len(txt) <= 12:
        ratio = line.dominant_size / body_size if body_size else 1.0
        if ratio < 1.8:
            return True

    # Never strip something set larger than body text — that's a real heading.
    if line.dominant_size > body_size + 0.3:
        return False

    # Set noticeably smaller than body text, at the *top* margin: a running
    # head's defining trait, and one no genuine heading shares (a heading is
    # never smaller than body text). Top only -- the bottom margin is exactly
    # where a real footnote block legitimately lives, in exactly this size
    # range, and must reach _split_body_notes rather than being discarded
    # here. Catches a scan's running head even when OCR noise makes this
    # specific occurrence read differently from every other one, defeating
    # both the repeat count below and the word-count gap check after it -- a
    # real risk on a noisily-scanned page, where the same printed header can
    # come out as a different garbled string each time.
    # ...but only on a page that has body-size text for it to head. A
    # bibliography or an endnotes page is set *entirely* below body size, so
    # this test matches its first real line just as well as a running head,
    # and silently eats the top of the section -- several reference entries
    # per page. Where nothing on the page is body size, there is no body for
    # a running head to sit above, and the genuine head is still caught by
    # the repeat count below.
    if at_top and line.dominant_size <= body_size - 1.5 and page_has_body:
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
    # Does anything on this page sit at body size? If not, the page is a
    # dedicated small-type section (references, endnotes) rather than body
    # text under a running head -- see _is_furniture.
    page_has_body = any(ln.dominant_size >= body_size - 0.3 for ln in kept)
    for _ in range(_MAX_STRIP):
        if len(kept) < 2:
            break
        if _is_furniture(kept[0], kept[1], at_top=True, height=height, body_size=body_size,
                         line_height=line_height, repeats=repeats,
                         page_has_body=page_has_body):
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
        if ln.dominant_size <= body_size - 0.3 and ln.y0 >= height * 0.35:
            notes.append(ln)
            i -= 1
        else:
            break
    notes.reverse()
    if not notes:
        return ordered, []

    # The block must open with a note label -- but body content set at
    # footnote size (a block quote, an epigraph) can sit directly above the
    # real footnotes and get swept into the same candidate run, pushing a
    # non-marker line to the top. Trim down to the first line that actually
    # opens a note, rather than discarding the whole run (and the genuine
    # footnotes still in it) over a false start above it.
    start = next((i for i, ln in enumerate(notes) if _starts_with_marker(ln, body_size)), None)
    if start is None:
        return ordered, []
    demoted, notes = notes[:start], notes[start:]

    body = ordered[: len(ordered) - len(notes) - len(demoted)] + demoted
    # A real footnote zone sits *under* body text. A page that is small type
    # all the way up is a dedicated endnote/reference page, which belongs to
    # the endnote handler — peeling it here would split it in half.
    if sum(1 for ln in body if ln.dominant_size >= body_size - 0.3) < 2:
        return ordered, []
    return body, notes


def _mark_endnote_sections(contents: List[PageContent], body_size: float) -> None:
    """Move chapter-end "Notes" sections out of the body and into note lines.

    Endnotes are set as a hanging-indent list, which paragraph grouping cannot
    reconstruct (a label line looks like a continuation of the indented line
    above it). Capturing them here, as lines, lets the note parser pair labels
    with their bodies directly. A section runs from the "Notes" heading until a
    heading-sized line -- normally the start of the next chapter.
    """
    in_notes = False
    for pc in contents:
        if in_notes and pc.body_lines:
            cut = len(pc.body_lines)
            for i, ln in enumerate(pc.body_lines):
                if ln.dominant_size > body_size + 0.5:
                    cut, in_notes = i, False
                    break
            pc.note_lines = pc.body_lines[:cut] + pc.note_lines
            pc.body_lines = pc.body_lines[cut:]
        if not pc.body_lines:
            continue
        for i, ln in enumerate(pc.body_lines):
            if _ENDNOTE_HEAD.match(ln.text.strip()) and ln.dominant_size >= body_size:
                pc.note_lines = pc.body_lines[i + 1:] + pc.note_lines
                pc.body_lines = pc.body_lines[:i]  # drop the heading itself
                in_notes = True
                break


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
        lines = [ln for ln in p.lines if ln.text.strip() and not _is_debris(ln)]
        kept = _strip_furniture(lines, p.height, body_size, line_height, repeats)
        ordered = _order_lines(kept, p.width)
        body_lines, note_lines = _split_body_notes(ordered, body_size, p.height, line_height)
        out.pages.append(
            PageContent(
                number=p.number, width=p.width, height=p.height, ocr=p.ocr,
                body_lines=body_lines, note_lines=note_lines,
            )
        )
    _mark_endnote_sections(out.pages, body_size)
    return out
