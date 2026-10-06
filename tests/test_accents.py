"""Free-standing accent glyphs (macrons, dots) are folded into their letters."""
import re
import unicodedata
import zipfile

import pytest

from pdf2kindle import ConvertOptions, convert_pdf
from pdf2kindle.accents import fold_accents
from tests.make_accents import main as make_accents

ADVANCE = 5.0  # width given to every glyph in the hand-built lines below


def _char(c, x0, oy):
    return {"c": c, "origin": (x0, oy), "bbox": (x0, oy - 8.0, x0 + ADVANCE, oy + 2.5)}


def _line(chars, size=10.5):
    """A rawdict-shaped line holding *chars* in one span."""
    xs0 = min(c["bbox"][0] for c in chars)
    xs1 = max(c["bbox"][2] for c in chars)
    ys0 = min(c["bbox"][1] for c in chars)
    ys1 = max(c["bbox"][3] for c in chars)
    span = {"font": "f", "size": size, "flags": 0, "color": 0,
            "origin": chars[0]["origin"], "bbox": (xs0, ys0, xs1, ys1), "chars": chars}
    return {"bbox": (xs0, ys0, xs1, ys1), "spans": [span]}


def _row(text, x0=100.0, oy=200.0, **kw):
    return _line([_char(c, x0 + i * ADVANCE, oy) for i, c in enumerate(text)], **kw)


def _glyph(c, over, oy, **kw):
    """A lone accent glyph centred over the letter at index *over* of a row at x=100."""
    return _line([_char(c, 100.0 + over * ADVANCE, oy)], **kw)


def _text(line):
    return "".join(c["c"] for s in line["spans"] for c in s["chars"])


def test_a_raised_hyphen_over_a_letter_becomes_a_macron():
    base = _row("pani")
    kept = fold_accents([_glyph("-", 1, 196.5), base])
    assert kept == [base]
    assert _text(base) == "pāni"


def test_a_lowered_dot_under_a_letter_becomes_a_dot_below():
    base = _row("pani")
    kept = fold_accents([base, _glyph(".", 2, 202.0)])
    assert kept == [base]
    assert _text(base) == "paṇi"


def test_an_accent_inside_its_own_line_is_folded_too():
    """The dot-below of "pavan." is part of the same text run as the letter."""
    chars = [_char(c, 100.0 + i * ADVANCE, 200.0) for i, c in enumerate("pan")]
    chars.insert(3, _char(".", 100.0 + 2 * ADVANCE, 202.0))  # under the n, zero advance
    line = _line(chars)
    assert fold_accents([line]) == [line]
    assert _text(line) == "paṇ"


def test_a_dotless_i_with_a_macron_is_an_i_with_a_macron():
    base = _row("pı")
    fold_accents([_glyph("-", 1, 196.5), base])
    assert _text(base) == "pī"


def test_both_marks_on_one_letter_are_kept():
    base = _row("pr")
    fold_accents([_glyph("-", 1, 196.5), base, _glyph(".", 1, 202.0)])
    # r with a macron and a dot below, composed as far as Unicode has a letter for it
    assert unicodedata.normalize("NFC", _text(base)) == "p\u1e5d"
    assert unicodedata.is_normalized("NFC", _text(base))


def test_punctuation_on_the_baseline_is_not_an_accent():
    line = _row("ab-c. d")
    before = _text(line)
    assert fold_accents([line]) == [line]
    assert _text(line) == before


def test_an_accent_with_no_letter_under_it_is_left_alone():
    """A row of dots that is a real ellipsis must survive."""
    dots = _line([_char(".", 300.0 + i * 2 * ADVANCE, 400.0) for i in range(3)])
    base = _row("some text")
    assert fold_accents([base, dots]) == [base, dots]


def test_a_hyphen_ending_the_row_above_is_not_an_accent_for_the_row_below():
    """Rows are a full line apart; only a glyph a few points off the baseline
    can be an accent."""
    above = _row("abc-", oy=200.0)
    below = _row("xyzw", oy=212.0)
    assert fold_accents([above, below]) == [above, below]
    assert _text(below) == "xyzw"


def test_a_much_smaller_glyph_is_a_superscript_not_an_accent():
    base = _row("pani")
    small = _glyph("-", 1, 196.5, size=6.0)
    assert fold_accents([small, base]) == [small, base]
    assert _text(base) == "pani"


def test_removing_a_leading_accent_resets_the_span_to_the_text_that_is_left():
    """A line that opens with an accent ("-hib") must not keep its origin."""
    base = _row("sa", x0=100.0, oy=200.0)
    lead = _line([_char("-", 107.0, 196.5)] + [_char(c, 110.0 + i * ADVANCE, 200.0)
                                               for i, c in enumerate("hib")])
    fold_accents([base, lead])
    span = lead["spans"][0]
    assert _text(lead) == "hib"
    assert span["origin"] == (110.0, 200.0)
    assert lead["bbox"][0] == 110.0


def test_a_row_of_accents_and_the_space_between_them_vanishes():
    base = _row("papa")
    marks = _line([_char("-", 105.0, 196.5), _char(" ", 110.0, 196.5), _char("-", 115.0, 196.5)])
    assert fold_accents([marks, base]) == [base]
    assert _text(base) == "pāpā"


def test_lines_without_accents_come_back_untouched():
    a, b = _row("one"), _row("two", oy=214.0)
    assert fold_accents([a, b]) == [a, b]


# --------------------------------------------------------------------------- #
# Recombining a row the accents cut in two
# --------------------------------------------------------------------------- #

def _frag(text, x0, x1, y0=100.0, y1=110.5, oy=108.0, size=10.5):
    from pdf2kindle.model import Line, Span

    span = Span(text=text, font="f", size=size, flags=0, color=0,
                bbox=(x0, y0, x1, y1), origin=(x0, oy))
    return Line(spans=[span], bbox=(x0, y0, x1, y1))


def test_row_halves_that_abut_are_one_word():
    from pdf2kindle.extract import _merge_same_row_lines

    merged = _merge_same_row_lines([_frag("water (pani), fi", 43.9, 159.9), _frag("re (agni)", 160.0, 200.0)])
    assert [m.text for m in merged] == ["water (pani), fire (agni)"]


def test_row_halves_with_a_real_gap_keep_their_word_space():
    from pdf2kindle.extract import _merge_same_row_lines

    merged = _merge_same_row_lines([_frag("water", 43.9, 80.0), _frag("fire", 90.0, 120.0)])
    assert [m.text for m in merged] == ["water fire"]


def test_fragments_whose_boxes_differ_but_whose_baseline_is_shared_are_one_row():
    """A fragment holding an accented letter has a taller box, so its top is
    several points off its neighbour's."""
    from pdf2kindle.extract import _merge_same_row_lines

    tall = _frag("(cord", 43.9, 65.5, y0=213.1, y1=223.6, oy=221.2)
    short = _frag(" kala)", 65.6, 100.0, y0=209.4, y1=223.7, oy=221.2)
    assert [m.text for m in _merge_same_row_lines([tall, short])] == ["(cord kala)"]


def test_consecutive_rows_are_not_merged():
    from pdf2kindle.extract import _merge_same_row_lines

    rows = [_frag("first row", 43.9, 150.0, y0=100.0, y1=110.5, oy=108.0),
            _frag("second row", 43.9, 150.0, y0=112.0, y1=122.5, oy=120.0)]
    assert len(_merge_same_row_lines(rows)) == 2


def test_a_row_end_space_lying_over_the_next_fragment_is_dropped():
    """"sā " ends in the space the producer appends to every row, and that
    space is drawn over the "hib" that follows: the word is "sāhib"."""
    from pdf2kindle.extract import _merge_same_row_lines

    merged = _merge_same_row_lines([_frag("the nishān sā ", 43.9, 287.9), _frag("hib (Sikh", 282.1, 324.4)])
    assert [m.text for m in merged] == ["the nishān sāhib (Sikh"]


def test_a_real_trailing_space_before_the_next_word_is_kept():
    from pdf2kindle.extract import _merge_same_row_lines

    merged = _merge_same_row_lines([_frag("water ", 43.9, 80.0), _frag("fire", 80.0, 100.0)])
    assert [m.text for m in merged] == ["water fire"]


# --------------------------------------------------------------------------- #
# A sentence cut by a page turn that resumes with a parenthesis
# --------------------------------------------------------------------------- #

def test_page_turn_before_an_opening_parenthesis_rejoins():
    from pdf2kindle.model import Element, ElementKind, InlineRun
    from pdf2kindle.structure import _merge_split_paragraphs

    def para(text):
        return Element(kind=ElementKind.PARAGRAPH, runs=[InlineRun(text=text)])

    merged = _merge_split_paragraphs([(4, para("his hero, Bhagat Puran Singh")), (5, para("(1904–1992). The Society grew."))])
    assert len(merged) == 1
    assert merged[0][1].text == "his hero, Bhagat Puran Singh (1904–1992). The Society grew."

    # ...but only across a page turn, and only when the sentence was unfinished.
    same_page = _merge_split_paragraphs([(4, para("First paragraph ends here")), (4, para("(a) a list item."))])
    assert len(same_page) == 2
    finished = _merge_split_paragraphs([(4, para("The sentence is over.")), (5, para("(Aside) a new paragraph."))])
    assert len(finished) == 2


# --------------------------------------------------------------------------- #
# End to end
# --------------------------------------------------------------------------- #

@pytest.fixture(scope="module")
def accents_epub(tmp_path_factory):
    src = tmp_path_factory.mktemp("data") / "accents.pdf"
    make_accents(str(src))
    out = tmp_path_factory.mktemp("out") / "accents.epub"
    convert_pdf(str(src), str(out), ConvertOptions(ocr="never"))
    return str(out)


def _body(epub):
    with zipfile.ZipFile(epub) as z:
        name = next(n for n in z.namelist() if re.search(r"chap_\d+\.xhtml$", n))
        return z.read(name).decode("utf-8")


def test_accent_glyphs_do_not_become_stray_paragraphs(accents_epub):
    body = _body(accents_epub)
    assert not re.search(r"<(p|blockquote)[^>]*>\s*[-.\s]+</(p|blockquote)>", body)
    assert "- -" not in re.sub(r"<[^>]+>", "", body)


def test_a_row_cut_by_accents_stays_one_sentence(accents_epub):
    paragraphs = re.findall(r"<p[^>]*>(.*?)</p>", _body(accents_epub), re.S)
    holding = [p for p in paragraphs if "pāṇī" in p]
    assert len(holding) == 1  # not cut at "fi" / "re"
    assert "water (pāṇī), fire (agni), earth (pātāl), and their compounds." in holding[0]
    assert not [p for p in paragraphs if p.rstrip().endswith(" fi") or p.startswith("re ")]


def test_the_diacritics_are_restored_on_their_letters(accents_epub):
    text = re.sub(r"<[^>]+>", "", _body(accents_epub))
    assert "pāṇī" in text and "pātāl" in text
    assert "pani" not in text and "patal" not in text
