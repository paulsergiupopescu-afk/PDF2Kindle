"""Reconstruct semantic document structure from analyzed pages.

Turns ordered lines into paragraphs/headings/images (preserving bold, italic and
footnote markers), links footnotes, then groups everything into chapters using
the PDF's outline when available or detected headings otherwise.
"""

from __future__ import annotations

import re
from typing import Dict, List, Optional, Tuple

from .analyze import Analyzed, PageContent
from .footnotes import find_markers, parse_page_notes
from .model import (
    Chapter,
    Document,
    Element,
    ElementKind,
    ImageBlock,
    InlineRun,
    Line,
)

_CHAPTER_RE = re.compile(
    r"^\s*(chapter|part|book|section|prologue|epilogue|introduction|preface|"
    r"appendix|conclusion|foreword|afterword)\b",
    re.IGNORECASE,
)
_MIN_IMAGE_PX = 80  # ignore tiny decorative images / bullets


# --------------------------------------------------------------------------- #
# Inline run construction
# --------------------------------------------------------------------------- #

def _append_text(runs: List[InlineRun], text: str, bold: bool, italic: bool) -> None:
    if not text:
        return
    if runs and runs[-1].noteref is None and runs[-1].bold == bold and runs[-1].italic == italic:
        runs[-1].text += text
    else:
        runs.append(InlineRun(text=text, bold=bold, italic=italic))


def _tail_text(runs: List[InlineRun]) -> str:
    return runs[-1].text if runs else ""


def _paragraph_runs(lines: List[Line], note_prefix: str) -> Tuple[List[InlineRun], List[str]]:
    """Build inline runs for a paragraph spanning *lines*, linking note markers."""
    runs: List[InlineRun] = []
    used_labels: List[str] = []

    for li, line in enumerate(lines):
        markers = {idx: label for idx, label in find_markers(line)}

        # Line-join handling (de-hyphenate or insert a space).
        if li > 0 and runs:
            tail = _tail_text(runs).rstrip()
            first_char = line.text.strip()[:1]
            if tail.endswith("-") and first_char.islower():
                # Remove the soft hyphen and glue words together.
                runs[-1].text = runs[-1].text.rstrip()[:-1]
            elif not tail.endswith((" ", "—", "–")):
                _append_text(runs, " ", runs[-1].bold, runs[-1].italic)

        for si, span in enumerate(line.spans):
            if si in markers:
                label = markers[si]
                note_id = f"{note_prefix}{label}"
                runs.append(InlineRun(text=label, noteref=note_id))
                used_labels.append(label)
            else:
                _append_text(runs, span.text, span.bold, span.italic)

    # Trim edges.
    if runs:
        runs[0].text = runs[0].text.lstrip()
        runs[-1].text = runs[-1].text.rstrip()
    runs = [r for r in runs if r.text != "" or r.noteref]
    return runs, used_labels


# --------------------------------------------------------------------------- #
# Heading classification
# --------------------------------------------------------------------------- #

def _is_heading(line: Line, body_size: float) -> Optional[int]:
    """Return a heading level (1..4) if the line looks like a heading, else None."""
    size = line.dominant_size
    text = line.text.strip()
    words = text.split()
    if not text or len(words) > 16:
        return None

    ratio = size / body_size if body_size else 1.0
    bold = all(s.bold for s in line.spans if s.text.strip())

    if _CHAPTER_RE.match(text) and ratio >= 1.05:
        return 1
    if ratio >= 1.8:
        return 1
    if ratio >= 1.4:
        return 2
    if ratio >= 1.18:
        return 3
    # Bold, short, standalone line at body size → minor heading.
    if bold and ratio >= 1.0 and len(words) <= 10 and not text.endswith((".", ",", ";", ":")):
        return 4
    # ALL CAPS short line.
    if text.isupper() and 1 < len(words) <= 8 and ratio >= 1.0:
        return 3
    return None


# --------------------------------------------------------------------------- #
# Paragraph grouping
# --------------------------------------------------------------------------- #

def _group_paragraphs(page: PageContent, body_size: float, line_height: float) -> List[List[Line]]:
    """Split a page's body lines into paragraph groups by gaps and indentation."""
    lines = page.body_lines
    if not lines:
        return []
    left_margin = min(ln.x0 for ln in lines)
    right_edge = max(ln.x1 for ln in lines)
    text_width = max(1.0, right_edge - left_margin)
    indent_min = max(6.0, text_width * 0.02)
    gap_threshold = line_height * 0.6

    groups: List[List[Line]] = []
    cur: List[Line] = []
    prev: Optional[Line] = None
    for ln in lines:
        start_new = False
        if prev is None:
            start_new = True
        else:
            gap = ln.y0 - prev.y1
            indented = ln.x0 > left_margin + indent_min
            prev_short = prev.x1 < right_edge - text_width * 0.18
            if gap > gap_threshold:
                start_new = True
            elif indented:
                start_new = True
            elif prev_short and ln.text[:1].isupper():
                start_new = True
        if start_new and cur:
            groups.append(cur)
            cur = []
        cur.append(ln)
        prev = ln
    if cur:
        groups.append(cur)
    return groups


# --------------------------------------------------------------------------- #
# Flow building
# --------------------------------------------------------------------------- #

def _select_images(page_images: List[ImageBlock]) -> List[ImageBlock]:
    return [im for im in page_images if im.width >= _MIN_IMAGE_PX and im.height >= _MIN_IMAGE_PX]


def _build_flow(
    analyzed: Analyzed,
    page_images: Dict[int, List[ImageBlock]],
) -> Tuple[List[Tuple[int, Element]], Dict[int, List[Element]]]:
    """Return (flat elements with page numbers, notes-by-page)."""
    flat: List[Tuple[int, Element]] = []
    notes_by_page: Dict[int, List[Element]] = {}

    for page in analyzed.pages:
        note_prefix = f"n{page.number}-"

        # Parse this page's footnote bodies first (so ids line up).
        page_notes = parse_page_notes(page.note_lines)
        if page_notes:
            elems: List[Element] = []
            for nb in page_notes:
                elems.append(
                    Element(
                        kind=ElementKind.FOOTNOTE,
                        runs=[InlineRun(text=nb.text)],
                        note_id=f"{note_prefix}{nb.label}",
                        note_label=nb.label,
                    )
                )
            notes_by_page[page.number] = elems

        for group in _group_paragraphs(page, analyzed.body_size, analyzed.line_height):
            level = None
            if len(group) <= 2:
                # Only single/short blocks are considered headings.
                level = _is_heading(group[0], analyzed.body_size)
            if level and len(group) == 1:
                runs, _ = _paragraph_runs(group, note_prefix)
                flat.append((page.number, Element(kind=ElementKind.HEADING, runs=runs, level=level)))
            else:
                runs, _ = _paragraph_runs(group, note_prefix)
                if not runs:
                    continue
                kind = ElementKind.PARAGRAPH
                # Indented block with no sentence-final punctuation variety → keep as paragraph.
                flat.append((page.number, Element(kind=kind, runs=runs)))

        # Append page images after its text (approximate reading position).
        for im in _select_images(page_images.get(page.number, [])):
            flat.append((page.number, Element(kind=ElementKind.IMAGE, image=im)))

    return flat, notes_by_page


# --------------------------------------------------------------------------- #
# Chapter splitting
# --------------------------------------------------------------------------- #

def _split_by_toc(
    flat: List[Tuple[int, Element]],
    notes_by_page: Dict[int, List[Element]],
    toc: List[list],
) -> Optional[List[Chapter]]:
    # toc entries: [level, title, page(1-based)]
    entries = [(int(l), str(t).strip(), int(p) - 1) for l, t, p in toc if int(p) >= 1]
    if len(entries) < 2:
        return None
    top_level = min(e[0] for e in entries)
    tops = [e for e in entries if e[0] == top_level]
    if len(tops) < 2:
        return None

    boundaries = [(t[2], t[1]) for t in tops]
    boundaries.sort()

    chapters: List[Chapter] = []
    for i, (start_page, title) in enumerate(boundaries):
        end_page = boundaries[i + 1][0] if i + 1 < len(boundaries) else 10 ** 9
        ch = Chapter(title=title or f"Chapter {i + 1}")
        for page_no, el in flat:
            if start_page <= page_no < end_page:
                ch.elements.append(el)
        for page_no, notes in notes_by_page.items():
            if start_page <= page_no < end_page:
                ch.footnotes.extend(notes)
        if ch.elements:
            chapters.append(ch)

    # Capture any front matter before the first boundary.
    first = boundaries[0][0]
    front = [el for pno, el in flat if pno < first]
    if front:
        fc = Chapter(title="Front Matter")
        fc.elements = front
        for page_no in range(0, first):
            fc.footnotes.extend(notes_by_page.get(page_no, []))
        chapters.insert(0, fc)
    return chapters if chapters else None


def _split_by_headings(
    flat: List[Tuple[int, Element]],
    notes_by_page: Dict[int, List[Element]],
) -> List[Chapter]:
    # Choose the top heading level actually present.
    heading_levels = [el.level for _, el in flat if el.kind == ElementKind.HEADING]
    split_level = min(heading_levels) if heading_levels else None

    chapters: List[Chapter] = []
    cur = Chapter(title="")
    cur_pages: set = set()

    def close():
        if cur.elements:
            for pno in sorted(cur_pages):
                cur.footnotes.extend(notes_by_page.get(pno, []))
            if not cur.title:
                cur.title = f"Chapter {len(chapters) + 1}"
            chapters.append(cur)

    for page_no, el in flat:
        if (
            split_level is not None
            and el.kind == ElementKind.HEADING
            and el.level == split_level
            and cur.elements
        ):
            close()
            cur = Chapter(title=el.text.strip())
            cur_pages = {page_no}
            cur.elements.append(el)
            continue
        if not cur.title and el.kind == ElementKind.HEADING and el.level == split_level:
            cur.title = el.text.strip()
        cur.elements.append(el)
        cur_pages.add(page_no)
    close()

    if not chapters:
        chapters = [Chapter(title="Book", elements=[el for _, el in flat])]
        for notes in notes_by_page.values():
            chapters[0].footnotes.extend(notes)
    return chapters


# --------------------------------------------------------------------------- #
# Public entry point
# --------------------------------------------------------------------------- #

def build_document(
    analyzed: Analyzed,
    meta: dict,
    page_images: Dict[int, List[ImageBlock]],
    *,
    title: str = "",
    author: str = "",
    language: str = "en",
) -> Document:
    flat, notes_by_page = _build_flow(analyzed, page_images)

    toc = meta.get("_toc") or []
    chapters = _split_by_toc(flat, notes_by_page, toc) if toc else None
    if not chapters:
        chapters = _split_by_headings(flat, notes_by_page)

    # Only keep footnotes actually attached; drop empty note bodies.
    for ch in chapters:
        ch.footnotes = [f for f in ch.footnotes if f.text.strip()]

    doc = Document(chapters=chapters, language=language)
    doc.title = title or (meta.get("title") or "").strip() or _guess_title(chapters)
    doc.author = author or (meta.get("author") or "").strip()

    # Cover: first sizeable image on the first page.
    for im in page_images.get(0, []):
        if im.width >= 200 and im.height >= 200:
            doc.cover = im
            break

    return doc


def _guess_title(chapters: List[Chapter]) -> str:
    for ch in chapters:
        for el in ch.elements:
            if el.kind == ElementKind.HEADING:
                return el.text.strip()[:120]
    return "Untitled"
