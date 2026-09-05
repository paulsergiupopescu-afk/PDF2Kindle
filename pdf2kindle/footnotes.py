"""Footnote detection and pairing.

Two halves must be found and matched:
  1. inline *reference markers* in the body (usually superscript digits), and
  2. the *note bodies* in the small-font block at the foot of the page.

We pair them by label within a page and hand the rest of the pipeline enough to
emit EPUB3 ``noteref``/``footnote`` links, which Kindle renders as pop-ups.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import List, Optional, Tuple

from .model import Line

_LABEL_RE = re.compile(r"^\s*([\*†‡§]|\(?\d{1,3}\)?)[.\)]?\s+(.*)$", re.DOTALL)
_MARKER_TEXT = re.compile(r"^[\*†‡§]?\d{0,3}[\*†‡§]?$")


@dataclass
class NoteBody:
    label: str
    text: str


def parse_page_notes(note_lines: List[Line]) -> List[NoteBody]:
    """Group the footnote-zone lines into individual notes keyed by label."""
    notes: List[NoteBody] = []
    cur_label: Optional[str] = None
    cur_parts: List[str] = []

    def flush():
        nonlocal cur_label, cur_parts
        if cur_label is not None:
            text = _dehyphenate_join(cur_parts)
            notes.append(NoteBody(label=cur_label, text=text.strip()))
        cur_label, cur_parts = None, []

    for line in note_lines:
        txt = line.text.strip()
        m = _LABEL_RE.match(txt)
        if m:
            flush()
            cur_label = _norm_label(m.group(1))
            cur_parts = [m.group(2)]
        else:
            if cur_label is None:
                # Note block that didn't start with a recognizable label; skip.
                continue
            cur_parts.append(txt)
    flush()
    return notes


def find_markers(line: Line) -> List[Tuple[int, str]]:
    """Return (span_index, label) for superscript reference markers on a line."""
    markers: List[Tuple[int, str]] = []
    for idx, span in enumerate(line.spans):
        t = span.text.strip()
        if not t:
            continue
        if span.superscript and _MARKER_TEXT.match(t) and any(c.isdigit() for c in t) or (
            span.superscript and t in ("*", "†", "‡", "§")
        ):
            markers.append((idx, _norm_label(t)))
    return markers


def _norm_label(label: str) -> str:
    return re.sub(r"[()\.\)]", "", label).strip()


def _dehyphenate_join(parts: List[str]) -> str:
    out = ""
    for i, part in enumerate(parts):
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
