"""Typography normalization for text lifted out of a PDF.

Print typesetting leaves artefacts that read badly on a Kindle and, worse,
break the reader's search and dictionary lookup:

* Ligature glyphs (U+FB00-FB06). "beneﬁt" is a single codepoint, so searching
  for "benefit" or long-pressing the word finds nothing.
* Doubled single quotes used as double quotes (``like this''), a convention
  from Computer Modern-era typesetting.
* Fractions split into numerator, fraction slash and denominator.
* Runs of two or more plain spaces. Justified typesetting sometimes bakes
  extra space characters into the text stream to widen a line; the print
  page looks right, but the extracted text carries the padding as literal
  repeated spaces -- "KEITH  HITCHINS", "the  pursuit  of  education".
"""

from __future__ import annotations

import re
from collections import Counter

_LIGATURES = {
    "ﬀ": "ff", "ﬁ": "fi", "ﬂ": "fl",
    "ﬃ": "ffi", "ﬄ": "ffl", "ﬅ": "st", "ﬆ": "st",
}

_VULGAR = {
    ("1", "2"): "½", ("1", "3"): "⅓", ("2", "3"): "⅔",
    ("1", "4"): "¼", ("3", "4"): "¾", ("1", "8"): "⅛",
    ("3", "8"): "⅜", ("5", "8"): "⅝", ("7", "8"): "⅞",
}

# "1⁄2" written with the dedicated fraction slash; any digit before it is the
# whole number ("51⁄2" is five and a half).
_FRACTION_RE = re.compile(r"(\d)⁄(\d)(?!\d)")

# Two or more plain spaces (never a legitimate typographic device -- unlike a
# non-breaking space or an em-space, which are distinct characters this
# leaves untouched).
_MULTISPACE_RE = re.compile(r"  +")


# What a word broken across a line end can be hyphenated with. The *soft*
# hyphen (U+00AD) is the one that matters in practice: it is invisible unless
# the break actually happens, so typesetting sprinkles it through a paragraph
# and PDF extraction hands it back at the line end exactly where a plain
# hyphen would be. Code that only looks for "-" rejoins none of those, and
# leaves "Litur ghia" and "Dumne zeu" scattered through the text.
_BREAK_HYPHENS = ("-", "\xad", "‐", "‑")


def ends_hyphenated(text: str) -> bool:
    """Does *text* end in a hyphen that splits a word across a line break?"""
    return text.rstrip().endswith(_BREAK_HYPHENS)


# A chapter or part label standing alone over its title: "CHAPTER FOUR",
# "Part II", "Chapter 12". Set small and at the top of the page, it has the
# look of a running head, but it is part of the title.
NUMBER_WORDS = (
    "one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|thirteen|"
    "fourteen|fifteen|sixteen|seventeen|eighteen|nineteen|twenty"
)
CHAPTER_LABEL_RE = re.compile(
    r"^\s*(chapter|part|book)\s+(\d{1,3}|[ivxlc]{1,7}|" + NUMBER_WORDS + r")\s*$",
    re.IGNORECASE,
)


# Stands in for a hard hyphen at a line break until the whole book has been
# read: "self-" over "interest" is the compound "self-interest", while "evo-"
# over "lution" is one word broken for the line. Only the book's own usage
# elsewhere can tell them apart -- see resolve_break_hyphens.
BREAK_MARK = "\ue000"

_MARKED_RE = re.compile(r"([^\W\d_]*)" + BREAK_MARK + r"([^\W\d_]*)(?=([-‐‑][^\W\d_])?)")
_WORD_RE = re.compile(r"[^\W\d_]+(?:[-‐‑][^\W\d_]+)*")
# "self-" compounds keep their hyphen; these few words are written solid.
_SELF_SOLID = {"ish", "ishly", "ishness", "less", "lessly", "lessness", "hood", "same"}


def drop_break_hyphen(text: str) -> str:
    """Strip a trailing line-break hyphen, to rejoin the word it split.

    A soft hyphen only ever marks a break, so it simply goes. A hard one is
    replaced by BREAK_MARK, because it may belong to the word.
    """
    stripped = text.rstrip()
    if not stripped.endswith(_BREAK_HYPHENS):
        return stripped
    return stripped[:-1] + ("" if stripped.endswith("\xad") else BREAK_MARK)


def word_forms(texts) -> Counter:
    """Count how each word is written across *texts*, ignoring marked breaks."""
    counts: Counter = Counter()
    for t in texts:
        counts.update(w.lower() for w in _WORD_RE.findall(_MARKED_RE.sub(" ", t)))
    return counts


def resolve_break_hyphens(text: str, forms: Counter) -> str:
    """Settle each BREAK_MARK in *text*: keep the hyphen where the book
    itself writes the word hyphenated more often than solid."""
    if BREAK_MARK not in text:
        return text

    def repl(m: re.Match) -> str:
        left, right = m.group(1), m.group(2)
        hyphenated = forms.get(f"{left}-{right}".lower(), 0)
        solid = forms.get(f"{left}{right}".lower(), 0)
        if m.group(3):
            keep = True  # one hyphen of several: "brother-" / "in-law"
        elif hyphenated > solid:
            keep = True
        elif hyphenated == solid == 0:
            keep = left.lower() == "self" and right.lower() not in _SELF_SOLID
        else:
            keep = False
        return f"{left}-{right}" if keep else f"{left}{right}"

    return _MARKED_RE.sub(repl, text)


def _fractions(text: str) -> str:
    def repl(m: re.Match) -> str:
        return _VULGAR.get((m.group(1), m.group(2)), f"{m.group(1)}/{m.group(2)}")
    return _FRACTION_RE.sub(repl, text)


def normalize(text: str) -> str:
    """Fold print artefacts into characters a Kindle can search and define."""
    if not text:
        return text
    for lig, plain in _LIGATURES.items():
        if lig in text:
            text = text.replace(lig, plain)
    # Doubled single quotes standing in for double quotes.
    text = text.replace("‘‘", "“").replace("’’", "”")
    text = text.replace("``", "“").replace("''", "”")
    if "⁄" in text:
        text = _fractions(text)
    if "  " in text:
        text = _MULTISPACE_RE.sub(" ", text)
    return text
