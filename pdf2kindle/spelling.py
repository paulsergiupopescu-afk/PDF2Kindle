"""Dictionary lookup, used to judge whether a line of text reads as real words.

This exists to arbitrate between two candidate transcriptions of the same
scanned line: the OCR text layer a PDF already carries, and a fresh OCR pass
of our own (see extract.py's repair pass). Neither is trustworthy on its own,
so the one that reads as more real words in the book's language wins.

Hunspell is used rather than a bare word list because the languages this
matters for are inflected: Romanian's "raţiunile", "existenţiale" and
"descoperă" are all absent from a dictionary's stem list but are perfectly
ordinary words, and scoring them as unknown would drown the signal we are
looking for. Hunspell applies the dictionary's affix rules, so they resolve.

Everything degrades to "unavailable" when hunspell or the language's
dictionary is not installed; the caller then leaves the text alone rather
than guessing.
"""

from __future__ import annotations

import logging
import re
import subprocess
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Set

log = logging.getLogger("pdf2kindle.spelling")

_DICT_DIRS = ("/usr/share/hunspell", "/usr/share/myspell", "/usr/local/share/hunspell")

# Tesseract's 3-letter codes (and a few 2-letter ones) → dictionary prefix.
_LANG_ALIASES = {
    "ron": "ro", "rum": "ro", "eng": "en", "fra": "fr", "fre": "fr",
    "deu": "de", "ger": "de", "spa": "es", "ita": "it", "por": "pt",
    "nld": "nl", "dut": "nl", "pol": "pl", "rus": "ru", "ell": "el",
    "grc": "el", "ukr": "uk", "hun": "hu", "ces": "cs", "cze": "cs",
}

# Older Romanian typography (and OCR of it) uses cedilla forms where the
# modern orthography -- and therefore the dictionary -- uses comma-below.
# Folded for lookup only; the text itself is never rewritten from here.
_LOOKUP_FOLD = str.maketrans({"ş": "ș", "Ş": "Ș", "ţ": "ț", "Ţ": "Ț"})

_WORD_RE = re.compile(r"[^\W\d_]{2,}", re.UNICODE)


def _find_dictionary(lang: str) -> Optional[str]:
    """Return a hunspell dictionary name (e.g. "ro_RO") for *lang*, if installed."""
    prefix = _LANG_ALIASES.get(lang.lower(), lang.lower())[:2]
    for d in _DICT_DIRS:
        for dic in sorted(Path(d).glob("*.dic")) if Path(d).is_dir() else []:
            if dic.stem.lower().startswith(prefix):
                return dic.stem
    return None


class Lexicon:
    """Word-validity lookups for one language, batched and cached.

    Hunspell is invoked in list-misspellings mode over many words at once;
    per-word answers are cached, so a book's repeated vocabulary costs one
    lookup rather than one per occurrence.
    """

    def __init__(self, lang: str) -> None:
        self.dictionary = _find_dictionary(lang)
        self._known: Dict[str, bool] = {}
        self.available = bool(self.dictionary) and self._probe()

    def _probe(self) -> bool:
        try:
            self._ask(["da"])
            return True
        except Exception as exc:  # pragma: no cover - environment dependent
            log.debug("hunspell unavailable: %s", exc)
            return False

    def _ask(self, words: List[str]) -> Set[str]:
        """Return the subset of *words* hunspell does not recognize."""
        proc = subprocess.run(
            ["hunspell", "-d", str(self.dictionary), "-l"],
            input="\n".join(words), capture_output=True, text=True, timeout=60,
        )
        return set(proc.stdout.split())

    def _learn(self, words: Iterable[str]) -> None:
        fresh = sorted({w for w in words if w not in self._known})
        if not fresh:
            return
        try:
            unknown = self._ask(fresh)
        except Exception as exc:  # pragma: no cover - environment dependent
            log.debug("hunspell lookup failed: %s", exc)
            return
        for w in fresh:
            self._known[w] = w not in unknown

    def is_known(self, word: str) -> bool:
        """Is *word* a real word in this language (affixed forms included)?"""
        w = word.translate(_LOOKUP_FOLD)
        if len(w) < 2:
            return False
        if w not in self._known:
            self._learn([w])
        return self._known.get(w, False)

    def score(self, text: str) -> Optional[float]:
        """Fraction of *text*'s words the dictionary recognizes, or None.

        None means "no opinion": too few words to judge. A word broken across
        a line end is not a word -- it is half of one -- so a trailing
        hyphenated fragment is excluded rather than counted as a failure,
        which would otherwise penalize whichever candidate happens to carry
        the line break.
        """
        words = [w.translate(_LOOKUP_FOLD) for w in _WORD_RE.findall(text)]
        if text.rstrip().endswith(("-", "­", "‐")) and words:
            words = words[:-1]
        if len(words) < 4:
            return None
        self._learn(words)
        hits = sum(1 for w in words if self._known.get(w, False))
        return hits / len(words)
