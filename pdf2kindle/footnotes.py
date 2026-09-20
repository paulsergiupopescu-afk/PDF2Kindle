"""Footnote detection and pairing.

Two halves must be found and matched:
  1. inline *reference markers* in the body (usually superscript digits), and
  2. the *note bodies* in the small-type block at the foot of the page.

Born-digital PDFs often omit the superscript flag, so markers are also detected
geometrically: a smaller span whose baseline sits above the line's baseline.
Labels frequently run straight into the note text ("1The case is…"), so the
label parser does not require whitespace after the number.

A scanned-and-OCR'd PDF (ABBYY FineReader and similar) can lose superscript
positioning entirely during reconstruction: the marker survives only as a
digit fused into the surrounding word at ordinary body size and baseline
("exprimat2 (aşa cum...") with nothing geometric left to detect it by. See
find_embedded_markers, which recovers these by exact match against the
note labels already known to exist on the same page.
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass
from typing import List, Optional, Tuple

from .model import Line, Span
from .text import drop_break_hyphen, ends_hyphenated, normalize

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


# A digit run fused onto the end of a word, with no space before it, and
# followed by whitespace/punctuation/end-of-text -- not simply a number
# ("anul 49/50" has a space before "49" and is left alone). Matched only
# against note labels *already known* to exist on the same page, so an
# innocent number stuck to a word is never mistaken for a marker: the
# coincidence of an unrelated digit run exactly equalling an existing
# footnote's label, right where OCR would place that citation, does not
# happen by chance.
_EMBEDDED_MARKER_RE = re.compile(r"(?<=[^\W\d_])(\d{1,3})(?=[\s.,;:)\]\"'”’]|$)")


# A number the typesetter put in brackets ("[12]") is a reference marker
# stated as one. That is the difference from a bare digit, which has to be
# corroborated against labels already found on the page before it can be
# believed: a delimiter nobody writes by accident is its own evidence, so
# these are recognized wherever they appear, including in a document whose
# notes are all gathered at the very end and whose citing pages therefore
# have no footnote zone to corroborate against.
_BRACKET_MARKER_RE = re.compile(r"\[(\d{1,3})\]")


def find_bracket_markers(text: str) -> List[Tuple[int, int, str]]:
    """Return (start, end, label) for bracketed reference markers in *text*."""
    if not text:
        return []
    return [(m.start(), m.end(), m.group(1)) for m in _BRACKET_MARKER_RE.finditer(text)]


def find_embedded_markers(text: str, known_labels: set) -> List[Tuple[int, int, str]]:
    """Return (start, end, label) for markers OCR fused into body text.

    Only ever called with the labels already parsed from this same page's
    footnote zone -- see the module docstring.
    """
    if not known_labels or not text:
        return []
    return [
        (m.start(1), m.end(1), m.group(1))
        for m in _EMBEDDED_MARKER_RE.finditer(text)
        if m.group(1) in known_labels
    ]


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


# Two columns of notes are further apart than any indent within one.
_COLUMN_GAP = 40.0


def _label_split(line: Line, body_size: float):
    """The (label, rest) this line opens with, if it looks like one at all."""
    split = _label_from_spans(line, body_size)
    if split is not None:
        return split
    m = _LABEL_RE.match(line.text.strip())
    return (_norm_label(m.group(1)), m.group(2)) if m else None


def _modal(values: List[float]) -> float:
    """The position these lines share, to the nearest half point."""
    counts: Dict[float, int] = {}
    for v in values:
        key = round(v * 2) / 2
        counts[key] = counts.get(key, 0) + 1
    return max(counts.items(), key=lambda kv: (kv[1], -kv[0]))[0]


def _note_columns(note_lines: List[Line]) -> Dict[int, List[Line]]:
    """Group note lines by the column they are set in."""
    xs = sorted({round(ln.x0, 1) for ln in note_lines})
    bands: List[List[float]] = []
    for x in xs:
        if bands and x - bands[-1][-1] <= _COLUMN_GAP:
            bands[-1].append(x)
        else:
            bands.append([x])
    out: Dict[int, List[Line]] = {i: [] for i in range(len(bands))}
    for ln in note_lines:
        x = round(ln.x0, 1)
        for i, band in enumerate(bands):
            if band[0] - 0.05 <= x <= band[-1] + 0.05:
                out[i].append(ln)
                break
    return out


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

    # Which lines open a note is settled by where they sit -- but "where"
    # only means anything within one column. Measured across a two-column
    # notes section, the left edge of one column and the left edge of the
    # other are the two extremes, the midpoint between them falls in the
    # gutter, and every line in the right-hand column is "too far right" to
    # open anything: its notes simply vanish.
    columns = _note_columns(note_lines)

    # Nor is the label always the part that hangs left. Some houses indent
    # the label and set the continuations flush ("  1. I am..." over "indebted
    # to..."), which is a first-line indent, not a hanging one. So rather than
    # assume a direction, take the position the labels in this column actually
    # share, and read anything at that position as a label.
    label_x: Dict[int, Optional[float]] = {}
    for ci, lines in columns.items():
        cands = [ln.x0 for ln in lines if _label_split(ln, body_size) is not None]
        others = [ln.x0 for ln in lines if _label_split(ln, body_size) is None]
        if len(cands) < 2:
            label_x[ci] = None
            continue
        mode = _modal(cands)
        # With nothing set differently from the labels there is no geometry to
        # read, and the numbering has to decide instead.
        if others and abs(_modal(others) - mode) < 3.0:
            label_x[ci] = None
        elif not others:
            label_x[ci] = None
        else:
            label_x[ci] = mode

    col_of = {id(ln): ci for ci, lines in columns.items() for ln in lines}

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
        split = _label_split(line, body_size)
        opens = False
        if split is not None and not _CONTINUES_SENTENCE.match(split[1]):
            lx = label_x.get(col_of.get(id(line), 0))
            opens = (abs(line.x0 - lx) <= 2.0) if lx is not None \
                else _starts_note(split[0], last_num)
        if opens:
            flush()
            cur_label, cur_parts = split[0], [split[1]]
            if cur_label.isdigit():
                last_num = int(cur_label)
        elif cur_label is not None:
            cur_parts.append(txt)  # continuation line of the current note
    flush()
    return [n for n in notes if n.text]


# A note body never opens mid-sentence. Text that does is the continuation of
# the note above it, and the "label" in front of that text was a number
# inside that note's own citation -- a volume or page reference that happened
# to fall at the start of a line ("...Les commentaires," + "113, vol. 3, pp.
# 116-117"), which the hanging-indent geometry cannot tell from a real label
# because it sits at the block's left edge exactly like one.
_CONTINUES_SENTENCE = re.compile(r"^\s*(?:[,;:.)\-]|\(\d)")


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
        if ends_hyphenated(out) and part[:1].islower():
            out = drop_break_hyphen(out) + part
        elif out:
            out += " " + part
        else:
            out = part
    return out
