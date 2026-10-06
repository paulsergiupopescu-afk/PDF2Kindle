"""Re-attach free-standing accent glyphs to the letters they belong to.

Transliterated Punjabi, Sanskrit or Arabic is often set with its macrons and
dots drawn as separate glyphs: an ordinary hyphen or full stop, in the text
font and at the text size, shifted a few points up or down so it lands over or
under its letter. Text extraction has no notion of an accent. The shift is
enough to make the glyph a line of its own, slotted in among the lines of the
row it decorates, so one printed row arrives as

    ... basically elements - air
    - -                                  <- the macrons over "pāṇī"
    (pavan.), water (pan.ı), ﬁ
    - -                                  <- the macrons over "pātāl"
    re (agni), earth (patal), and their

which prints as stray "- -" paragraphs, splits a sentence in two, cuts "fire"
into "ﬁ" and "re", and loses the diacritics (pan.ı for paṇī).

Each accent is found by geometry -- a hyphen or dot raised or lowered off the
baseline whose centre sits over a letter -- and folded into that letter as a
combining character, which composes to the precomposed form (a + macron = ā).
The accent glyph is then removed. Nothing else is touched: a hyphen or full
stop sitting on its line's baseline is ordinary punctuation and never moves.
"""

from __future__ import annotations

import unicodedata
from collections import Counter
from typing import Dict, List, Optional, Set, Tuple

# Glyphs a typesetter borrows as accents: hyphen-likes and macron signs, and dots.
_MACRON_GLYPHS = frozenset(
    "-\N{HYPHEN}\N{NON-BREAKING HYPHEN}\N{MACRON}\N{MODIFIER LETTER MACRON}"
)
_DOT_GLYPHS = frozenset(".\N{MIDDLE DOT}\N{DOT ABOVE}")
_ACCENT_GLYPHS = _MACRON_GLYPHS | _DOT_GLYPHS

# Combining marks, by (glyph kind, where it sits relative to its letter).
_MARK = {
    ("macron", "above"): "\N{COMBINING MACRON}",
    ("dot", "above"): "\N{COMBINING DOT ABOVE}",
    ("dot", "below"): "\N{COMBINING DOT BELOW}",
    ("macron", "below"): "\N{COMBINING MACRON BELOW}",
}

# How far off the baseline an accent must sit, as a fraction of the font size.
# Printed punctuation rides the baseline; every accent in the books this was
# written against is at least 0.15 em off it, and nothing real is.
_MIN_RAISE = 0.15
_MIN_DROP = 0.10
# ...and how far it can sit and still belong to a letter on the row it shifts to.
_MAX_OFFSET = 0.8
# An accent is drawn at the text size; a much smaller glyph is a superscript.
_MIN_SIZE_RATIO = 0.8


class _Glyph:
    """One character of a raw line, with the geometry the matching needs."""

    __slots__ = ("char", "row", "cx", "x0", "x1", "oy", "size")

    def __init__(self, char: dict, span: dict, row: int) -> None:
        self.char = char
        self.row = row  # index of the line it came from
        x0, _, x1, _ = char["bbox"]
        self.x0, self.x1, self.cx = x0, x1, (x0 + x1) / 2.0
        self.oy = char["origin"][1]
        self.size = span["size"]

    @property
    def text(self) -> str:
        return self.char["c"]


def _baseline(line: dict) -> Optional[float]:
    """A row's baseline: the commonest origin among its letters and digits.

    None when the line has none (a line of nothing but accent glyphs).
    """
    ys: Counter = Counter()
    for sd in line["spans"]:
        for c in sd["chars"]:
            if c["c"].isalnum():
                ys[round(c["origin"][1], 1)] += 1
    return ys.most_common(1)[0][0] if ys else None


def _kind(text: str) -> str:
    return "macron" if text in _MACRON_GLYPHS else "dot"


def _find_base(accent: _Glyph, letters: List[Tuple[_Glyph, float]]):
    """The letter *accent* decorates, and whether it sits above or below it."""
    best = None
    for letter, base_y in letters:
        if letter.size * _MIN_SIZE_RATIO > accent.size or accent.size * _MIN_SIZE_RATIO > letter.size:
            continue
        if not letter.x0 <= accent.cx <= letter.x1:
            continue
        rise = base_y - accent.oy  # positive: the accent is above the letter's baseline
        if rise >= _MIN_RAISE * accent.size:
            where = "above"
        elif -rise >= _MIN_DROP * accent.size:
            where = "below"
        else:
            continue
        if abs(rise) > _MAX_OFFSET * accent.size:
            continue
        key = abs(rise)
        if best is None or key < best[0]:
            best = (key, letter, where)
    return None if best is None else (best[1], best[2])


def _off_baseline_accents(lines: List[dict]) -> List[_Glyph]:
    """Every accent-shaped glyph that is not riding its own line's baseline.

    A printed hyphen or full stop is on its line's baseline; a line of nothing
    but such glyphs has none, so anything in it is shifted by definition. Most
    pages have no such glyph, and then nothing else is looked at.
    """
    found: List[_Glyph] = []
    for i, ld in enumerate(lines):
        if not any(c["c"] in _ACCENT_GLYPHS for sd in ld["spans"] for c in sd["chars"]):
            continue
        own = _baseline(ld)
        for sd in ld["spans"]:
            for c in sd["chars"]:
                if c["c"] in _ACCENT_GLYPHS and (
                    own is None or abs(c["origin"][1] - own) >= _MIN_DROP * sd["size"]
                ):
                    found.append(_Glyph(c, sd, i))
    return found


def _letters_in_reach(lines: List[dict], accents: List[_Glyph]) -> List[Tuple[_Glyph, float]]:
    """The letters, with their row's baseline, that some accent could sit on."""
    reach = [(a.oy, _MAX_OFFSET * a.size) for a in accents]
    letters: List[Tuple[_Glyph, float]] = []
    for i, ld in enumerate(lines):
        top, bottom = ld["bbox"][1], ld["bbox"][3]
        if not any(top - r <= y <= bottom + r for y, r in reach):
            continue
        base_y = _baseline(ld)
        if base_y is None:
            continue
        letters.extend(
            (_Glyph(c, sd, i), base_y)
            for sd in ld["spans"] for c in sd["chars"] if c["c"].isalpha()
        )
    return letters


def fold_accents(lines: List[dict]) -> List[dict]:
    """Fold free-standing accents in PyMuPDF "rawdict" *lines* into their letters.

    Returns the lines to keep, in their original order: each accent glyph is
    removed and its combining mark appended to the letter it sits on, and a
    line left with no glyphs is dropped. Lines with no accents come back
    unchanged. The dicts are edited in place.
    """
    accents = _off_baseline_accents(lines)
    if not accents:
        return lines

    letters = _letters_in_reach(lines, accents)
    marks: Dict[int, List[str]] = {}
    base_of: Dict[int, _Glyph] = {}
    removed: Set[int] = set()
    touched: Set[int] = set()
    for accent in accents:
        found = _find_base(accent, letters)
        if found is None:
            continue
        letter, where = found
        marks.setdefault(id(letter), []).append(_MARK[(_kind(accent.text), where)])
        base_of[id(letter)] = letter
        removed.add(id(accent.char))
        touched.add(accent.row)
    if not removed:
        return lines

    for key, combining in marks.items():
        base = base_of[key].char
        # A dotless i carries its macron as an i: dotless-i + macron is not "ī".
        text = "i" if base["c"] == "\N{LATIN SMALL LETTER DOTLESS I}" else base["c"]
        base["c"] = unicodedata.normalize("NFC", text + "".join(combining))

    kept: List[dict] = []
    for i, ld in enumerate(lines):
        if i not in touched:
            kept.append(ld)
            continue
        for sd in ld["spans"]:
            if not any(id(c) in removed for c in sd["chars"]):
                continue
            sd["chars"] = [c for c in sd["chars"] if id(c) not in removed]
            if sd["chars"]:
                # The span's origin and box were the removed accent's, if it led.
                sd["origin"] = sd["chars"][0]["origin"]
                sd["bbox"] = _union([c["bbox"] for c in sd["chars"]])
        ld["spans"] = [sd for sd in ld["spans"] if sd["chars"]]
        # A row of accents is "- -": drop the space left between them as well.
        if not any(c["c"].strip() for sd in ld["spans"] for c in sd["chars"]):
            continue
        ld["bbox"] = _union([c["bbox"] for sd in ld["spans"] for c in sd["chars"]])
        kept.append(ld)
    return kept


def _union(boxes: List[Tuple[float, float, float, float]]) -> Tuple[float, float, float, float]:
    return (
        min(b[0] for b in boxes), min(b[1] for b in boxes),
        max(b[2] for b in boxes), max(b[3] for b in boxes),
    )
