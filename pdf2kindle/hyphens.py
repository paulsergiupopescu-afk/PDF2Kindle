"""Repair hyphens left stranded in the middle of a line.

De-hyphenation elsewhere rejoins a word the PDF breaks across a line end.
That misses text which was *already* broken before it reached the PDF: copy
pasted from an earlier typeset edition, every one of that edition's line-end
hyphens arrives mid-line, followed by a space -- "who by his Death and Res-
urrection", "the pre- cisely". Nothing about the layout marks these, so they
pass straight through to the ebook.

The same "word- word" shape covers three different things, and each needs a
different repair:

* a word split into syllables ("pre- cisely")      -> "precisely"
* a hyphenated compound split at its hyphen
  ("self- portrait", "present- day")                -> "self-portrait"
* a dash typed as a hyphen ("with others- can be")  -> "others—can"

Which one it is gets decided from the book itself first -- if it spells
"present-day" or "resurrection" elsewhere, that settles it -- and only then
from rules of thumb. Running the pass over the whole document, rather than a
paragraph at a time, is what makes that evidence available.

Two more shapes are dashes typed with the hyphen key, and are set as dashes:

* a hyphen with a space on either side ("faith - Sacred Scripture"), which is
  never a hyphen at all;
* in English, a hyphen with no space at all that runs into a function word
  ("his work-that is to say", "life-the gift of God-can be"). A compound's
  second half is a content word, so this is safe outside a short list of
  set phrases ("unheard-of", "how-to") and the particles that form phrasal
  nouns ("built-in", "add-on"), which are left alone.
"""

from __future__ import annotations

import re
from collections import Counter
from typing import Iterator, List, Optional, Set

from .model import Document, InlineRun

_HYPHENS = "\xad\u2010\u2011-"  # plain hyphen last, so it never forms a range
_WORD = r"[^\W\d_]+"

# "Res- urrection": a word, a hyphen, whitespace, then another word.
_STRANDED_RE = re.compile(rf"(?<![\w{_HYPHENS}])({_WORD})[{_HYPHENS}][ \t]+({_WORD})")
# "faith - Sacred": a hyphen standing alone between words. Digits on either
# side are left alone -- "pp. 3 - 5" is a range, not an aside.
_SPACED_RE = re.compile(r"(?<![\s\d\-])[ \t]+-[ \t]+(?=[^\W\d_]|[(\"“‘'])")
_COMPOUND_RE = re.compile(rf"{_WORD}(?:-{_WORD})+")
_WORD_RE = re.compile(_WORD)

# Prefixes that form hyphenated compounds rather than being split off a word.
# Only consulted once the book's own spelling has had its say. Most are also
# ordinary syllables ("pre- cisely", "re- turn"), so they keep their hyphen
# only before a word the book uses on its own; the strong ones hardly ever
# start a plain word, so they keep it regardless.
_STRONG_PREFIXES = frozenset("self non pseudo anti quasi socio".split())
_PREFIXES = _STRONG_PREFIXES | frozenset("""
    pro pre post co bio neo semi multi inter intra counter vice re over under
    all well ill half cross extra ultra
""".split())

# A word like these after the break means the "hyphen" was a dash: a
# compound's second half is a content word, not "can" or "whether".
_DASH_FOLLOWERS = frozenset("""
    and or but nor yet so that which who whom whose whether if as than when
    where while because although though since unless the a an this these those
    it its he she they we you his her their our my your one
    can could would should will shall may might must is are was were be been
    has have had do does did not no on at by for from of to with into upon
    even especially particularly namely indeed also above all both either
    neither everything everyone nothing anything something
""".split())

# "pre- and post-war": a hyphen deliberately left hanging before a conjunction.
_SUSPENSION = frozenset({"and", "or", "nor", "to"})

# "work-that": two words joined by a bare hyphen, not part of a longer chain
# ("up-to-date", "father-and-son" are compounds through and through).
_UNSPACED_RE = re.compile(rf"(?<![\w{_HYPHENS}])({_WORD})-({_WORD})(?![\w{_HYPHENS}])")
# Particles that make phrasal compounds ("built-in", "add-on", "stand-by"),
# and compounds that end in a function word all the same.
_PARTICLES = frozenset({"in", "on", "by", "up", "off", "out", "be", "been"})
_SET_PHRASES = frozenset("""
    unheard-of lean-to set-to how-to would-be make-do to-do can-do so-so no-one
    yes-no either-or cure-all catch-all be-all break-even
""".split())


def _run_lists(doc: Document) -> Iterator[List[InlineRun]]:
    """Each element's runs, with note markers left out of the list."""
    for ch in doc.chapters:
        for el in list(ch.elements) + list(ch.footnotes):
            yield [r for r in el.runs if r.noteref is None and r.text]


class _Evidence:
    """How the book itself spells things, ignoring the broken spots."""

    def __init__(self, texts: list) -> None:
        self.words: Counter = Counter()
        self.compounds: Set[str] = set()
        self.stems: Set[str] = set()
        for text in texts:
            clean = _STRANDED_RE.sub(" ", text)
            self.words.update(w.lower() for w in _WORD_RE.findall(clean))
            self.compounds.update(c.lower() for c in _COMPOUND_RE.findall(clean))
        for w in self.words:
            for k in range(5, len(w) + 1):
                self.stems.add(w[:k])

    def knows_word(self, word: str, break_at: int) -> bool:
        """Does the book use *word*, or an inflection of it ("exchange" for
        "exchanging")? The shared stem must run past the break, or any word
        beginning with the first fragment would count."""
        if self.words[word]:
            return True
        stem = len(word) - 3
        return stem > break_at + 1 and word[:stem] in self.stems


def _resolve(left: str, right: str, after: str, ev: _Evidence, english: bool) -> Optional[str]:
    lw, rw = left.lower(), right.lower()
    if f"{lw}-{rw}" in ev.compounds:
        return f"{left}-{right}"
    if ev.knows_word(lw + rw, len(lw)):
        return left + right
    if right[0].isupper():  # "Pseudo- Dionysius": no syllable starts a capital
        return f"{left}-{right}"
    nxt = after.split(maxsplit=1)
    if rw in _SUSPENSION and nxt and "-" in nxt[0]:
        return None
    if english and rw in _DASH_FOLLOWERS:
        return f"{left}—{right}"
    if lw in _STRONG_PREFIXES or (ev.words[rw] and (lw in _PREFIXES or ev.words[lw])):
        return f"{left}-{right}"
    return left + right


def _is_dash(left: str, right: str) -> bool:
    """Is the bare hyphen in "left-right" a dash rather than a compound?"""
    lw, rw = left.lower(), right.lower()
    return (rw in _DASH_FOLLOWERS and rw not in _PARTICLES and lw not in _PREFIXES
            and f"{lw}-{rw}" not in _SET_PHRASES)


def _unspaced_dash(m: re.Match) -> str:
    return f"{m.group(1)}—{m.group(2)}" if _is_dash(m.group(1), m.group(2)) else m.group(0)


_RUN_END_RE = re.compile(rf"(?<![\w{_HYPHENS}])({_WORD})(-?)$")
_RUN_START_RE = re.compile(rf"^(-?)({_WORD})(?![\w{_HYPHENS}])")


def _unspaced_dash_across(a: InlineRun, b: InlineRun) -> None:
    """The same, where the hyphen sits at an italic boundary: "<em>family
    wage-</em>that is", "Capacity for work</em>-that is"."""
    end, start = _RUN_END_RE.search(a.text), _RUN_START_RE.match(b.text)
    if not end or not start or len(end.group(2) + start.group(1)) != 1:
        return
    if _is_dash(end.group(1), start.group(2)):
        if end.group(2):
            a.text = a.text[:-1] + "—"
        else:
            b.text = "—" + b.text[1:]


def repair_hyphens(doc: Document) -> None:
    """Fix stranded mid-line hyphens, and hyphens typed for dashes, in *doc*."""
    run_lists = list(_run_lists(doc))
    ev = _Evidence([r.text for runs in run_lists for r in runs])
    english = (doc.language or "en").lower().startswith("en")

    def fix(m: re.Match) -> str:
        fixed = _resolve(m.group(1), m.group(2), m.string[m.end():], ev, english)
        return m.group(0) if fixed is None else fixed

    for runs in run_lists:
        for r in runs:
            text = _SPACED_RE.sub(" — ", _STRANDED_RE.sub(fix, r.text))
            r.text = _UNSPACED_RE.sub(_unspaced_dash, text) if english else text
        if english:
            for a, b in zip(runs, runs[1:]):
                _unspaced_dash_across(a, b)
