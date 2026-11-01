"""Unit tests for scanned-textbook reconstruction: diacritics, glosses,
running heads, column regions and back-matter chapter openings."""
from pdf2kindle.analyze import PageContent, _strip_furniture, _folio_offset, column_layout
from pdf2kindle.diacritics import transfer
from pdf2kindle.footnotes import find_markers, parse_page_notes
from pdf2kindle.model import Line, Page, Span
from pdf2kindle.structure import _group_paragraphs, _is_heading, _opens_fresh_page


def words_line(words, y=100.0, x=50.0, size=11.0, gap=3.0):
    """A line built from (text, size) pieces laid out left to right, all on
    one baseline -- the way an OCR text layer flattens superscripts."""
    spans = []
    for text, sz in words:
        w = max(1.0, len(text) * sz * 0.5)
        spans.append(Span(text=text, font="Times", size=sz, flags=0, color=0,
                          bbox=(x, y + size - sz, x + w, y + size), origin=(x, y + size)))
        x += w + (gap if not text.endswith(" ") else 0)
    return Line(spans=spans, bbox=(spans[0].bbox[0], y, spans[-1].bbox[2], y + size))


def box_line(text, x0, x1, y, size=11.0):
    sp = Span(text=text, font="Times", size=size, flags=0, color=0,
              bbox=(x0, y, x1, y + size), origin=(x0, y + size))
    return Line(spans=[sp], bbox=(x0, y, x1, y + size))


# --- diacritics ----------------------------------------------------------- #

def test_transfer_copies_only_marks_and_folds_to_macron_in_latin():
    assert transfer("in terra and in Deo", "in terra and in Deó", latin=True) == \
        "in terra and in Deō"
    assert transfer("(con/fi/nes).", "(cón/fi/nés).", latin=True) == "(cōn/fi/nēs)."


def test_transfer_repairs_lookalike_and_keeps_markers_and_length():
    # ABBYY reads a barred o as "d"; the fresh reading's ó repairs it.
    assert transfer("postuld postulate", "postuló postulate", latin=True) == "postulō postulate"
    # A note marker digit the fresh pass misread is never touched.
    orig = "in porcos,1 ut in eos"
    out = transfer(orig, "in porcós,! ut in eos", latin=True)
    assert out == "in porcōs,1 ut in eos" and len(out) == len(orig)


def test_transfer_without_latin_keeps_accent_kind():
    assert transfer("cafe au lait", "café au lait") == "café au lait"


# --- glosses and flattened markers ---------------------------------------- #

def test_run_in_glosses_split_into_separate_notes():
    notes = parse_page_notes([
        words_line([("1", 7.5), ("porcus, ", 9), ("porci, ", 9), ("m. ", 9), ("pig ", 9),
                    ("2", 7.5), ("impetus, ", 9), ("rush ", 9), ("3", 7.5), ("ruo, ", 9),
                    ("fall", 9)], y=500),
    ])
    assert [n.label for n in notes] == ["1", "2", "3"]
    assert notes[1].text.startswith("impetus")


def test_flattened_marker_needs_known_label():
    ln = words_line([("grex ", 11), ("porcorum", 11), ("1", 5.5), (" magnus", 11)])
    assert find_markers(ln, 11.0) == []
    assert find_markers(ln, 11.0, {"1"}) == [(2, "1")]


# --- running heads -------------------------------------------------------- #

def _page(number, lines, height=622.0):
    return Page(number=number, width=383.0, height=height, ocr=False, lines=lines)


def test_running_head_with_folio_is_stripped_even_when_large():
    pages = [_page(n, [box_line(f"Readings {n - 17}", 50, 200, 30, 12.3),
                       box_line("body text " * 5, 50, 330, 80)]) for n in range(20, 40)]
    offset = _folio_offset(pages)
    assert offset == 17
    head = box_line("Summary of Ways to Express Purpose 285", 50, 300, 30, 12.3)
    body = [box_line("body text", 50, 330, 80 + 12 * i) for i in range(5)]
    kept = _strip_furniture([head] + body, 622.0, 11.0, 12.0, repeats={}, folio=302 - offset)
    assert head not in kept and len(kept) == 5


def test_split_running_head_row_goes_together():
    title = box_line("i. The Ordinary of the Mass", 120, 280, 30, 12.3)
    folio = box_line("ZZi", 300, 320, 30, 12.3)  # old-style 331 misread
    body = [box_line("body text", 50, 330, 80 + 12 * i) for i in range(5)]
    kept = _strip_furniture([title, folio] + body, 622.0, 11.0, 12.0, repeats={}, folio=331)
    assert title not in kept and folio not in kept


def test_section_word_closing_a_parenthesis_is_not_a_chapter():
    assert _is_heading(box_line("Section 105).", 50, 120, 100), 11.0) is None


# --- columns -------------------------------------------------------------- #

def test_two_column_list_under_prose_reads_column_by_column():
    prose = [box_line("Prose runs the full measure of the page here.", 20, 330, 60 + 12 * i)
             for i in range(3)]
    left = [box_line(f"left {i}", 20, 140, 120 + 12 * i) for i in range(5)]
    right = [box_line(f"right {i}", 190, 310, 120 + 12 * i) for i in range(5)]
    order = [ln.text for ln, _ in column_layout(prose + right + left, 383.0)]
    assert order[3:8] == [f"left {i}" for i in range(5)]
    assert order[8:] == [f"right {i}" for i in range(5)]


def test_off_centre_gutter_is_found():
    # Text block shifted right: the gutter sits at ~205, not at 383 / 2.
    left = [box_line(f"left entry {i}", 60, 195, 60 + 12 * i) for i in range(8)]
    right = [box_line(f"right entry {i}", 215, 350, 60 + 12 * i) for i in range(8)]
    keys = [k for _, k in column_layout(left + right, 383.0)]
    assert sorted({k[1] for k in keys}) == [0, 1]


def test_hanging_vocabulary_entries_group_per_entry():
    lines = []
    y = 60
    for col_x in (20, 190):
        for i in range(4):
            # Ragged right, as such lists are: entries end where they run out.
            lines.append(box_line(f"verbum{i}, verbi, n.", col_x, col_x + 90 + 7 * i, y))
            lines.append(box_line("word, saying", col_x + 10, col_x + 50 + 9 * i, y + 11))
            y += 22
        y = 60
    ordered = [ln for ln, _ in column_layout(lines, 383.0)]
    page = PageContent(number=1, width=383.0, height=622.0, ocr=False, body_lines=ordered)
    starts = set()
    groups = _group_paragraphs(page, 11.0, 12.0, starts)
    assert len(groups) == 8 and all(len(g) == 2 for g in groups)
    assert all(id(g[0]) in starts for g in groups)


# --- back-matter chapter openings ----------------------------------------- #

def test_display_heading_after_short_page_opens_a_chapter():
    page = PageContent(number=10, width=383.0, height=622.0, ocr=False)
    assert _opens_fresh_page(page, {9: 300.0}, 16.2, 11.2)
    assert not _opens_fresh_page(page, {9: 545.0}, 16.2, 11.2)  # previous page full
    assert not _opens_fresh_page(page, {9: 300.0}, 13.0, 11.2)  # not display size
