"""Footnote detection and pairing.

Two halves must be found and matched:
  1. inline *reference markers* in the body (usually superscript digits), and
  2. the *note bodies* in the small-type block at the foot of the page.

Born-digital PDFs often omit the superscript flag, so markers are also detected
geometrically: a smaller span whose baseline sits above the line's baseline.
Labels frequently run straight into the note text ("1The case is…"), so the
label parser does not require whitespace after the number.
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass
from typing import List, Optional, Tuple

from .model import Line, Span
from .text import normalize

# A label is 1-3 digits *not* followed by another digit (so "1972" is not a
# label), or a footnote symbol. Trailing "." / ")" and the space are optional.
_LABEL_RE = re.compile(r"^\s*(\d{1,3}(?!\d)|[\*†‡§¶])[.\)]?\s*(.+)$", re.DOTALL)
_MARKER_TEXT = re.compile(r"^[\*†‡§¶]?\d{0,3}[\*†‡§¶]?$")


@dataclass
class NoteBody:
    label: str
    text: str


# --------------------------------------------------------------------------- #
# Reference markers
# --------------------------------------------------------------------------- #

def _line_metrics(line: Line) -> Tuple[float, float]:
    """(dominant size, baseline) of a line's main text.

    Measured against the line itself, not the document body size: a note or
    bibliography section may be set several points smaller than body text, and
    comparing its spans to the document size would make every one of them look
    like a superscript. The baseline is the *most common* origin among
    dominant-size spans -- taking the maximum lets a fraction's subscript drag
    the baseline below the real one.
    """
    real = [s for s in line.spans if s.text.strip()]
    if not real:
        return 0.0, line.y1
    dom = max(real, key=lambda s: len(s.text)).size
    at_dom = [s for s in real if abs(s.size - dom) <= 0.3]
    if not at_dom:
        return dom, line.y1
    origins = Counter(round(s.origin[1], 1) for s in at_dom)
    return dom, origins.most_common(1)[0][0]


def _is_raised(span: Span, line: Line) -> bool:
    """Smaller type sitting above the line's own baseline → a superscript."""
    dom, base = _line_metrics(line)
    if dom <= 0 or span.size > dom - 0.5:
        return False
    return span.origin[1] <= base - dom * 0.12


def _in_fraction(line: Line, idx: int) -> bool:
    """Is this raised digit the numerator of a fraction (5 1/2), not a marker?"""
    nxt = next((s.text.strip() for s in line.spans[idx + 1:] if s.text.strip()), "")
    if nxt[:1] in ("\u2044", "/"):
        return True
    prev = "".join(s.text for s in line.spans[:idx]).rstrip()
    # A marker follows a word or punctuation; a digit butted against another
    # digit is part of a number, an exponent or a fraction.
    return prev[-1:].isdigit()


def find_markers(line: Line, body_size: float = 0.0) -> List[Tuple[int, str]]:
    """Return (span_index, label) for footnote reference markers on a line."""
    markers: List[Tuple[int, str]] = []
    for idx, span in enumerate(line.spans):
        t = span.text.strip()
        if not t or not _MARKER_TEXT.match(t):
            continue
        has_digit = any(c.isdigit() for c in t)
        is_symbol = t in ("*", "†", "‡", "§", "¶")
        if not (has_digit or is_symbol):
            continue
        if not (span.superscript or _is_raised(span, line)):
            continue
        if has_digit and _in_fraction(line, idx):
            continue
        markers.append((idx, _norm_label(t)))
    return markers


# --------------------------------------------------------------------------- #
# Note bodies
# --------------------------------------------------------------------------- #

def _label_from_spans(line: Line, body_size: float) -> Optional[Tuple[str, str]]:
    """If the line opens with a raised/smaller number span, split it off."""
    spans = [s for s in line.spans if s.text.strip()]
    if not spans:
        return None
    first = spans[0]
    t = first.text.strip()
    if not t[:1].isdigit() and t[:1] not in "*†‡§¶":
        return None
    dom, _ = _line_metrics(line)
    small = dom > 0 and first.size <= dom - 0.5
    if not (first.superscript or small):
        return None
    if not _MARKER_TEXT.match(t):
        return None
    rest = "".join(s.text for s in line.spans[line.spans.index(first) + 1:])
    return _norm_label(t), rest.strip()


def parse_page_notes(note_lines: List[Line], body_size: float = 0.0) -> List[NoteBody]:
    """Group footnote-zone lines into individual notes keyed by label.

    Note lists are usually set with a hanging indent: the label sits at the
    block's left edge and continuation lines are indented. Where that shape is
    present the geometry decides what opens a note, which is far more reliable
    than the numbering (and works on a continuation page, where the first label
    is not 1). Otherwise we fall back to requiring the count to advance.
    """
    if not note_lines:
        return []
    xs = [ln.x0 for ln in note_lines]
    lo, hi = min(xs), max(xs)
    # Label lines and continuation lines form two indent clusters; split them
    # at the midpoint. A fixed tolerance off the left edge does not work,
    # because wider labels hang further left ("10" starts left of "1").
    hanging = (hi - lo) >= 6.0
    label_max_x = lo + (hi - lo) * 0.5

    notes: List[NoteBody] = []
    cur_label: Optional[str] = None
    cur_parts: List[str] = []
    last_num: Optional[int] = None

    def flush() -> None:
        nonlocal cur_label, cur_parts
        if cur_label is not None:
            notes.append(
                NoteBody(label=cur_label, text=normalize(_dehyphenate_join(cur_parts).strip()))
            )
        cur_label, cur_parts = None, []

    for line in note_lines:
        txt = line.text.strip()
        if not txt:
            continue
        split = _label_from_spans(line, body_size)
        if split is None:
            m = _LABEL_RE.match(txt)
            split = (_norm_label(m.group(1)), m.group(2)) if m else None
        opens = False
        if split is not None:
            opens = (line.x0 <= label_max_x) if hanging else _starts_note(split[0], last_num)
        if opens:
            flush()
            cur_label, cur_parts = split[0], [split[1]]
            if cur_label.isdigit():
                last_num = int(cur_label)
        elif cur_label is not None:
            cur_parts.append(txt)  # continuation line of the current note
    flush()
    return [n for n in notes if n.text]


def _starts_note(label: str, last_num: Optional[int]) -> bool:
    """Footnote labels run in sequence, so a number that does not advance the
    count is text inside the current note, not the start of a new one."""
    if not label.isdigit():
        return True  # symbols (*, †) always start a note
    if last_num is None:
        return True  # first label of a block (may be a continuation page)
    # Must advance the count, and only by a little: a page reference that
    # happens to start a line ("324. 27 Aquinas...") is not note 324.
    return last_num < int(label) <= last_num + 3


def _norm_label(label: str) -> str:
    return re.sub(r"[()\.\)]", "", label).strip()


def _dehyphenate_join(parts: List[str]) -> str:
    out = ""
    for part in parts:
        part = part.strip()
        if not part:
            continue
        if out.endswith("-") and part[:1].islower():
            out = out[:-1] + part
        elif out:
            out += " " + part
        else:
            out = part
    return out
