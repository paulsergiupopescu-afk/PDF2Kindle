"""Typography normalization for text lifted out of a PDF.

Print typesetting leaves artefacts that read badly on a Kindle and, worse,
break the reader's search and dictionary lookup:

* Ligature glyphs (U+FB00-FB06). "beneﬁt" is a single codepoint, so searching
  for "benefit" or long-pressing the word finds nothing.
* Doubled single quotes used as double quotes (``like this''), a convention
  from Computer Modern-era typesetting.
* Fractions split into numerator, fraction slash and denominator.
"""

from __future__ import annotations

import re

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
    return text
