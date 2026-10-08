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

import hashlib
import re
from typing import Dict, List, Optional, Tuple

from .analyze import Analyzed, PageContent
from .extract import _column_count
from .footnotes import find_embedded_markers, find_markers, parse_page_notes
from .text import (
    BREAK_MARK,
    CHAPTER_LABEL_RE,
    drop_break_hyphen,
    ends_hyphenated,
    normalize,
    resolve_break_hyphens,
    word_forms,
)
from .document_analysis import DocumentStatistics, PageType
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
_PRINT_NAV_RE = re.compile(
    r"^\s*(contents|table\s+of\s+contents|cuprins|sumar|sommaire|inhalt|"
    r"tabla\s+de\s+contenido|table\s+des\s+matières|"
    # An index, however qualified: "Index", "Subject Index", "Index of
    # Scripture References", "Indexes", "Indice", "Register".
    r"(?:(?:general|subject|name|author|scripture|biblical)\s+)?"
    r"(?:index|indexes|indices|indice|register)(?:\s+of\s+[\w\s,]{1,60})?)\s*$",
    re.IGNORECASE,
)
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
    # A bare "CHAPTER FOUR" label is often set *smaller* than body text;
    # _merge_split_headings joins it to the title that follows.
    if CHAPTER_LABEL_RE.match(text):
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

    hanging = _is_hanging_indent(lines, indent_min, right_edge, text_width)

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
            elif cur and _CAPTION_RE.match(cur[0].text) and _continues_caption(cur[0], ln):
                start_new = False
            elif _BULLET_RE.match(ln.text):
                start_new = True
            elif (cur and _BULLET_RE.match(cur[0].text) and 0 < ln.x0 - cur[0].x0 <= 30
                  and not (prev_short and ln.text[:1].isupper())):
                # A bulleted item's wrapped lines hang under its text; after
                # the item's short last line, an indented line is the next
                # paragraph.
                start_new = False
            elif hanging:
                # Inverted: the indented lines continue an entry, and the
                # one back at the margin opens the next.
                start_new = ln.x0 < prev.x0 - indent_min or (
                    not first_line_indent and ln.x0 <= left_margin + indent_min / 2
                    and prev.x0 <= left_margin + indent_min / 2
                )
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


# An index entry line ends in its page references ("cooperation 29, 48-49,")
# or a cross-reference ("pain see suffering").
_INDEX_LINE_RE = re.compile(r"(\d+(?:\s*[-–]\s*\d+)?(?:ff?\.)?[,;]?|\bsee(?:\s+also)?\s+.+)\s*$")


def _is_index_page(page: PageContent) -> bool:
    """A page of a back-of-book index: nearly every line an entry."""
    lines = [ln for ln in page.body_lines if ln.text.strip()]
    if len(lines) < 8:
        return False
    hits = sum(1 for ln in lines if _INDEX_LINE_RE.search(ln.text.strip()))
    return hits >= 0.7 * len(lines)


def _group_index_entries(page: PageContent) -> List[List[Line]]:
    """One group per index entry: a line indented under the one before it, or
    opening with a page number, continues that entry's page list (or is its
    sub-entry); anything else --
    including the top of the next column -- starts a new entry."""
    groups: List[List[Line]] = []
    prev: Optional[Line] = None
    for ln in page.body_lines:
        if not ln.text.strip():
            continue
        continues = (
            prev is not None and groups
            and ln.y0 > prev.y0  # not the jump to the top of the next column
            and (ln.x0 > groups[-1][0].x0 + 3 or ln.text.strip()[:1].isdigit())
        )
        if continues:
            groups[-1].append(ln)
        else:
            groups.append([ln])
        prev = ln
    return groups


_BULLET_RE = re.compile(r"^\s*[•▪◦‣●■]\s")


def _continues_caption(first: Line, ln: Line) -> bool:
    """Is *ln* a wrapped line of the caption opened by *first*? A caption
    set as "Figure 10.1   The composite origin of…" wraps its text under
    the text, not under the label -- an indent that would otherwise read as
    the start of a new paragraph."""
    starts = [sp.bbox[0] for sp in first.spans if sp.text.strip()]
    return ln.x0 >= first.x0 - 1 and any(abs(ln.x0 - x) <= 3 for x in starts)


_HANGING_MIN_SHARE = 0.25


def _is_hanging_indent(lines: List[Line], indent_min: float, right_edge: float,
                       text_width: float) -> bool:
    """Is this page set with hanging indents (a bibliography, a glossary)?

    In ordinary prose an indented line opens a paragraph, so it follows the
    *short* last line of the paragraph before. Under a hanging indent it is
    the second line of an entry, so it follows a *full* line -- the entry's
    first, set out at the margin. When nearly every indented line on the
    page follows a full line at the margin -- and there are many of them --
    the page is a hanging list.
    """
    if len(lines) < 4:
        return False
    left_margin = min(ln.x0 for ln in lines)
    indented = after_full = 0
    for prev, ln in zip(lines, lines[1:]):
        if ln.x0 > prev.x0 + indent_min:
            indented += 1
            if (prev.x1 >= right_edge - text_width * 0.18
                    and prev.x0 <= left_margin + indent_min / 2):
                after_full += 1
    # Prose indents only each paragraph's first line, a small share of the
    # page; a hanging list indents every continuation line.
    return (indented >= 3 and indented >= _HANGING_MIN_SHARE * len(lines)
            and after_full >= 0.8 * indented)


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
# Table reconstruction
# --------------------------------------------------------------------------- #

def _table_from_group(group: List[Line]) -> Optional[List[List[str]]]:
    """Recognize a simple text table from repeated span x-positions.

    PDF tables often expose each cell as a separate span. We deliberately use
    conservative geometry here: at least three rows, at least two cells per
    row, and stable column positions across rows. If the geometry is
    ambiguous we keep the original paragraphs rather than manufacturing a
    bad table.
    """
    if len(group) < 3:
        return None
    rows: List[List[Tuple[float, str]]] = []
    for line in group:
        cells = [(sp.bbox[0], sp.text.strip()) for sp in line.spans if sp.text.strip()]
        if len(cells) < 2:
            return None
        rows.append(cells)

    # A table should have a consistent number of columns. Allow one missing
    # cell, which is common for a blank cell at the end of a row.
    counts = [len(r) for r in rows]
    columns = max(set(counts), key=counts.count)
    if columns < 2 or sum(c == columns for c in counts) < len(rows) - 1:
        return None

    # Cluster x positions into columns. A cell beginning within this tolerance
    # of a known column is treated as the same column.
    tolerance = 8.0
    centers: List[float] = []
    for row in rows:
        for x, _ in row:
            if not any(abs(x - c) <= tolerance for c in centers):
                centers.append(x)
    centers.sort()
    if len(centers) != columns:
        return None

    aligned: List[List[str]] = []
    for row in rows:
        out = [""] * columns
        for x, text in row:
            idx = min(range(columns), key=lambda i: abs(x - centers[i]))
            if abs(x - centers[idx]) > tolerance or out[idx]:
                return None
            out[idx] = text
        aligned.append(out)

    # Require actual multi-column alignment, not merely styled text fragments.
    # At least two columns must contain content on most rows.
    populated = sum(sum(bool(c) for c in row) >= 2 for row in aligned)
    if populated < len(aligned) - 1:
        return None

    # Long prose lines with stylistic spans are not tables. Tables tend to have
    # short cells and stable row geometry.
    nonempty = [c for row in aligned for c in row if c]
    if not nonempty or sum(len(c.split()) <= 12 for c in nonempty) / len(nonempty) < 0.85:
        return None

    return aligned


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


# The same picture on this many different pages is a publisher's logo stamped
# on every blank verso, or a recurring ornament -- decoration, not a figure.
_REPEATED_IMAGE_PAGES = 3
# A cover carries a title, a subtitle and author names -- a page-one picture
# with more text than this around it is a real first page with a big figure.
_COVER_MAX_WORDS = 60


def _repeated_images(page_images: Dict[int, List[ImageBlock]]) -> set:
    """Content hashes of images that recur on many pages."""
    pages_of: Dict[bytes, set] = {}
    for pno, images in page_images.items():
        for im in images:
            pages_of.setdefault(hashlib.sha1(im.data).digest(), set()).add(pno)
    return {h for h, pages in pages_of.items() if len(pages) >= _REPEATED_IMAGE_PAGES}


def _image_position(im: ImageBlock, boxes: List[Tuple[float, float, float]]) -> int:
    """Index among a page's elements at which an image belongs: before the
    first element in reading order that starts below the image's middle in
    the same column. An image beside or below everything goes last."""
    x0, y0, x1, y1 = im.bbox
    mid = (y0 + y1) / 2
    for i, (top, left, right) in enumerate(boxes):
        if top >= mid and left < x1 and right > x0:
            return i
    return len(boxes)


def _build_flow(
    analyzed: Analyzed,
    page_images: Dict[int, List[ImageBlock]],
    academic: bool,
    keep_print_nav: bool = False,
) -> Tuple[List[Tuple[int, Element]], Dict[int, List[Element]]]:
    flat: List[Tuple[int, Element]] = []
    notes_by_page: Dict[int, List[Element]] = {}
    repeated = _repeated_images(page_images)

    for page in analyzed.pages:
        if not keep_print_nav and _is_headless_toc_page(page):
            continue
        if (page.number == 0
                and any(_covers_page(im, page) for im in page_images.get(0, []))
                and sum(len(ln.text.split()) for ln in page.body_lines) <= _COVER_MAX_WORDS):
            # The cover: its art is already the EPUB cover, and any title
            # text drawn over it would only open the book as stray headings.
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

        page_start = len(flat)
        # (top, left, right) of each element added for this page, so images
        # can be placed among them at their real reading position.
        boxes: List[Tuple[float, float, float]] = []

        def emit(el: Element, group: List[Line]) -> None:
            flat.append((page.number, el))
            boxes.append((group[0].y0, min(ln.x0 for ln in group), max(ln.x1 for ln in group)))

        index_page = _is_index_page(page)
        groups = (_group_index_entries(page) if index_page
                  else _group_paragraphs(page, analyzed.body_size, analyzed.line_height))
        for group in groups:
            if index_page and not (len(group) == 1 and _is_heading(group[0], analyzed.body_size)):
                runs = _paragraph_runs(group, note_prefix)
                if runs:
                    emit(Element(kind=ElementKind.REFERENCE, runs=runs), group)
                continue
            if academic:
                table_rows = _table_from_group(group)
                if table_rows is not None:
                    emit(Element(kind=ElementKind.TABLE, table_rows=table_rows), group)
                    continue

            if len(group) == 1:
                level = _is_heading(group[0], analyzed.body_size)
                size = group[0].dominant_size if level else 0.0
            else:
                level = _is_wrapped_heading(group, analyzed.body_size)
                size = group[0].dominant_size if level else 0.0
            if level:
                runs = _paragraph_runs(group, note_prefix, analyzed.body_size, known_labels)
                emit(Element(kind=ElementKind.HEADING, runs=runs, level=level, size=size), group)
                continue

            runs = _paragraph_runs(group, note_prefix, analyzed.body_size, known_labels)
            if not runs:
                continue

            kind = ElementKind.PARAGRAPH
            if academic:
                if _CAPTION_RE.match(group[0].text):
                    kind = ElementKind.CAPTION
                elif (len(group) >= 2 and group[0].x0 < min(ln.x0 for ln in group[1:]) - 3
                      and not _BULLET_RE.match(group[0].text)):
                    # First line out at the margin, the rest indented: a
                    # hanging-indent entry (see _is_hanging_indent).
                    kind = ElementKind.REFERENCE
                elif _is_blockquote(group, analyzed.body_left, analyzed.body_size, page.width):
                    kind = ElementKind.BLOCKQUOTE
            emit(Element(kind=kind, runs=runs), group)

        images = [
            im for im in _select_images(page_images.get(page.number, []))
            # The scan itself (see _is_scan_background), or a logo or
            # ornament (see _REPEATED_IMAGE_PAGES): not figures.
            if not _is_scan_background(im, page) and hashlib.sha1(im.data).digest() not in repeated
        ]
        # Bottom-most first, so earlier insertions don't shift later positions.
        for im in sorted(images, key=lambda im: im.bbox[1], reverse=True):
            at = _image_position(im, boxes)
            flat.insert(page_start + at, (page.number, Element(kind=ElementKind.IMAGE, image=im)))

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
            # A colon normally closes a heading ("Methods:") -- unless the
            # next line carries on in lowercase, which is a title wrapped
            # after its colon ("The Genesis cosmogony disproven:" over "the
            # universe is ancient and large").
            colon_wrap = ptxt.endswith(":") and cur[:1].islower()
            continues = (
                same_size
                and not new_numbered_section
                and (colon_wrap or not ptxt.endswith((".", "?", "!", ":", ";")))
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
    """Rejoin a paragraph (or block quote) that was broken in two.

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
        # A hanging-indent entry's continuation at the top of the next page
        # has no hanging first line of its own, so it reads as a paragraph
        # or (being indented) a block quote.
        continues_entry = (out and out[-1][1].kind == ElementKind.REFERENCE
                           and el.kind in (ElementKind.PARAGRAPH, ElementKind.BLOCKQUOTE))
        if (out and el.kind in (ElementKind.PARAGRAPH, ElementKind.BLOCKQUOTE, ElementKind.REFERENCE)
                and (out[-1][1].kind == el.kind or continues_entry)):
            prev = out[-1][1]
            ptxt, ctxt = prev.text.rstrip(), el.text.lstrip()
            # The continuation's first letter, past any opening bracket or
            # quote: "(thereby losing something) somewhat as follows".
            first_letter = next((c for c in ctxt[:4] if c.isalpha()), "")
            if ptxt and ctxt and not ptxt.endswith(_SENT_END) and first_letter.islower():
                if ends_hyphenated(ptxt):
                    if prev.runs[-1].noteref is None and not prev.runs[-1].sup:
                        prev.runs[-1].text = drop_break_hyphen(prev.runs[-1].text)
                    prev.runs.extend(el.runs)
                    continue
                # Within a page too for a block quote: an epigraph's lines
                # can be set ragged or right-aligned, and its last line then
                # looks indented like a new paragraph's.
                if page_no != out[-1][0] or el.kind == ElementKind.BLOCKQUOTE:
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


def _drop_classified_print_nav(
    flat: List[Tuple[int, Element]],
    document_stats: Optional[DocumentStatistics],
) -> List[Tuple[int, Element]]:
    """Drop pages confidently classified as printed table-of-contents material.

    The page number is retained in the flat stream until chapter splitting,
    so global document analysis can remove a TOC even when it appears at the
    back of a book rather than in the front matter.
    """
    if document_stats is None:
        return flat
    contents_pages = {
        c.number for c in document_stats.classifications
        if c.page_type == PageType.CONTENTS and c.confidence >= 0.80
    }
    if not contents_pages:
        return flat
    return [(page_no, el) for page_no, el in flat if page_no not in contents_pages]

def _insert_page_breaks(flat: List[Tuple[int, Element]]) -> List[Tuple[int, Element]]:
    """Insert semantic EPUB page-break markers at source PDF page boundaries."""
    out: List[Tuple[int, Element]] = []
    previous_page: Optional[int] = None
    for page_no, element in flat:
        if previous_page is not None and page_no != previous_page:
            marker = Element(
                kind=ElementKind.PAGE_BREAK,
                anchor=f"page-{page_no + 1}",
            )
            marker.level = page_no + 1
            out.append((page_no, marker))
        out.append((page_no, element))
        previous_page = page_no
    return out


# --------------------------------------------------------------------------- #
# Chapter splitting
# --------------------------------------------------------------------------- #

_BARE_NUM_RE = re.compile(r"^\d{1,3}$")


# An outline entry for a *grouping* division rather than a chapter: "Part
# One", "Book II", "Partea întâi". Its children in the outline are the actual
# chapters, and are split on in their own right (see _split_by_toc).
_PART_TITLE_RE = re.compile(
    r"^\s*(parts?|books?|partea|cartea|teil|partie|parte|livre|deel)\b", re.IGNORECASE
)


def _outline_boundaries(entries: List[Tuple[int, str, int]]) -> List[Tuple[int, str, int]]:
    """Pick the outline entries to split chapters on, as (page, title, depth).

    Top-level entries always split. A top-level entry that is a *Part* also
    contributes its direct children: a book organized as Part > Chapter
    otherwise ends up as one enormous file per Part, with every chapter
    title missing from the table of contents. Children of an ordinary
    chapter are its sections and stay inside it.
    """
    top_level = min(e[0] for e in entries)
    out: List[Tuple[int, str, int]] = []
    in_part = False
    for level, title, page in entries:
        if level == top_level:
            in_part = bool(_PART_TITLE_RE.match(title))
            out.append((page, title, 0))
        elif in_part and level == top_level + 1:
            out.append((page, title, 1))
    return out


_SECTION_NUM_RE = re.compile(r"^\s*(\d+(?:\.\d+)+)\.?\s")
# A section heading is never longer than this, even one phrased as a whole
# question and wrapped over five lines.
_SECTION_MAX_WORDS = 70


def _mark_outline_sections(flat: List[Tuple[int, Element]], toc) -> None:
    """Make every section the PDF outline lists a heading in the text.

    Font size finds a section heading set larger than body text, but many
    books set them barely apart from it -- bold at body size, or italic --
    and one that wraps onto a second line never qualifies at all. The
    outline names each section and its page, which is far more reliable:
    the paragraph on that page opening with the section's number (or, for
    an unnumbered one, its title) is the heading. Its level follows its
    depth below the chapter it belongs to.
    """
    entries = [(int(l), str(t).strip(), int(p) - 1) for l, t, p in toc if int(p) >= 1]
    if not entries:
        return
    top = min(e[0] for e in entries)
    chapter_level = top
    in_part = False
    for level, title, page in entries:
        if level == top:
            in_part = bool(_PART_TITLE_RE.match(title))
            chapter_level = top + 1 if in_part else top
            continue
        if level <= chapter_level:
            continue  # a chapter itself: it is split on, not marked
        m = _SECTION_NUM_RE.match(title)
        key = m.group(1) if m else _fold(title)[:24]
        for pno, el in flat:
            if pno < page or pno > page + 1:
                continue
            if el.kind not in (ElementKind.PARAGRAPH, ElementKind.HEADING, ElementKind.BLOCKQUOTE):
                continue
            text = el.text.strip()
            if len(text.split()) > _SECTION_MAX_WORDS:
                continue
            hit = (re.match(re.escape(key) + r"\.?\s", text) if m
                   else _fold(text).startswith(key))
            if hit:
                el.kind = ElementKind.HEADING
                el.level = min(1 + level - chapter_level, 4)
                el.runs = [InlineRun(text=" ".join(text.split()))]
                # The outline's wording is the publisher's own short form
                # for the table of contents.
                el.nav_title = title
                break


def _fold(text: str) -> str:
    return " ".join(re.findall(r"\w+", text.casefold()))


def _join_title_headings(elements: List[Element], title: str) -> List[Element]:
    """Join the chapter's opening headings when together they are its
    outline title. A title set as a question over a subtitle ("Are we going
    anywhere?" / "A static or cyclic universe…") ends in punctuation that
    _merge_split_headings rightly treats as the end of a heading, but the
    outline proves the two lines are one title."""
    want = _fold(re.sub(r"^\s*\d+(?:\.\d+)*\.?\s+", "", title))
    heads = []
    for el in elements[:5]:
        if el.kind != ElementKind.HEADING or el.level != 1:
            break
        heads.append(el)
    for n in range(len(heads), 1, -1):
        for start in range(len(heads) - n + 1):
            group = heads[start:start + n]
            joined = " ".join(h.text.strip() for h in group)
            if _fold(joined).endswith(want) and want:
                group[0].runs = [InlineRun(text=joined)]
                return [el for el in elements if not any(el is h for h in group[1:])]
    return elements


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
    # Outline order, not page order, decides nesting; a stable sort by page
    # keeps a Part ahead of a first chapter that starts on the same page.
    boundaries = sorted(_outline_boundaries(entries), key=lambda b: b[0])

    chapters: List[Chapter] = []
    for i, (start_page, title, depth) in enumerate(boundaries):
        end_page = boundaries[i + 1][0] if i + 1 < len(boundaries) else 10 ** 9
        ch = Chapter(title=title or f"Chapter {i + 1}", depth=depth)
        ch.elements = _join_title_headings(
            [el for pno, el in flat if start_page <= pno < end_page], title
        )
        for pno, notes in notes_by_page.items():
            if start_page <= pno < end_page:
                ch.footnotes.extend(notes)
        if ch.elements:
            chapters.append(ch)
        elif depth == 0 and i + 1 < len(boundaries) and boundaries[i + 1][2] == 1:
            # A Part whose chapter starts on its own page (or one whose
            # divider page was blank) still needs an entry of its own for
            # its chapters to nest under.
            ch.elements = [Element(kind=ElementKind.HEADING, level=1,
                                   runs=[InlineRun(text=ch.title)])]
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
    """Anchor sub-headings, build the nested TOC, and link section references."""
    levels = [el.level for el in chapter.elements if el.kind == ElementKind.HEADING]
    if not levels:
        return
    top = min(levels)
    k = 0
    targets: Dict[str, str] = {}
    for el in chapter.elements:
        if el.kind == ElementKind.HEADING and el.level > top:
            el.anchor = f"sec-{idx}-{k}"
            chapter.subheads.append(SubHead(anchor=el.anchor, title=el.nav_title or el.text.strip(),
                                            level=el.level))
            m = re.match(r"^\s*(\d+(?:\.\d+){0,3})\.?\s+", el.text.strip())
            if m:
                targets[m.group(1)] = el.anchor
            k += 1

    # Only link references for which a concrete local target exists. This is
    # intentionally conservative: a false hyperlink is worse than plain text.
    ref_re = re.compile(r"\b(?:section|sec\.)\s+(\d+(?:\.\d+){0,3})\b", re.I)
    for el in chapter.elements:
        if el.kind not in (ElementKind.PARAGRAPH, ElementKind.BLOCKQUOTE, ElementKind.CAPTION):
            continue
        rebuilt: List[InlineRun] = []
        for run in el.runs:
            if run.href or run.noteref:
                rebuilt.append(run)
                continue
            pos = 0
            for m in ref_re.finditer(run.text):
                target = targets.get(m.group(1))
                if not target:
                    continue
                if m.start() > pos:
                    rebuilt.append(InlineRun(text=run.text[pos:m.start()], bold=run.bold, italic=run.italic))
                rebuilt.append(InlineRun(text=m.group(0), bold=run.bold, italic=run.italic, href=f"#{target}"))
                pos = m.end()
            if pos:
                if pos < len(run.text):
                    rebuilt.append(InlineRun(text=run.text[pos:], bold=run.bold, italic=run.italic))
            else:
                rebuilt.append(run)
        el.runs = rebuilt


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
    document_stats: Optional[DocumentStatistics] = None,
    preserve_page_breaks: bool = False,
) -> Document:
    academic = profile == "academic"
    flat, notes_by_page = _build_flow(analyzed, page_images, academic, keep_print_nav)
    if not keep_print_nav:
        flat = _drop_classified_print_nav(flat, document_stats)
    flat = _merge_split_headings(flat)
    flat = _merge_split_paragraphs(flat)
    if preserve_page_breaks:
        flat = _insert_page_breaks(flat)

    toc = meta.get("_toc") or []
    if toc:
        _mark_outline_sections(flat, toc)
    chapters = _split_by_toc(flat, notes_by_page, toc) if toc else None
    if not chapters:
        chapters = _split_by_headings(flat, notes_by_page)

    if not keep_print_nav:
        chapters = [c for c in chapters if not _PRINT_NAV_RE.match(c.title.strip())] or chapters

    # Before navigation is built, so a byline never becomes a TOC entry.
    _mark_bylines(chapters, author or (meta.get("author") or ""))

    for i, ch in enumerate(chapters):
        if not keep_print_nav:
            _drop_front_matter_lists(ch)
        if academic:
            _extract_endnotes(ch, i)
            _style_references(ch)
            _assign_nav(ch, i)
        ch.footnotes = [f for f in ch.footnotes if f.text.strip()]
        _resolve_notes(ch)

    _settle_break_hyphens(chapters)

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


def _mark_bylines(chapters: List[Chapter], authors: str) -> None:
    """Turn an author's name set under a chapter or part title -- which,
    being large or alone on its line, reads as a heading or as the
    chapter's opening paragraph -- into a byline."""
    names = {
        re.sub(r"\s+", " ", n).strip().casefold()
        for n in re.split(r",|;|&|\band\b", authors) if n.strip()
    }
    if not names:
        return
    for ch in chapters:
        for el in ch.elements[:4]:
            if (el.kind in (ElementKind.HEADING, ElementKind.PARAGRAPH)
                    and re.sub(r"\s+", " ", el.text).strip().casefold() in names):
                el.kind = ElementKind.BYLINE
                el.level = 0


def _settle_break_hyphens(chapters: List[Chapter]) -> None:
    """Decide every line-break hyphen held back by drop_break_hyphen, now
    that the whole book is available to show how each word is spelled."""
    elements = [el for ch in chapters for el in ch.elements + ch.footnotes]
    forms = word_forms(r.text for el in elements for r in el.runs)
    for el in elements:
        runs = el.runs
        for i, run in enumerate(runs):
            # A break at the very end of a run continues into the next one
            # (the continuation was set in a different style).
            if run.text.endswith(BREAK_MARK) and i + 1 < len(runs):
                left = re.search(r"[^\W\d_]*$", run.text[:-1]).group(0)
                right = re.match(r"[^\W\d_]*", runs[i + 1].text).group(0)
                settled = resolve_break_hyphens(f"{left}{BREAK_MARK}{right}", forms)
                run.text = run.text[:-1] + ("-" if settled[len(left):len(left) + 1] == "-" else "")
            run.text = resolve_break_hyphens(run.text, forms).replace(BREAK_MARK, "")
    for ch in chapters:
        ch.title = resolve_break_hyphens(ch.title, forms).replace(BREAK_MARK, "")
        for sh in ch.subheads:
            sh.title = resolve_break_hyphens(sh.title, forms).replace(BREAK_MARK, "")


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
