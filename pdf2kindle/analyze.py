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
from .text import CHAPTER_LABEL_RE

_NOTE_START = re.compile(r"^\s*(?:[\*†‡§¶]|\(?\d{1,3}\)?[.\)]?)(?:\s|$)")
_ENDNOTE_HEAD = re.compile(r"^\s*(notes|endnotes|notes to chapter\s*\d*)\s*$", re.IGNORECASE)
_CAPTION_START = re.compile(r"^\s*(figure|fig\.|table|plate|map|chart)\s*\d", re.IGNORECASE)
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


_EDGE_ARABIC = re.compile(r"^(\d{1,4})\s+\S|\S\s+(\d{1,4})$")
_EDGE_ROMAN = re.compile(r"^([ivxlc]{1,7})\s+\S|\S\s+([ivxlc]{1,7})$")
_ROMAN_VALUES = {"i": 1, "v": 5, "x": 10, "l": 50, "c": 100}
# Margin lines carrying a folio needed to trust a page-number offset.
_FOLIO_MIN = 3


def _roman_value(text: str) -> int:
    total = 0
    for i, ch in enumerate(text):
        v = _ROMAN_VALUES[ch]
        nxt = _ROMAN_VALUES[text[i + 1]] if i + 1 < len(text) else 0
        total += -v if v < nxt else v
    return total


def _edge_folios(text: str) -> List[tuple]:
    """("arabic"|"roman", value) for a number at either end of a margin line."""
    t = text.strip()
    found = []
    if _DIGITS_ONLY.match(t):
        t = t.strip("[]().-–— ")
        if t.isdigit():
            return [("arabic", int(t))]
        if t and all(c in _ROMAN_VALUES for c in t.lower()):
            return [("roman", _roman_value(t.lower()))]
        return []
    m = _EDGE_ARABIC.search(t)
    if m:
        found.append(("arabic", int(m.group(1) or m.group(2))))
    m = _EDGE_ROMAN.search(t)
    if m:
        found.append(("roman", _roman_value(m.group(1) or m.group(2))))
    return found


def _folio_offsets(pages: List[Page]) -> dict:
    """The constant (PDF page index - printed page number), per numbering style.

    A running head that carries its page's folio ("Classic teaching on
    original sin 13") is the same text on at most a page or two when its
    chapter is short, so counting repeats never catches it. Its folio,
    though, follows the book's page numbering exactly -- and that numbering,
    learned from every folio in the margins, identifies it with certainty.
    """
    counts: dict = {"arabic": Counter(), "roman": Counter()}
    for p in pages:
        if not p.lines or p.height <= 0:
            continue
        top, bot = p.height * _TOP_BAND, p.height * _BOT_BAND
        for line in p.lines:
            if line.y1 <= top or line.y0 >= bot:
                for style, value in _edge_folios(line.text):
                    counts[style][p.number - value] += 1
    offsets = {}
    for style, counter in counts.items():
        if counter:
            offset, n = counter.most_common(1)[0]
            if n >= _FOLIO_MIN:
                offsets[style] = offset
    return offsets


def _carries_folio(text: str, page_number: int, offsets: dict) -> bool:
    return any(
        style in offsets and page_number - offsets[style] == value
        for style, value in _edge_folios(text)
    )


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
    page_number: int = -1,
    folio_offsets: Optional[dict] = None,
) -> bool:
    """Is this margin line a running head / folio rather than real content?"""
    txt = line.text.strip()
    if not txt:
        return True

    in_band = line.y1 <= height * _TOP_BAND if at_top else line.y0 >= height * _BOT_BAND
    if not in_band:
        return False

    # A short line holding exactly this page's printed number at one end is
    # a running head with its folio -- see _folio_offsets. Not when larger
    # than body text: a chapter title can end in a number too.
    if (folio_offsets and len(txt.split()) <= 12
            and line.dominant_size <= body_size + 0.3
            and _carries_folio(txt, page_number, folio_offsets)):
        return True

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

    # A "CHAPTER FOUR" label over a chapter title is small, short and set
    # apart at the top of the page -- everything a running head is, except
    # repeated from page to page.
    if CHAPTER_LABEL_RE.match(txt) and repeats.get(_normalize_running(txt), 0) < _REPEAT_MIN:
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
    # Unless the line opens a block of equally small type right below it:
    # that is the top of a page of endnotes, not a head over body text.
    small_block = (
        neighbour is not None
        and abs(neighbour.dominant_size - line.dominant_size) < 0.5
        and neighbour.y0 - line.y1 < line_height
    )
    if at_top and line.dominant_size <= body_size - 1.5 and not small_block:
        return True

    # Repeats elsewhere in the margins → running head/foot. This catches
    # per-chapter heads ("Introduction") that a whole-book ratio would miss.
    if repeats.get(_normalize_running(txt), 0) >= _REPEAT_MIN:
        return True

    # Otherwise: short, and set off from the text block by a clear gap --
    # but a figure caption set at the foot of the page is that shape too.
    if neighbour is not None and len(txt.split()) <= 10 and not _CAPTION_START.match(txt):
        gap = (neighbour.y0 - line.y1) if at_top else (line.y0 - neighbour.y1)
        if gap >= line_height * 1.4:
            return True
    return False


def _strip_furniture(
    lines: List[Line], height: float, body_size: float, line_height: float, repeats: Counter,
    page_number: int = -1, folio_offsets: Optional[dict] = None,
) -> List[Line]:
    kept = sorted(lines, key=lambda ln: ln.y0)
    for _ in range(_MAX_STRIP):
        if len(kept) < 2:
            break
        if _is_furniture(kept[0], kept[1], at_top=True, height=height, body_size=body_size,
                         line_height=line_height, repeats=repeats,
                         page_number=page_number, folio_offsets=folio_offsets):
            kept = kept[1:]
        else:
            break
    for _ in range(_MAX_STRIP):
        if len(kept) < 2:
            break
        if _is_furniture(kept[-1], kept[-2], at_top=False, height=height, body_size=body_size,
                         line_height=line_height, repeats=repeats,
                         page_number=page_number, folio_offsets=folio_offsets):
            kept = kept[:-1]
        else:
            break
    return kept


# --------------------------------------------------------------------------- #
# Reading order
# --------------------------------------------------------------------------- #

# A gutter between two columns is at least this wide (points), and both
# columns hold at least this many lines.
_GUTTER_MIN = 8.0
_COLUMN_MIN_LINES = 4


def _find_gutter(lines: List[Line], width: float) -> Optional[float]:
    """x-position of an empty vertical channel splitting the page in two.

    Measured on the spans themselves, not on whole lines: where two columns
    of short entries (an index) sit at the same height, the extractor joins
    the left and right entries into one line, and only the run of empty
    space between their spans still shows the columns apart.
    """
    if width <= 0:
        return None
    covered = bytearray(int(width) + 1)
    for ln in lines:
        for sp in ln.spans:
            if not sp.text.strip():
                continue
            a, b = max(0, int(sp.bbox[0])), min(int(width), int(sp.bbox[2]))
            covered[a:b + 1] = b"\x01" * max(0, b + 1 - a)
    # The widest uncovered run in the middle of the page.
    best, best_len, run_start = None, 0.0, None
    lo, hi = int(width * 0.3), int(width * 0.7)
    for x in range(lo, hi + 2):
        empty = x <= hi and not covered[x]
        if empty and run_start is None:
            run_start = x
        elif not empty and run_start is not None:
            if x - run_start > best_len:
                best, best_len = (run_start + x) / 2.0, x - run_start
            run_start = None
    if best is None or best_len < _GUTTER_MIN:
        return None
    left = sum(1 for ln in lines if any(sp.bbox[2] <= best for sp in ln.spans if sp.text.strip()))
    right = sum(1 for ln in lines if any(sp.bbox[0] >= best for sp in ln.spans if sp.text.strip()))
    if left < _COLUMN_MIN_LINES or right < _COLUMN_MIN_LINES:
        return None
    return best


def _split_at(line: Line, x: float) -> List[Line]:
    """Split a line into its parts either side of *x* (spans never cross it)."""
    parts = []
    for side in ([sp for sp in line.spans if sp.bbox[2] <= x],
                 [sp for sp in line.spans if sp.bbox[2] > x]):
        if any(sp.text.strip() for sp in side):
            bbox = (min(sp.bbox[0] for sp in side), min(sp.bbox[1] for sp in side),
                    max(sp.bbox[2] for sp in side), max(sp.bbox[3] for sp in side))
            parts.append(Line(spans=side, bbox=bbox))
    return parts


def _order_lines(lines: List[Line], width: float) -> List[Line]:
    if len(lines) < 6:
        return sorted(lines, key=lambda ln: (round(ln.y0, 1), ln.x0))
    gutter = _find_gutter(lines, width)
    if gutter is not None:
        parts = [part for ln in lines for part in _split_at(ln, gutter)]
        left = [ln for ln in parts if ln.x1 <= gutter]
        right = [ln for ln in parts if ln.x0 >= gutter]
        return (sorted(left, key=lambda ln: (round(ln.y0, 1), ln.x0))
                + sorted(right, key=lambda ln: (round(ln.y0, 1), ln.x0)))
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
    # A raised/smaller leading number is a note label even without a space --
    # but not a number that runs on into a bracket, dash or comma: that is
    # the tail of a reference broken over the line ("Amos 9:1–" / "16] are
    # not even about God"), in a block quote set at note size.
    text = line.text.strip()
    digits = len(text) - len(text.lstrip("0123456789"))
    return (
        (first.superscript or first.size <= body_size - 1.0)
        and digits > 0
        and text[digits:digits + 1] not in tuple("]–-,:;/)")
    )


def _split_body_notes(
    lines: List[Line], body_size: float, height: float, line_height: float
) -> tuple[List[Line], List[Line]]:
    """Peel a trailing smaller-type footnote block off the bottom of the page."""
    if not lines:
        return [], []
    # Already in reading order (see _order_lines). Re-sorting by height here
    # would interleave the two columns of a two-column page line by line.
    ordered = list(lines)

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
    heading-sized line or a chapter label -- normally the start of the next
    chapter.
    """
    in_notes = False
    for pc in contents:
        if in_notes and pc.body_lines:
            cut = len(pc.body_lines)
            for i, ln in enumerate(pc.body_lines):
                # The next chapter: its title, or the small "CHAPTER FOUR"
                # label set above it.
                if ln.dominant_size > body_size + 0.5 or CHAPTER_LABEL_RE.match(ln.text.strip()):
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
    folio_offsets = _folio_offsets(pages)

    out = Analyzed(body_size=body_size, line_height=line_height, body_left=body_left)
    for p in pages:
        lines = [ln for ln in p.lines if ln.text.strip() and not _is_debris(ln)]
        kept = _strip_furniture(lines, p.height, body_size, line_height, repeats,
                                page_number=p.number, folio_offsets=folio_offsets)
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
