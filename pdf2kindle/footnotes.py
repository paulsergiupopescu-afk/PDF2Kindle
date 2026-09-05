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
from dataclasses import dataclass
from typing import List, Optional, Tuple

from .model import Line, Span

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

def _baseline(line: Line, body_size: float) -> float:
    """Baseline of the line's main text (largest run of near-body-size spans)."""
    cands = [s for s in line.spans if s.text.strip() and s.size >= body_size - 0.6]
    if not cands:
        cands = [s for s in line.spans if s.text.strip()]
    if not cands:
        return line.y1
    return max(s.origin[1] for s in cands)


def _is_raised(span: Span, line: Line, body_size: float) -> bool:
    """Smaller type sitting above the line's baseline → a superscript."""
    if span.size > body_size - 0.5:
        return False
    base = _baseline(line, body_size)
    return span.origin[1] <= base - body_size * 0.12


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
        raised = span.superscript or (body_size > 0 and _is_raised(span, line, body_size))
        if raised:
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
    small = body_size > 0 and first.size <= body_size - 0.5
    if not (first.superscript or small):
        return None
    if not _MARKER_TEXT.match(t):
        return None
    rest = "".join(s.text for s in line.spans[line.spans.index(first) + 1:])
    return _norm_label(t), rest.strip()


def parse_page_notes(note_lines: List[Line], body_size: float = 0.0) -> List[NoteBody]:
    """Group footnote-zone lines into individual notes keyed by label."""
    notes: List[NoteBody] = []
    cur_label: Optional[str] = None
    cur_parts: List[str] = []
    last_num: Optional[int] = None

    def flush() -> None:
        nonlocal cur_label, cur_parts
        if cur_label is not None:
            notes.append(NoteBody(label=cur_label, text=_dehyphenate_join(cur_parts).strip()))
        cur_label, cur_parts = None, []

    for line in note_lines:
        txt = line.text.strip()
        if not txt:
            continue
        split = _label_from_spans(line, body_size)
        if split is None:
            m = _LABEL_RE.match(txt)
            split = (_norm_label(m.group(1)), m.group(2)) if m else None
        if split is not None and _starts_note(split[0], last_num):
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
        return True
    return int(label) > last_num


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
