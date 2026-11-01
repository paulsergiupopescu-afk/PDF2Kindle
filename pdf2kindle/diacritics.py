"""Restore diacritics an existing OCR text layer flattened away.

Scanned books arrive with a text layer from whatever digitized them (ABBYY
FineReader and similar), and a common, systematic failure of that pass is
dropping or mangling accents: a Latin primer's macrons come out as plain
vowels or as look-alike consonants ("laudō" as "laudd", "scīre" as "scire"),
and the same happens to Romanian "ă"/"ș" or French "é" in older scans.

A fresh Tesseract pass usually *sees* the mark, but its own reading is worse
in every other respect -- it reports no bold/italic/superscript flags, misreads
superscript note numbers as punctuation, and invents its own errors. So this
module never takes Tesseract's text. It aligns the two readings of a line
character by character and copies across only the diacritic, onto a letter
the existing layer already agrees on (or onto one of a few known look-alike
misreadings of exactly that vowel). Everything else -- spans, styles, digits,
punctuation -- stays as the existing layer had it.
"""

from __future__ import annotations

import difflib
import unicodedata
from typing import Dict, Optional

# Tesseract's Latin model has no reliable macron glyph class: it reports a
# long vowel's bar as whichever accent it resembles most (acute, grave,
# circumflex, diaeresis). Latin orthography marks vowels with a macron only
# (the breve is a teaching rarity), so in Latin mode every such mark folds to
# a macron.
_MACRON = "̄"
_FOLD_TO_MACRON = {"̀", "́", "̂", "̃", "̄", "̆", "̈"}
_VOWELS = set("aeiouyAEIOUY")

# Misreadings of an accented vowel in the existing layer that are safe to
# repair when the fresh reading puts that accented vowel in the same place:
# ABBYY reads a barred "ō" as "d" or "6" and a barred "ī" as "!", but none of
# these is ever mistaken for a footnote marker (a bare "1" or "l" is, so they
# are left alone).
_LOOKALIKES: Dict[str, str] = {"o": "d6", "i": "!", "O": "D6", "I": "!"}


def _split(ch: str) -> tuple:
    """(base letter, combining marks) of one precomposed character."""
    decomposed = unicodedata.normalize("NFD", ch)
    base = decomposed[:1]
    marks = decomposed[1:]
    return base, marks


def strip_marks(text: str) -> str:
    """*text* with every combining mark removed, one output char per input char."""
    return "".join(_split(c)[0] or c for c in text)


def _compose(base: str, marks: str, latin: bool) -> Optional[str]:
    if latin:
        if base not in _VOWELS or not any(m in _FOLD_TO_MACRON for m in marks):
            return None
        marks = _MACRON
    out = unicodedata.normalize("NFC", base + marks)
    return out if len(out) == 1 else None


def transfer(original: str, candidate: str, *, latin: bool = False) -> str:
    """Copy the diacritics of *candidate* onto *original*, changing nothing else.

    *original* is the existing text layer's reading of a line, *candidate* a
    fresh OCR reading of the same line. A character of *original* gains a
    mark only where the alignment pairs it with the same base letter in
    *candidate* (or with the accented vowel one of its known look-alike
    misreadings stands for), and only if *original* has no mark there
    already. With *latin*, any accent the OCR reports on a vowel is written
    as a macron, and marks on consonants are ignored.
    """
    if not original or not candidate:
        return original
    cand = unicodedata.normalize("NFC", candidate)
    cand_base = strip_marks(cand)
    orig_cmp = strip_marks(original)
    if not any(c != b for c, b in zip(cand, cand_base)):
        return original  # nothing to transfer
    out = list(original)
    matcher = difflib.SequenceMatcher(a=orig_cmp, b=cand_base, autojunk=False)
    for op, i1, i2, j1, j2 in matcher.get_opcodes():
        if op == "equal":
            pairs = zip(range(i1, i2), range(j1, j2))
        elif op == "replace" and i2 - i1 == j2 - j1:
            pairs = zip(range(i1, i2), range(j1, j2))
        else:
            continue
        for i, j in pairs:
            if cand[j] == cand_base[j]:
                continue  # the fresh reading has no mark here
            if original[i] != orig_cmp[i]:
                continue  # already marked in the existing layer
            base, marks = _split(cand[j])
            if original[i] != base:
                if original[i] not in _LOOKALIKES.get(base, ""):
                    continue
                # A look-alike only stands for a vowel inside a word.
                if i == 0 or not original[i - 1].isalpha():
                    continue
            new = _compose(base, marks, latin)
            if new is not None:
                out[i] = new
    return "".join(out)
