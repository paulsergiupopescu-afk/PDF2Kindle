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
from .extract import _column_count
from .footnotes import find_embedded_markers, find_markers, parse_page_notes
from .text import drop_break_hyphen, ends_hyphenated, normalize
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

# Named top-level divisions. Matched against the line's first word only, so a
# plural ("Conclusions") and its singular both hit without listing each one --
# the alternative, an exhaustive word list, silently misses whichever term a
# given book happens to use and inconsistently demotes it (see _is_heading).
_CHAPTER_RE = re.compile(
    r"^\s*(chapters?|parts?|books?|sections?|prologue|epilogue|introduction|"
    r"preface|appendix(?:es|ices)?|conclusions?|foreword|afterword|abstract|"
    r"summary|bibliography|references|acknowledge?ments?|glossary|index|"
    # A handful of common non-English equivalents, so a division word isn't
    # only ever recognized in English -- most usefully Romanian here, but
    # covering the same handful of major European languages is nearly free.
    r"capitolul?|partea|cartea|secţiunea|secțiunea|introducere|prefaţă|prefață|"
    r"anexa|anexă|concluzii|concluzie|rezumat|bibliografie|referinţe|referințe|"
    r"glosar|"
    r"chapitre|partie|préface|conclusion|résumé|bibliographie|glossaire|"
    r"kapitel|teil|einleitung|vorwort|schlussfolgerung|zusammenfassung|"
    r"capítulo|parte|introducción|prefacio|conclusión|resumen|bibliografía|"
    r"capitolo|prefazione|conclusione|riepilogo)\b",
    re.IGNORECASE,
)
# "1", "1.2", "1.2.3", "IV.", "A." style leading section numbers.
# The space after the number is not required: a Word-styled heading numbered
# "1.Literature overview" (dot, no space) is exactly as common as "1. Literature
# overview" (dot, space) or "1 Literature overview" (space, no dot).
_NUM_HEAD_RE = re.compile(r"^\s*(\d+(?:\.\d+){0,3})\.?\s*\S")
_ROMAN_HEAD_RE = re.compile(r"^\s*([IVXLC]{1,6})\.\s+\S")
# A roman numeral alone on its own line/paragraph, nothing else -- a
# sub-section divider, not a "IV. Title" heading (that's _ROMAN_HEAD_RE above).
_BARE_ROMAN_HEAD_RE = re.compile(r"^\s*[IVXLC]{1,6}\.?\s*$")
# A dot leader ("...................") or a run of ellipsis characters, as
# used by a printed Contents/Index/List-of-Tables entry to connect a title to
# its page number.
_DOT_LEADER_RE = re.compile(r"\.{4,}|…{2,}")
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
# A "List of Illustrations" / "Maps" / "Tables" section is a caption ...... page#
# tabular layout that print alone can lay out; reflowed, the page numbers are
# meaningless and the caption/number pairing often garbles across lines. Drop
# it like Contents/Index, but as a *sub-section* -- these are headings inside
# the front matter, not their own top-level chapter, when the PDF has no
# outline to split on.
_FRONT_LIST_RE = re.compile(
    r"^\s*list\s+of\s+(illustrations|maps|tables|figures|plates|abbreviations)\s*$"
    r"|^\s*(illustrations|maps|tables|figures|plates|contents|table\s+of\s+contents)\s*$",
    re.IGNORECASE,
)
# A copyright line: "© Keith Hitchins 2014". Reliable across most books when
# the PDF itself carries no /Author metadata.
_COPYRIGHT_RE = re.compile(
    r"©\s*([A-Z][\w.''\-]+(?:\s+[A-Z][\w.''\-]+){0,4})\s*,?\s*(?:19|20)\d{2}"
)
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


def _paragraph_runs(
    lines: List[Line],
    note_prefix: str,
    body_size: float = 0.0,
    known_labels: Optional[set] = None,
) -> List[InlineRun]:
    """Build inline runs for a paragraph spanning *lines*, linking note markers."""
    runs: List[InlineRun] = []
    for li, line in enumerate(lines):
        markers = {idx: label for idx, label in find_markers(line, body_size)}

        if li > 0 and runs:
            tail = _tail_text(runs).rstrip()
            first_char = line.text.strip()[:1]
            if ends_hyphenated(tail) and first_char.islower():
                runs[-1].text = drop_break_hyphen(runs[-1].text)
            elif not tail.endswith((" ", "—", "–")):
                _append_text(runs, " ", runs[-1].bold, runs[-1].italic)

        for si, span in enumerate(line.spans):
            if si in markers:
                label = markers[si]
                runs.append(InlineRun(text=label, noteref=f"{note_prefix}{label}"))
                continue
            embedded = find_embedded_markers(span.text, known_labels) if known_labels else []
            if not embedded:
                _append_text(runs, span.text, span.bold, span.italic)
                continue
            pos = 0
            for start, end, label in embedded:
                if start > pos:
                    _append_text(runs, span.text[pos:start], span.bold, span.italic)
                runs.append(InlineRun(text=label, noteref=f"{note_prefix}{label}"))
                pos = end
            if pos < len(span.text):
                _append_text(runs, span.text[pos:], span.bold, span.italic)

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
    # A dot-leader or ellipsis run ("Introduction .......... 4") is a printed
    # Contents/Index entry, never a real heading -- regardless of its bold
    # weight or leading section number, both of which a Word-styled ToC entry
    # otherwise satisfies just as well as an actual section title.
    if _DOT_LEADER_RE.search(text):
        return None
    # A scanned page's decorative rule, page-break ornament, or an OCR
    # misread of one ("■", "***", "_____ 1 Λ Λ _____") can be large or bold
    # enough to look like a heading by every other test here. Real headings,
    # in any language, contain an actual word -- a run of letters at least a
    # few characters long; a stray Greek/Cyrillic OCR fragment ("1 ΠΛ") does
    # not. The one legitimate all-digit heading, a bare chapter number set on
    # its own line to be merged with its title later, is let through only
    # when it is dramatically larger than body text -- unlike a folio number
    # that survived furniture-stripping, which sits close to body size.
    has_word = bool(re.search(r"[^\W\d_]{3,}", text, re.UNICODE))
    if not has_word:
        ratio_check = size / body_size if body_size else 1.0
        # A bare roman numeral ("I", "IV") standing alone as its own
        # paragraph marks a sub-section divider -- and unlike the arabic
        # case just below, it needs no size/weight qualifier at all: this
        # book sets some of these bold and some not, at body size either
        # way, so no single geometric signal covers all of them. What
        # covers all of them just as well is the string itself: decorative
        # OCR noise essentially never comes out as a clean, short roman
        # numeral with nothing else on the line by chance.
        if _BARE_ROMAN_HEAD_RE.match(text):
            return 2
        if not (_BARE_NUM_HEAD_RE.match(text) and ratio_check >= 1.8):
            return None

    ratio = size / body_size if body_size else 1.0
    bold = all(s.bold for s in line.spans if s.text.strip())
    trailing_period = text.endswith((".", ",", ";", ":")) and not text.endswith("...")

    # Named divisions ("Chapter 3", "Appendix B", "Introduction"). A "Part"
    # label conventionally sits at body size, a small superior line over the
    # real (larger) title on the next line or two -- so unlike a numbered or
    # roman heading below, this is let through at body size rather than
    # required to be larger than it, on the strength of the word alone.
    if _CHAPTER_RE.match(text) and ratio >= 0.98:
        return 1

    # Numbered sections: depth of the number sets the level. Guard against body
    # sentences that merely start with a number by requiring shortness + weight.
    # Bold alone is not enough at any size: a bold lettered sub-item inside a
    # list ("I. (a) Pentru aceasta...", set well *below* body size) matches
    # the number/roman shape too, and a heading is never smaller than body
    # text regardless of weight -- ratio >= 1.0 rules that out.
    m = _NUM_HEAD_RE.match(text)
    if m and len(words) <= 14 and ratio >= 1.0 and (bold or ratio >= 1.05) and not trailing_period:
        depth = m.group(1).count(".")  # "1"->0, "1.2"->1, "1.2.3"->2
        return min(1 + depth, 4) if ratio >= 1.3 else min(2 + depth, 4)
    if _ROMAN_HEAD_RE.match(text) and len(words) <= 14 and ratio >= 1.0 and (bold or ratio >= 1.05):
        # Roman-then-Arabic is the classic "Part I > Section 1 > 1.1" book
        # hierarchy -- a roman numeral outranks a plain arabic-numbered
        # section, so it belongs at the top level alongside a named division
        # ("Chapter 3"), not lumped in with its own subsections.
        return 1

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


def _is_wrapped_heading(group: List[Line], body_size: float) -> Optional[int]:
    """Return a heading level for a *multi-line* group that is really one
    heading wrapped across lines -- a title set large enough to need two or
    three lines never gets a chance at `_is_heading`, which only runs on
    single-line groups, so on its own it is silently kept as a paragraph.

    Judged purely by font size and shortness, deliberately not reusing
    `_is_heading`'s numbered-section/bold/chapter-name rules: those exist to
    catch a heading set at or near body size, which is exactly the size a
    genuine multi-line paragraph is also set at, and applying them here would
    misclassify ordinary wrapped prose as a heading constantly.
    """
    if len(group) > 4:
        return None  # a heading essentially never wraps further than this
    sizes = [ln.dominant_size for ln in group]
    if max(sizes) - min(sizes) > 0.6:
        return None  # not one uniformly-styled heading
    size = sizes[0]
    ratio = size / body_size if body_size else 1.0
    if ratio < 1.18:
        return None
    total_words = sum(len(ln.text.split()) for ln in group)
    if total_words > 20:
        return None
    if ratio >= 1.8:
        return 1
    if ratio >= 1.4:
        return 2
    return 3


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


def _is_scan_background(im: ImageBlock, page: PageContent) -> bool:
    """A near-exact full-page raster is the scan itself, not a figure.

    A "searchable PDF" produced by scanning + OCR (ABBYY FineReader and
    similar) embeds the original page photograph behind an invisible text
    layer on *every* page. That image is essentially always 100% of the page
    area; a real inline illustration -- even a large plate -- almost always
    leaves visible margin around it. 92% comfortably separates the two
    without risking a genuine full-bleed figure.
    """
    area = page.width * page.height
    if area <= 0:
        return False
    x0, y0, x1, y1 = im.bbox
    return abs((x1 - x0) * (y1 - y0)) / area > 0.92


def _select_images(page_images: List[ImageBlock]) -> List[ImageBlock]:
    return [im for im in page_images if im.width >= _MIN_IMAGE_PX and im.height >= _MIN_IMAGE_PX]


# Keywords that confirm a page is a front-matter list, on top of its shape
# (see _is_headless_toc_page) -- required so a short-lined poem or epigraph
# in the front matter, which can share the low word-per-line signature,
# isn't mistaken for one.
_TOC_KEYWORDS_RE = re.compile(
    r"list\s+of\s+(illustrations|maps|tables|figures|plates)|further\s+reading"
    r"|acknowledgments|\bcontents\b",
    re.IGNORECASE,
)
# Front matter is reliably within a book's first pages; bounding the search
# keeps this from ever matching a table deep in the real text.
_TOC_PAGE_LIMIT = 25


def _is_headless_toc_page(page: PageContent) -> bool:
    """A Contents/TOC page whose own heading was stripped as a running head
    or folio before reaching here, leaving just its row-and-page-number body.

    Its shape is the same "many short fragments" signature as a map -- reused
    from extract.py's column-count test -- confirmed by keyword content so a
    short-lined poem or epigraph in the front matter isn't swept up too.
    """
    if page.number >= _TOC_PAGE_LIMIT or len(page.body_lines) < 10:
        return False
    words = [len(ln.text.split()) for ln in page.body_lines]
    avg_words = sum(words) / len(page.body_lines)
    lens = sorted(len(ln.text.strip()) for ln in page.body_lines)
    median_len = lens[len(lens) // 2]
    if not (avg_words < 3.5 and median_len < 30):
        return False
    if not (2 <= _column_count(page.body_lines) <= 12):
        return False
    combined = " ".join(ln.text for ln in page.body_lines)
    return bool(_TOC_KEYWORDS_RE.search(combined))


def _build_flow(
    analyzed: Analyzed,
    page_images: Dict[int, List[ImageBlock]],
    academic: bool,
    keep_print_nav: bool = False,
) -> Tuple[List[Tuple[int, Element]], Dict[int, List[Element]]]:
    flat: List[Tuple[int, Element]] = []
    notes_by_page: Dict[int, List[Element]] = {}

    for page in analyzed.pages:
        if not keep_print_nav and _is_headless_toc_page(page):
            continue
        note_prefix = f"n{page.number}-"

        page_notes = parse_page_notes(page.note_lines, analyzed.body_size)
        known_labels = {nb.label for nb in page_notes}
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
            if len(group) == 1:
                level = _is_heading(group[0], analyzed.body_size)
                size = group[0].dominant_size if level else 0.0
            else:
                level = _is_wrapped_heading(group, analyzed.body_size)
                size = group[0].dominant_size if level else 0.0
            if level:
                runs = _paragraph_runs(group, note_prefix, analyzed.body_size, known_labels)
                flat.append((page.number, Element(kind=ElementKind.HEADING, runs=runs,
                                                  level=level, size=size)))
                continue

            runs = _paragraph_runs(group, note_prefix, analyzed.body_size, known_labels)
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
            if _is_scan_background(im, page):
                continue  # the scan itself, not a figure -- see _is_scan_background
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
            # A bare chapter number ("2") or a short "Part"/"Chapter" label
            # ("Partea întâi") is set at its own, usually smaller, size as a
            # superior line over the real title that follows -- so either one
            # merges forward into a differently-sized next line on sight,
            # the same way a numbered heading's own wrapped continuation
            # does below.
            numbered = bool(_BARE_NUM_HEAD_RE.match(ptxt)) or (
                bool(_CHAPTER_RE.match(ptxt)) and len(ptxt.split()) <= 4
            )
            # A genuine multi-line wrap keeps the same font size throughout;
            # a title page stacks several *different* short heading-like
            # lines (field of study, title, thesis type, supervisor) that
            # merely happen to lack terminal punctuation each -- without a
            # size match, those would all glue into one nonsense heading.
            same_size = prev.size <= 0 or el.size <= 0 or abs(prev.size - el.size) <= 1.0
            # A book can style every numbered depth at one identical size --
            # "1.Literature overview" and its own "1.1. Hegemony..." both at
            # 20pt here -- so same_size alone cannot rule out the next line
            # being a *new*, independently-numbered heading rather than a
            # continuation of this one's wrapped text. "numbered" already
            # covers the one case where a bare number legitimately precedes
            # its title.
            new_numbered_section = bool(_NUM_HEAD_RE.match(cur)) and not numbered
            continues = (
                same_size
                and not new_numbered_section
                and not ptxt.endswith((".", "?", "!", ":", ";"))
                and len(ptxt) < 160
            )
            if numbered or continues:
                prev.runs = [InlineRun(text=f"{ptxt} {cur}")]
                prev.level = min(prev.level or 9, el.level or 9)
                # Adopt the just-merged fragment's size as the new basis for
                # comparison: after a bare-number merge ("2" then a normal-
                # size title), prev.size must track the *title's* size, or a
                # third wrapped fragment (also at the title's size) would be
                # compared against the number's size instead and rejected.
                if el.size > 0:
                    prev.size = el.size
                continue
        out.append((page_no, el))
    return out


def _merge_split_paragraphs(flat: List[Tuple[int, Element]]) -> List[Tuple[int, Element]]:
    """Rejoin a paragraph that was broken in two.

    A page turn is the usual cause: page furniture used to interrupt these,
    and now that it is stripped a sentence broken by the turn should read as
    one paragraph again. Continuing lowercase is the evidence there, so the
    merge is limited to a genuine page boundary -- within a page, the break
    between two paragraphs is real and must be respected.

    A paragraph ending in a hyphenated part-word needs no such caution: no
    paragraph ever ends that way, so the word plainly continues in whatever
    comes next, whether or not a page boundary falls in between. That case is
    common in a scan, where anything the extractor could not place (a note
    zone, a stray folio) can interrupt a paragraph mid-word.
    """
    out: List[Tuple[int, Element]] = []
    for page_no, el in flat:
        if out and el.kind == ElementKind.PARAGRAPH and out[-1][1].kind == ElementKind.PARAGRAPH:
            prev = out[-1][1]
            ptxt, ctxt = prev.text.rstrip(), el.text.lstrip()
            if ptxt and ctxt and not ptxt.endswith(_SENT_END) and ctxt[:1].islower():
                if ends_hyphenated(ptxt):
                    if prev.runs[-1].noteref is None and not prev.runs[-1].sup:
                        prev.runs[-1].text = drop_break_hyphen(prev.runs[-1].text)
                    prev.runs.extend(el.runs)
                    continue
                if page_no != out[-1][0]:
                    _join_runs(prev, " ")
                    prev.runs.extend(el.runs)
                    continue
        out.append((page_no, el))
    return out


def _rehome_endnotes(chapters: List[Chapter]) -> None:
    """Move a note to the chapter that cites it.

    Notes are collected from the pages they are *printed* on, and in a
    journal article that is a "Notes" section at the very end -- pages after
    the last heading, so they land in the final chapter while every marker
    that refers to them sits in an earlier one. Resolved chapter by chapter,
    both halves then fail: the markers find no note and are downgraded to
    plain superscripts, and the notes sit at the end with nothing pointing
    at them.

    A note is moved only when exactly one chapter cites its label and its own
    chapter does not, so notes genuinely belonging where they are printed --
    footnotes at the foot of their own page -- are left alone.
    """
    cites: Dict[str, List[int]] = {}
    for i, ch in enumerate(chapters):
        for el in ch.elements:
            for run in el.runs:
                if run.noteref:
                    label = run.text.strip()
                    if label:
                        cites.setdefault(label, [])
                        if i not in cites[label]:
                            cites[label].append(i)
    for i, ch in enumerate(chapters):
        for note in list(ch.footnotes):
            label = (note.note_label or "").strip()
            if not label:
                continue
            where = cites.get(label, [])
            if len(where) != 1 or where[0] == i:
                continue
            target = chapters[where[0]]
            # Only if that chapter has no note under this label already.
            if any((f.note_label or "").strip() == label for f in target.footnotes):
                continue
            ch.footnotes.remove(note)
            target.footnotes.append(note)


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

_BARE_NUM_RE = re.compile(r"^\d{1,3}$")


def _split_by_toc(flat, notes_by_page, toc) -> Optional[List[Chapter]]:
    entries = [(int(l), str(t).strip(), int(p) - 1) for l, t, p in toc if int(p) >= 1]
    if len(entries) < 2:
        return None
    top_level = min(e[0] for e in entries)
    tops = [e for e in entries if e[0] == top_level]
    if len(tops) < 2:
        return None
    # A PDF outline built by a scan/digitization batch process sometimes
    # carries bookmarks that are just its own numbering ("01", "02", ...),
    # not real section titles. Splitting on those would produce a book of
    # chapters literally titled "01" through "05"; the font-based heading
    # detector, working from the book's own typeset headings, does far
    # better. Reject the outline outright when every entry is this bare.
    if all(_BARE_NUM_RE.match(t) for _, t, _ in tops):
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
            # An untitled *first* chunk is whatever comes before the book's
            # first real heading -- title-page and colophon content, not a
            # numbered chapter of its own.
            ch.title = "Front Matter" if i == 0 else f"Chapter {i + 1}"
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
            if ends_hyphenated(tail) and piece[:1].islower():
                cur.runs[-1].text = drop_break_hyphen(tail) + piece  # word split across lines
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
    flat, notes_by_page = _build_flow(analyzed, page_images, academic, keep_print_nav)
    flat = _merge_split_headings(flat)
    flat = _merge_split_paragraphs(flat)

    toc = meta.get("_toc") or []
    chapters = _split_by_toc(flat, notes_by_page, toc) if toc else None
    if not chapters:
        chapters = _split_by_headings(flat, notes_by_page)

    if not keep_print_nav:
        chapters = [c for c in chapters if not _PRINT_NAV_RE.match(c.title.strip())] or chapters

    for i, ch in enumerate(chapters):
        if not keep_print_nav:
            _drop_front_matter_lists(ch)
        if academic:
            _extract_endnotes(ch, i)
            _style_references(ch)
            _assign_nav(ch, i)
        ch.footnotes = [f for f in ch.footnotes if f.text.strip()]

    _rehome_endnotes(chapters)
    for ch in chapters:
        _resolve_notes(ch)

    doc = Document(chapters=chapters, language=language)
    doc.title = title or (meta.get("title") or "").strip() or _guess_title(chapters)
    doc.author = author or (meta.get("author") or "").strip() or _guess_author(chapters)

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


def _drop_front_matter_lists(chapter: Chapter) -> None:
    """Remove a "List of Illustrations/Maps/Tables" block from a chapter.

    Unlike Contents/Index, these are frequently *sub*-headings inside a larger
    front-matter chapter (no PDF outline means front matter never gets split
    out on its own), so they are dropped at element level: from the matching
    heading up to -- but not including -- the next heading at the same level
    or shallower.
    """
    els = chapter.elements
    out: List[Element] = []
    skip_level: Optional[int] = None
    for el in els:
        if el.kind == ElementKind.HEADING:
            if skip_level is not None and el.level <= skip_level:
                skip_level = None
            if skip_level is None and _FRONT_LIST_RE.match(el.text.strip()):
                skip_level = el.level
                continue
        if skip_level is not None:
            continue
        out.append(el)
    chapter.elements = out


def _guess_title(chapters: List[Chapter]) -> str:
    """Prefer the *largest*-set heading near the start of the book.

    A title page routinely carries several heading-shaped lines around the
    actual title -- an author name, "Field of Study: ...", "Master's
    Thesis" -- any of which could come first in reading order. The title
    itself is reliably the most prominent (largest) of them, not simply
    whichever heading is encountered first.
    """
    candidates = [
        el for ch in chapters[:2] for el in ch.elements
        if el.kind == ElementKind.HEADING and el.text.strip()
    ]
    if not candidates:
        return "Untitled"
    best = max(candidates, key=lambda e: e.size) if any(c.size > 0 for c in candidates) else candidates[0]

    # A subtitle sits immediately after the title, set noticeably smaller --
    # not body-size-adjacent like the next real chapter heading would be, but
    # in the range a subtitle conventionally uses relative to its title.
    idx = next((i for i, c in enumerate(candidates) if c is best), -1)
    if 0 <= idx < len(candidates) - 1 and best.size > 0:
        nxt = candidates[idx + 1]
        ratio = nxt.size / best.size if best.size else 0
        if 0.4 <= ratio <= 0.75 and len(nxt.text.split()) <= 10:
            return f"{best.text.strip()}: {nxt.text.strip()}"[:160]
    return best.text.strip()[:120]


def _guess_author(chapters: List[Chapter]) -> str:
    """Fall back to a "© Name YYYY" copyright line when the PDF has no
    /Author metadata -- reliable on the colophon page of most books."""
    for ch in chapters[:2]:
        for el in ch.elements[:60]:
            m = _COPYRIGHT_RE.search(el.text)
            if m:
                return m.group(1).strip()
    return ""
