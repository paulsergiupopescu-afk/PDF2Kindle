"""Reconstruct semantic document structure from analyzed pages.

Turns ordered lines into paragraphs/headings/blockquotes/captions (preserving
bold, italic and note markers), links footnotes and endnotes, then groups
everything into chapters using the PDF outline or detected headings.

The ``academic`` profile adds features that matter for scholarly books:
multi-level numbered section headings and a nested table of contents, block
quotes, figure/table captions, chapter-end **endnotes** re-linked as pop-ups,
and hanging-indent bibliography entries.
"""

from __future__ import annotations

import re
from typing import Dict, List, Optional, Tuple

from .analyze import Analyzed, PageContent
from .footnotes import find_markers, parse_page_notes
from .text import normalize
from .model import (
    Chapter,
    Document,
    Element,
    ElementKind,
    ImageBlock,
    InlineRun,
    Line,
    SubHead,
)

_CHAPTER_RE = re.compile(
    r"^\s*(chapter|part|book|section|prologue|epilogue|introduction|preface|"
    r"appendix|conclusion|foreword|afterword)\b",
    re.IGNORECASE,
)
# "1", "1.2", "1.2.3", "IV.", "A." style leading section numbers.
_NUM_HEAD_RE = re.compile(r"^\s*(\d+(?:\.\d+){0,3})\.?\s+\S")
_ROMAN_HEAD_RE = re.compile(r"^\s*([IVXLC]{1,6})\.\s+\S")
_CAPTION_RE = re.compile(
    r"^\s*(figure|fig\.?|table|tbl\.?|plate|chart|diagram|scheme|equation|eq\.?|"
    r"listing|algorithm|map|graph|exhibit|box)\s*\.?\s*\d",
    re.IGNORECASE,
)
_NOTES_HEAD_RE = re.compile(r"^\s*(notes?|endnotes?|footnotes?)\s*$", re.IGNORECASE)
_REFS_HEAD_RE = re.compile(
    r"^\s*(references?|bibliography|works\s+cited|literature\s+cited|"
    r"further\s+reading|sources)\s*$",
    re.IGNORECASE,
)
_NOTE_ENTRY_RE = re.compile(r"^\s*(\d{1,3})[\.\)]?\s+(.*)$", re.DOTALL)
# A printed contents list and an index are page-number machinery for paper.
# Reflowed, their numbers point nowhere and the reader has a real nav TOC.
_PRINT_NAV_RE = re.compile(r"^\s*(contents|table\s+of\s+contents|index)\s*$", re.IGNORECASE)
_MIN_IMAGE_PX = 80


# --------------------------------------------------------------------------- #
# Inline run construction
# --------------------------------------------------------------------------- #

def _append_text(runs: List[InlineRun], text: str, bold: bool, italic: bool) -> None:
    if not text:
        return
    if (runs and runs[-1].noteref is None and not runs[-1].sup
            and runs[-1].bold == bold and runs[-1].italic == italic):
        runs[-1].text += text
    else:
        runs.append(InlineRun(text=text, bold=bold, italic=italic))


def _tail_text(runs: List[InlineRun]) -> str:
    return runs[-1].text if runs else ""


def _paragraph_runs(lines: List[Line], note_prefix: str, body_size: float = 0.0) -> List[InlineRun]:
    """Build inline runs for a paragraph spanning *lines*, linking note markers."""
    runs: List[InlineRun] = []
    for li, line in enumerate(lines):
        markers = {idx: label for idx, label in find_markers(line, body_size)}

        if li > 0 and runs:
            tail = _tail_text(runs).rstrip()
            first_char = line.text.strip()[:1]
            if tail.endswith("-") and first_char.islower():
                runs[-1].text = runs[-1].text.rstrip()[:-1]
            elif not tail.endswith((" ", "—", "–")):
                _append_text(runs, " ", runs[-1].bold, runs[-1].italic)

        for si, span in enumerate(line.spans):
            if si in markers:
                label = markers[si]
                runs.append(InlineRun(text=label, noteref=f"{note_prefix}{label}"))
            else:
                _append_text(runs, span.text, span.bold, span.italic)

    if runs:
        runs[0].text = runs[0].text.lstrip()
        runs[-1].text = runs[-1].text.rstrip()
    for r in runs:
        if r.noteref is None:
            r.text = normalize(r.text)
    return [r for r in runs if r.text != "" or r.noteref]


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
    trailing_period = text.endswith((".", ",", ";", ":")) and not text.endswith("...")

    # Named divisions ("Chapter 3", "Appendix B", "Introduction").
    if _CHAPTER_RE.match(text) and ratio >= 1.02:
        return 1

    # Numbered sections: depth of the number sets the level. Guard against body
    # sentences that merely start with a number by requiring shortness + weight.
    m = _NUM_HEAD_RE.match(text)
    if m and len(words) <= 14 and (bold or ratio >= 1.05) and not trailing_period:
        depth = m.group(1).count(".")  # "1"->0, "1.2"->1, "1.2.3"->2
        return min(1 + depth, 4) if ratio >= 1.3 else min(2 + depth, 4)
    if _ROMAN_HEAD_RE.match(text) and len(words) <= 14 and (bold or ratio >= 1.05):
        return 2

    # Font-size driven levels.
    if ratio >= 1.8:
        return 1
    if ratio >= 1.4:
        return 2
    if ratio >= 1.18:
        return 3
    if bold and ratio >= 1.0 and len(words) <= 10 and not trailing_period:
        return 4
    if text.isupper() and 1 < len(words) <= 8 and ratio >= 1.0:
        return 3
    return None


# --------------------------------------------------------------------------- #
# Paragraph grouping
# --------------------------------------------------------------------------- #

def _group_paragraphs(page: PageContent, body_size: float, line_height: float) -> List[List[Line]]:
    """Split a page's body lines into paragraph groups.

    A new paragraph starts on a wide vertical gap, a first-line indent (a line
    indented relative to the *previous* line), or after a short final line.
    Indent is measured against the previous line so an evenly-indented block
    (a block quote) stays together instead of fragmenting per line.
    """
    lines = page.body_lines
    if not lines:
        return []
    right_edge = max(ln.x1 for ln in lines)
    left_margin = min(ln.x0 for ln in lines)
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
            first_line_indent = ln.x0 > prev.x0 + indent_min
            prev_short = prev.x1 < right_edge - text_width * 0.18
            if gap > gap_threshold:
                start_new = True
            elif first_line_indent:
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


def _is_blockquote(lines: List[Line], body_left: float, body_size: float, page_width: float) -> bool:
    if body_left <= 0:
        return False
    xs = sorted(ln.x0 for ln in lines)
    median_x0 = xs[len(xs) // 2]
    indent = max(18.0, page_width * 0.035)
    dom = max(lines, key=lambda ln: len(ln.text)).dominant_size
    if len(lines) >= 2 and median_x0 >= body_left + indent:
        return True
    if dom <= body_size - 0.8 and median_x0 >= body_left + 6:
        return True
    return False


# --------------------------------------------------------------------------- #
# Flow building
# --------------------------------------------------------------------------- #

def _covers_page(im: ImageBlock, page: PageContent) -> bool:
    area = page.width * page.height
    if area <= 0:
        return False
    x0, y0, x1, y1 = im.bbox
    return abs((x1 - x0) * (y1 - y0)) / area > 0.5


def _select_images(page_images: List[ImageBlock]) -> List[ImageBlock]:
    return [im for im in page_images if im.width >= _MIN_IMAGE_PX and im.height >= _MIN_IMAGE_PX]


def _build_flow(
    analyzed: Analyzed,
    page_images: Dict[int, List[ImageBlock]],
    academic: bool,
) -> Tuple[List[Tuple[int, Element]], Dict[int, List[Element]]]:
    flat: List[Tuple[int, Element]] = []
    notes_by_page: Dict[int, List[Element]] = {}

    for page in analyzed.pages:
        note_prefix = f"n{page.number}-"

        page_notes = parse_page_notes(page.note_lines, analyzed.body_size)
        if page_notes:
            notes_by_page[page.number] = [
                Element(
                    kind=ElementKind.FOOTNOTE,
                    runs=[InlineRun(text=nb.text)],
                    note_id=f"{note_prefix}{nb.label}",
                    note_label=nb.label,
                )
                for nb in page_notes
            ]

        for group in _group_paragraphs(page, analyzed.body_size, analyzed.line_height):
            level = _is_heading(group[0], analyzed.body_size) if len(group) == 1 else None
            if level:
                runs = _paragraph_runs(group, note_prefix, analyzed.body_size)
                flat.append((page.number, Element(kind=ElementKind.HEADING, runs=runs, level=level)))
                continue

            runs = _paragraph_runs(group, note_prefix, analyzed.body_size)
            if not runs:
                continue

            kind = ElementKind.PARAGRAPH
            if academic:
                if _CAPTION_RE.match(group[0].text):
                    kind = ElementKind.CAPTION
                elif _is_blockquote(group, analyzed.body_left, analyzed.body_size, page.width):
                    kind = ElementKind.BLOCKQUOTE
            flat.append((page.number, Element(kind=kind, runs=runs)))

        for im in _select_images(page_images.get(page.number, [])):
            if page.number == 0 and _covers_page(im, page):
                continue  # full-page art on page 1 is the cover, already used
            flat.append((page.number, Element(kind=ElementKind.IMAGE, image=im)))

    return flat, notes_by_page


_SENT_END = (".", "!", "?", '"', "\u201d", "\u2019", "'", ")", ":", ";", "\u2014")


def _join_runs(prev: Element, sep: str) -> None:
    """Append a separator to a paragraph without corrupting a trailing marker."""
    if prev.runs and prev.runs[-1].noteref is None and not prev.runs[-1].sup:
        prev.runs[-1].text = prev.runs[-1].text.rstrip() + sep
    elif sep:
        prev.runs.append(InlineRun(text=sep))


_BARE_NUM_HEAD_RE = re.compile(r"^\s*\d+(?:\.\d+){0,3}\.?\s*$")


def _merge_split_headings(flat: List[Tuple[int, Element]]) -> List[Tuple[int, Element]]:
    """Rejoin a heading that print split across lines.

    Books set section numbers on their own line ("1.2" above "Basic
    Austinianism") and wrap long titles. Each fragment would otherwise become
    its own heading -- and its own meaningless entry in the table of contents.
    Only fragments on the same page are joined, so a real heading is never
    merged into the chapter before it.
    """
    out: List[Tuple[int, Element]] = []
    for page_no, el in flat:
        if (
            out
            and el.kind == ElementKind.HEADING
            and out[-1][1].kind == ElementKind.HEADING
            and out[-1][0] == page_no
        ):
            prev = out[-1][1]
            ptxt, cur = prev.text.strip(), el.text.strip()
            numbered = bool(_BARE_NUM_HEAD_RE.match(ptxt))
            continues = prev.level == el.level and not ptxt.endswith((".", "?", "!", ":", ";"))
            if numbered or continues:
                prev.runs = [InlineRun(text=f"{ptxt} {cur}")]
                prev.level = min(prev.level or 9, el.level or 9)
                continue
        out.append((page_no, el))
    return out


def _merge_across_pages(flat: List[Tuple[int, Element]]) -> List[Tuple[int, Element]]:
    """Rejoin a paragraph that continues onto the next page.

    Page furniture used to interrupt these; now that it is stripped, a sentence
    broken by a page turn should read as one paragraph again.
    """
    out: List[Tuple[int, Element]] = []
    for page_no, el in flat:
        if out and el.kind == ElementKind.PARAGRAPH and out[-1][1].kind == ElementKind.PARAGRAPH \
                and page_no != out[-1][0]:
            prev = out[-1][1]
            ptxt, ctxt = prev.text.rstrip(), el.text.lstrip()
            if ptxt and ctxt and not ptxt.endswith(_SENT_END):
                if ptxt.endswith("-") and ctxt[:1].islower():
                    if prev.runs[-1].noteref is None and not prev.runs[-1].sup:
                        prev.runs[-1].text = prev.runs[-1].text.rstrip()[:-1]
                    prev.runs.extend(el.runs)
                    continue
                if ctxt[:1].islower():
                    _join_runs(prev, " ")
                    prev.runs.extend(el.runs)
                    continue
        out.append((page_no, el))
    return out


def _resolve_notes(chapter: Chapter) -> None:
    """Bind every reference marker to its note, then guarantee no dead links.

    Markers are first created with a page-scoped id, which is right for
    footnotes printed at the foot of the page they are cited on. Endnotes are
    different: they are gathered at the end of the chapter and numbered
    continuously, so a marker on one page refers to a note many pages later.
    When a label is unambiguous within the chapter we therefore match on the
    label alone; a marker that still resolves to nothing is downgraded to a
    plain superscript rather than shipped as a broken link.
    """
    by_id = {f.note_id: f for f in chapter.footnotes if f.note_id}
    by_label: Dict[str, Element] = {}
    ambiguous: set = set()
    for f in chapter.footnotes:
        label = (f.note_label or "").strip()
        if not label:
            continue
        if label in by_label:
            ambiguous.add(label)
        else:
            by_label[label] = f

    for el in chapter.elements:
        for run in el.runs:
            if not run.noteref:
                continue
            if run.noteref in by_id:
                continue  # already points at a note on the citing page
            label = run.text.strip()
            target = by_label.get(label)
            if target is not None and label not in ambiguous:
                run.noteref = target.note_id
            else:
                run.noteref = None
                run.sup = True

    # Present the notes in reading order rather than page-discovery order.
    chapter.footnotes.sort(
        key=lambda f: int(f.note_label) if (f.note_label or "").isdigit() else 10 ** 9
    )


# --------------------------------------------------------------------------- #
# Chapter splitting
# --------------------------------------------------------------------------- #

def _split_by_toc(flat, notes_by_page, toc) -> Optional[List[Chapter]]:
    entries = [(int(l), str(t).strip(), int(p) - 1) for l, t, p in toc if int(p) >= 1]
    if len(entries) < 2:
        return None
    top_level = min(e[0] for e in entries)
    tops = [e for e in entries if e[0] == top_level]
    if len(tops) < 2:
        return None
    boundaries = sorted((t[2], t[1]) for t in tops)

    chapters: List[Chapter] = []
    for i, (start_page, title) in enumerate(boundaries):
        end_page = boundaries[i + 1][0] if i + 1 < len(boundaries) else 10 ** 9
        ch = Chapter(title=title or f"Chapter {i + 1}")
        ch.elements = [el for pno, el in flat if start_page <= pno < end_page]
        for pno, notes in notes_by_page.items():
            if start_page <= pno < end_page:
                ch.footnotes.extend(notes)
        if ch.elements:
            chapters.append(ch)

    first = boundaries[0][0]
    front = [el for pno, el in flat if pno < first]
    if front:
        fc = Chapter(title="Front Matter", elements=front)
        for pno, notes in notes_by_page.items():
            if pno < first:
                fc.footnotes.extend(notes)
        chapters.insert(0, fc)
    return chapters or None


def _attach_notes_by_range(
    chapters: List[Chapter], starts: List[int], notes_by_page: Dict[int, List[Element]]
) -> None:
    """Attach each page's notes to the chapter whose page range covers it.

    Ranges rather than the exact pages an element came from: merging a
    paragraph across a page turn removes the only element carrying the later
    page, and its notes would otherwise be dropped.
    """
    last = max(notes_by_page) if notes_by_page else 0
    for i, ch in enumerate(chapters):
        start = starts[i]
        end = starts[i + 1] if i + 1 < len(starts) else last + 1
        for pno, notes in notes_by_page.items():
            if start <= pno < end:
                ch.footnotes.extend(notes)


def _split_by_headings(flat, notes_by_page) -> List[Chapter]:
    heading_levels = [el.level for _, el in flat if el.kind == ElementKind.HEADING]
    split_level = min(heading_levels) if heading_levels else None

    built: List[Tuple[int, Chapter]] = []
    cur = Chapter(title="")
    cur_start: Optional[int] = None

    for page_no, el in flat:
        is_break = (
            split_level is not None
            and el.kind == ElementKind.HEADING
            and el.level == split_level
            and cur.elements
        )
        if is_break:
            built.append((cur_start or 0, cur))
            cur = Chapter(title=el.text.strip())
            cur_start = page_no
            cur.elements.append(el)
            continue
        if cur_start is None:
            cur_start = page_no
        if not cur.title and el.kind == ElementKind.HEADING and el.level == split_level:
            cur.title = el.text.strip()
        cur.elements.append(el)
    if cur.elements:
        built.append((cur_start or 0, cur))

    if not built:
        built = [(0, Chapter(title="Book", elements=[el for _, el in flat]))]

    chapters = []
    starts = []
    for i, (start, ch) in enumerate(built):
        if not ch.title:
            ch.title = f"Chapter {i + 1}"
        chapters.append(ch)
        starts.append(start)
    _attach_notes_by_range(chapters, starts, notes_by_page)
    return chapters


# --------------------------------------------------------------------------- #
# Academic post-processing
# --------------------------------------------------------------------------- #

def _extract_endnotes(chapter: Chapter, idx: int) -> None:
    """Move a trailing 'Notes'/'Endnotes' section into pop-up notes and link them."""
    els = chapter.elements
    start = None
    for i, el in enumerate(els):
        if el.kind == ElementKind.HEADING and _NOTES_HEAD_RE.match(el.text.strip()):
            start = i
            break
    if start is None:
        return

    prefix = f"en{idx}-"
    note_map: Dict[str, Element] = {}
    order: List[Element] = []
    consumed_to = start
    cur: Optional[Element] = None

    for el in els[start + 1:]:
        if el.kind != ElementKind.PARAGRAPH:
            break  # a non-paragraph (e.g. the next heading) ends the notes block
        m = _NOTE_ENTRY_RE.match(el.text)
        if m:
            label = m.group(1)
            note = Element(
                kind=ElementKind.FOOTNOTE,
                runs=[InlineRun(text=m.group(2).strip())],
                note_id=f"{prefix}{label}",
                note_label=label,
            )
            note_map[label] = note
            order.append(note)
            cur = note
        elif cur is not None:
            tail = cur.runs[-1].text.rstrip() if cur.runs else ""
            piece = el.text.strip()
            if tail.endswith("-") and piece[:1].islower():
                cur.runs[-1].text = tail[:-1] + piece  # word split across lines
            else:
                cur.runs.append(InlineRun(text=" " + piece))
        else:
            break
        consumed_to += 1

    if not note_map:
        return

    # Drop the "Notes" heading and its note-body paragraphs from the body;
    # the notes are re-emitted as the chapter's pop-up footnote section.
    chapter.elements = els[:start] + els[consumed_to + 1:]

    existing_ids = {f.note_id for f in chapter.footnotes}
    for note in order:
        if note.note_id not in existing_ids:
            chapter.footnotes.append(note)

    # Re-point body reference markers to the matching endnote.
    for el in chapter.elements:
        for run in el.runs:
            if run.noteref and run.noteref not in existing_ids:
                label = run.text.strip()
                if label in note_map:
                    run.noteref = note_map[label].note_id


def _style_references(chapter: Chapter) -> None:
    """Tag entries under a References/Bibliography heading for hanging indent."""
    in_refs = False
    for el in chapter.elements:
        if el.kind == ElementKind.HEADING:
            in_refs = bool(_REFS_HEAD_RE.match(el.text.strip()))
            continue
        if in_refs and el.kind == ElementKind.PARAGRAPH:
            el.kind = ElementKind.REFERENCE


def _assign_nav(chapter: Chapter, idx: int) -> None:
    """Anchor sub-headings and record them for a nested table of contents."""
    levels = [el.level for el in chapter.elements if el.kind == ElementKind.HEADING]
    if not levels:
        return
    top = min(levels)
    k = 0
    for el in chapter.elements:
        if el.kind == ElementKind.HEADING and el.level > top:
            el.anchor = f"sec-{idx}-{k}"
            chapter.subheads.append(SubHead(anchor=el.anchor, title=el.text.strip(), level=el.level))
            k += 1


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
    profile: str = "academic",
    keep_print_nav: bool = False,
) -> Document:
    academic = profile == "academic"
    flat, notes_by_page = _build_flow(analyzed, page_images, academic)
    flat = _merge_split_headings(flat)
    flat = _merge_across_pages(flat)

    toc = meta.get("_toc") or []
    chapters = _split_by_toc(flat, notes_by_page, toc) if toc else None
    if not chapters:
        chapters = _split_by_headings(flat, notes_by_page)

    if not keep_print_nav:
        chapters = [c for c in chapters if not _PRINT_NAV_RE.match(c.title.strip())] or chapters

    for i, ch in enumerate(chapters):
        if academic:
            _extract_endnotes(ch, i)
            _style_references(ch)
            _assign_nav(ch, i)
        ch.footnotes = [f for f in ch.footnotes if f.text.strip()]
        _resolve_notes(ch)

    doc = Document(chapters=chapters, language=language)
    doc.title = title or (meta.get("title") or "").strip() or _guess_title(chapters)
    doc.author = author or (meta.get("author") or "").strip()

    # Cover: a render of page 1 is the most faithful and always available;
    # fall back to a large embedded image only if rendering failed.
    cr = meta.get("_cover_render")
    if cr:
        doc.cover = ImageBlock(data=cr["data"], ext=cr["ext"], bbox=(0.0, 0.0, 0.0, 0.0),
                               width=cr["width"], height=cr["height"])
    else:
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
