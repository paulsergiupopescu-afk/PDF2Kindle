"""Repair one scan's OCR using a second scan of the same book.

Two scans of the same edition are two independent readings of the same ink.
Where their OCR passes disagree, one of them is usually simply right -- and
which one is decidable the same way extract.py already decides it against a
fresh Tesseract pass: by whether the reading is a word of the book's own
language (see spelling.py). A second scan is a far better second opinion than
re-OCRing the same image, because it was produced from a different photograph
by a different engine, so its mistakes do not correlate with the first's.

The hard part is not the words but the *pages*. Two scans of one book are not
page-for-page: a table set across a different number of columns, a blank leaf
included in one and dropped from the other, a cover counted or not, all shift
everything after them. In the pair this was built for, the offset runs +1 for
the first hundred pages, then -1, then -3 for the rest. So the pages are
aligned first, by content, allowing for pages present in one scan and missing
from the other -- and only then are lines within a matched pair compared.

Nothing here trusts the alignment blindly: a page pairing below
_MIN_PAGE_SIMILARITY is dropped rather than repaired from, and the caller
gets the whole mapping so it can be inspected before anything is rewritten.
"""

from __future__ import annotations

import difflib
import logging
import re
from typing import Dict, List, Optional, Sequence

log = logging.getLogger("pdf2kindle.crossref")

# Only pages this similar are trusted as the same printed page. Two scans of
# one page share most of their long words even when both OCR passes are poor;
# two *different* pages of the same book share far fewer.
_MIN_PAGE_SIMILARITY = 0.35
# Only lines this similar are trusted as the same printed line.
_MIN_LINE_SIMILARITY = 0.55
# How far the page alignment may drift from the running diagonal. The observed
# drift in a real pair was 4 pages; this leaves generous room around that
# while keeping the search linear rather than quadratic in the book's length.
_BAND = 24
# Words shorter than this carry too little signal to fingerprint a page with.
_MIN_FINGERPRINT_LEN = 5

_LETTERS = re.compile(r"[^\W\d_]+", re.UNICODE)
# Older Romanian typography uses cedilla forms where the modern orthography
# uses comma-below; the two scans need not agree on which, so fold for
# comparison only (as spelling.py does for dictionary lookups).
_FOLD = str.maketrans({"ş": "ș", "Ş": "Ș", "ţ": "ț", "Ţ": "Ț"})


def fingerprint(text: str) -> set:
    """The set of long words on a page, for comparing one page to another."""
    return {
        w for w in _LETTERS.findall(text.translate(_FOLD).lower())
        if len(w) >= _MIN_FINGERPRINT_LEN
    }


def _similarity(a: set, b: set) -> float:
    """Overlap of two page fingerprints, relative to the smaller one.

    Deliberately not a Jaccard index: one scan often carries a running head,
    a folio, or a footnote the other lost, and a page should still match its
    counterpart when one side simply holds more than the other.
    """
    if not a or not b:
        return 0.0
    return len(a & b) / min(len(a), len(b))


def align_pages(left: Sequence[str], right: Sequence[str]) -> List[Optional[int]]:
    """Map each page of *left* to the page of *right* holding the same print.

    Returns a list as long as *left*, holding the matched index into *right*
    or None where no page matched well enough. Monotonic by construction: a
    banded Needleman-Wunsch over page fingerprints, so pages present in only
    one of the two scans open a gap instead of dragging everything after them
    out of step.
    """
    fl = [fingerprint(t) for t in left]
    fr = [fingerprint(t) for t in right]
    n, m = len(fl), len(fr)
    if not n or not m:
        return [None] * n

    NEG = float("-inf")
    # score[i][j] is only defined for j within _BAND of i's diagonal.
    score: List[Dict[int, float]] = [dict() for _ in range(n + 1)]
    back: List[Dict[int, str]] = [dict() for _ in range(n + 1)]
    score[0][0] = 0.0

    def band(i: int) -> range:
        centre = int(i * m / max(n, 1))
        return range(max(0, centre - _BAND), min(m, centre + _BAND) + 1)

    for i in range(n + 1):
        for j in band(i):
            if i == 0 and j == 0:
                continue
            best, how = NEG, ""
            if i > 0 and j > 0:
                prev = score[i - 1].get(j - 1, NEG)
                if prev > NEG:
                    # A gap costs nothing but gains nothing; a match is worth
                    # its similarity, so the alignment prefers to pair pages
                    # only where the content actually agrees.
                    cand = prev + _similarity(fl[i - 1], fr[j - 1])
                    if cand > best:
                        best, how = cand, "match"
            if i > 0:
                prev = score[i - 1].get(j, NEG)
                if prev > NEG and prev > best:
                    best, how = prev, "skip-left"
            if j > 0:
                prev = score[i].get(j - 1, NEG)
                if prev > NEG and prev > best:
                    best, how = prev, "skip-right"
            if how:
                score[i][j] = best
                back[i][j] = how

    # Walk back from the best end state.
    ends = [(score[n].get(j, NEG), j) for j in band(n)]
    best_end = max(ends, default=(NEG, 0))
    if best_end[0] == NEG:
        return [None] * n
    i, j = n, best_end[1]
    out: List[Optional[int]] = [None] * n
    while i > 0:
        how = back[i].get(j)
        if how == "match":
            if _similarity(fl[i - 1], fr[j - 1]) >= _MIN_PAGE_SIMILARITY:
                out[i - 1] = j - 1
            i, j = i - 1, j - 1
        elif how == "skip-left":
            i -= 1
        elif how == "skip-right":
            j -= 1
        else:
            break
    return out


def fill_gaps(mapping: List[Optional[int]]) -> List[Optional[int]]:
    """Pair up pages the content match could not, where the offset is certain.

    A page whose own OCR failed badly has no words left to fingerprint, so it
    cannot match its counterpart by content -- which is exactly backwards,
    since those are the pages most worth repairing. But its position is still
    known: when the matched pages on either side agree on the offset, and the
    run of unmatched pages between them is exactly as long as the run of free
    pages on the other side, there is only one way they can correspond.

    Where the two sides disagree -- a real divergence, a table set across a
    different number of pages in the two scans -- nothing is filled in, and
    those pages simply go unrepaired. A wrong pairing is harmless anyway:
    align_lines only matches lines that actually read alike, so pages that do
    not correspond yield no line pairs, and nothing is rewritten.
    """
    out = list(mapping)
    n = len(out)
    i = 0
    while i < n:
        if out[i] is not None:
            i += 1
            continue
        run_end = i
        while run_end < n and out[run_end] is None:
            run_end += 1
        before_i = i - 1
        after_i = run_end
        if before_i >= 0 and after_i < n and out[before_i] is not None and out[after_i] is not None:
            before, after = out[before_i], out[after_i]
            gap_left = run_end - i            # unmatched pages on this side
            gap_right = after - before - 1    # free pages on the other side
            if gap_left == gap_right and (after - after_i) == (before - before_i):
                for k in range(gap_left):
                    out[i + k] = before + 1 + k
        i = run_end
    return out


def align_lines(left: Sequence[str], right: Sequence[str]) -> Dict[int, int]:
    """Map line indexes of one page to the same printed lines of the other.

    Both scans photographed the same physical page, so its printed lines are
    the same lines -- but an OCR pass can merge two of them or split one, so
    the correspondence is found rather than assumed. Only pairings that
    actually read alike are kept; the rest are left unmatched, and the caller
    simply doesn't repair those lines.
    """
    matcher = difflib.SequenceMatcher(
        a=[_normalize_line(t) for t in left],
        b=[_normalize_line(t) for t in right],
        autojunk=False,
    )
    out: Dict[int, int] = {}
    for op, i1, i2, j1, j2 in matcher.get_opcodes():
        if op == "equal":
            for k in range(i2 - i1):
                out[i1 + k] = j1 + k
            continue
        if op != "replace":
            continue
        # Within a disagreeing run, pair lines off in order where they still
        # read alike -- this is where the actual OCR differences live, so
        # exact equality is exactly what cannot be required here.
        for k in range(min(i2 - i1, j2 - j1)):
            li, ri = i1 + k, j1 + k
            ratio = difflib.SequenceMatcher(
                None, _normalize_line(left[li]), _normalize_line(right[ri])
            ).ratio()
            if ratio >= _MIN_LINE_SIMILARITY:
                out[li] = ri
    return out


def _normalize_line(text: str) -> str:
    return " ".join(text.translate(_FOLD).lower().split())


# A word the *other* scan also writes somewhere is the book's own vocabulary,
# not this scan's misreading -- even when a Romanian dictionary has never
# heard of it. Below this many occurrences in the other scan, and below this
# share of its occurrences here, the agreement is coincidence (one stray
# misread on that side) rather than corroboration.
_CORROBORATION_MIN = 2
_CORROBORATION_SHARE = 0.2
# An abbreviation is letters cut short by a period ("vol.", "cap.", "pp.").
# No dictionary lists them, so every one of them looks like a misreading to
# the arbitration -- and a scholarly book is full of them.
_ABBREVIATION = re.compile(r"^[^\W\d_]{1,4}\.$", re.UNICODE)

_TOKEN_STRIP = ".,;:()[]{}„”“\"'`!?…«»"


def token_counts(texts: Sequence[str]) -> Dict[str, int]:
    """How often each whitespace-delimited word occurs across *texts*."""
    counts: Dict[str, int] = {}
    for text in texts:
        for raw in text.split():
            w = raw.strip(_TOKEN_STRIP)
            if w:
                counts[w] = counts.get(w, 0) + 1
    return counts


def rejoin_if_split(new: str, ref_counts: Dict[str, int]) -> str:
    """Undo a space the other scan put in the middle of a word.

    Its reading of one occurrence can break a word in two -- "Botezătorul"
    arriving as "Boteză torul", both halves real words to a dictionary, which
    is why nothing about the pair itself gives the split away. What gives it
    away is the other scan's own vocabulary: it writes "Botezătorul" whole
    eleven times elsewhere, so the split is an anomaly of this one occurrence
    and the halves belong back together. A genuine split the other scan got
    right ("celefiră" -> "cele fără") leaves no such trace, because nothing
    anywhere writes "celefără".
    """
    if " " not in new.strip():
        return new
    joined = "".join(new.split())
    if ref_counts.get(joined.strip(_TOKEN_STRIP), 0) >= _CORROBORATION_MIN:
        return joined
    return new


def is_own_vocabulary(old: str, own_counts: Dict[str, int],
                      ref_counts: Dict[str, int]) -> bool:
    """Should *old* be left alone as a word this book actually uses?

    Two things make a reading the book's own rather than a slip of this scan's
    OCR. Either the other scan writes it too -- "Orientalia" is absent from
    any Romanian dictionary, but the second scan spells it that way as well,
    which settles it -- or it is an abbreviation, which no dictionary lists
    and which the arbitration would therefore happily "correct" into whatever
    real word it resembles ("vol." into "voi.").

    Neither test can save a proper name that only this scan spells correctly
    and the other consistently mangles: where both scans are internally
    consistent and disagree, nothing countable distinguishes which is right.
    """
    for token in (old, old.strip(_TOKEN_STRIP)):
        if _ABBREVIATION.match(token):
            return True
    word = old.strip(_TOKEN_STRIP)
    if not word:
        return False
    ref = ref_counts.get(word, 0)
    if ref >= _CORROBORATION_MIN:
        return True
    own = own_counts.get(word, 0)
    return bool(ref) and own > 0 and ref >= _CORROBORATION_SHARE * own
