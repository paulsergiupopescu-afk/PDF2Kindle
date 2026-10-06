#!/usr/bin/env python3
"""Coherency test for corrected pages (see corrections.py).

    python coherency.py CORRECTIONS_DIR [--from N --to M] [--quiet]

Checks, page by page and across pages:
  syntax     every line is a known element; markers and angs well-formed
  notes      notes numbered 1..n on each page; every reference has its note,
             every note is referenced, references appear in order
  stanzas    stanza numbers follow the translator's grammar ([1], [1-Pause],
             [4-5-25]); within a run of hymns the running count advances by 1;
             the Japu's pauris run 1..38
  angs       original-text page numbers strictly increase
  flow       a paragraph that opens a page mid-sentence is marked p+; a new
             paragraph does not start in lower case
  headings   hymn titles read as titles
  text       OCR debris (~ < > { } | @ ®), digits glued to words, doubled
             words, unbalanced brackets, unknown words (hunspell + book lexicon)

Errors make the exit status non-zero; warnings are listed for a human eye.
"""

from __future__ import annotations

import argparse
import os
import re
import sys
from collections import defaultdict
from typing import Dict, List, Tuple

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

from corrections import ITAL_CLOSE, ITAL_OPEN, load_dir  # noqa: E402
from headings import is_heading  # noqa: E402
from lexicon import Lexicon  # noqa: E402

MARKER = re.compile(r"^(\d{1,3}(-\d{1,3}){0,7}|\d{1,2}-Pause(-\d{1,3}){0,3}|Pause)$")
DEBRIS = re.compile(r"[~<>{}|@®\\^_]|\S\[\S|\]\S")
GLUED = re.compile(r"\b[A-Za-z]{2,}\d+\b|\b\d+[A-Za-z]{2,}\b")
DOUBLE = re.compile(r"\b(\w+) \1\b", re.I)
PH = re.compile("⁣(\\d+)⁣")
INDIC = re.compile(r"[ऀ-ॿ਀-੿]")
OK_DOUBLE = {"that", "had", "blessed", "very", "many", "far", "is", "o", "nānak", "pure"}


class Report:
    def __init__(self) -> None:
        self.items: List[Tuple[str, str, int, str]] = []

    def err(self, cat: str, page: int, msg: str) -> None:
        self.items.append(("ERROR", cat, page, msg))

    def warn(self, cat: str, page: int, msg: str) -> None:
        self.items.append(("warn", cat, page, msg))


def plain(s: str) -> str:
    return PH.sub("", s).replace(ITAL_OPEN, "").replace(ITAL_CLOSE, "").replace(" ", " ")


def check(pages, problems, lo: int, hi: int, lex: Lexicon) -> Report:
    R = Report()
    for p in problems:
        m = re.match(r"p(\d{3})", p)
        R.err("syntax", int(m.group(1)) if m else -1, p)
    idx = [i for i in sorted(pages) if lo <= i <= hi]
    # ------------------------------------------------------------ per page
    words = defaultdict(set)
    prev_last_para = None
    prev_angs: List[Tuple[int, int]] = []
    tails: List[Tuple[int, str]] = []          # (page, marker) closing each hymn
    japu = []
    for i in idx:
        pg = pages[i]
        if not pg.blocks and not pg.notes:
            R.err("syntax", i, "page has no content")
        # notes
        nums = [n.num for n in pg.notes]
        if nums != list(range(1, len(nums) + 1)):
            R.err("notes", i, f"notes not numbered 1..n: {nums}")
        refs: List[int] = []
        for b in pg.blocks:
            refs += b.refs if b.kind == "inv" else [int(x) for x in PH.findall(b.text)]
        for r in refs:
            if r not in nums:
                R.err("notes", i, f"reference [^{r}] has no note n{r}")
        for n in nums:
            c = refs.count(n)
            if c == 0:
                R.err("notes", i, f"note n{n} is never referenced")
            elif c > 1:
                R.warn("notes", i, f"note n{n} referenced {c} times")
        if refs != sorted(refs):
            R.warn("notes", i, f"references out of order: {refs}")
        for n in pg.notes:
            t = plain(n.text)
            if not t.strip():
                R.err("notes", i, f"note n{n.num} is empty")
            if DEBRIS.search(t):
                R.err("text", i, f"debris in n{n.num}: {DEBRIS.search(t).group(0)!r} in …{t[max(0, DEBRIS.search(t).start() - 25):DEBRIS.search(t).start() + 25]}…")
        # blocks
        hymn_markers: List[str] = []
        first_text = True
        for k, b in enumerate(pg.blocks):
            t = plain(b.text) if b.kind not in ("inv", "ang") else ""
            if b.kind == "ang":
                v = int(b.text)
                if prev_angs and v <= prev_angs[-1][1]:
                    R.err("angs", i, f"ang {v} after ang {prev_angs[-1][1]} (page {prev_angs[-1][0]})")
                elif prev_angs and v > prev_angs[-1][1] + 1:
                    R.warn("angs", i, f"ang jumps {prev_angs[-1][1]} -> {v}")
                prev_angs.append((i, v))
                continue
            if b.kind in ("head", "inv"):
                hymn_markers = []
            if b.kind == "head" and pg.blocks and any(x.kind == "verse" for x in pg.blocks) and not is_heading(t) \
                    and not re.search(r"Shaloka|Pauri|Japu|So-?Dar|So-?Purukh|Sohil|Āsā|Gujri|Gauri|Rāg", t):
                R.warn("headings", i, f"title does not read as a hymn title: {t!r}")
            if b.kind == "verse":
                if b.marker:
                    if not MARKER.match(b.marker):
                        R.err("stanzas", i, f"bad stanza number {{{b.marker}}} after {t[-40:]!r}")
                    hymn_markers.append(b.marker)
                    if "-" in b.marker and "Pause" not in b.marker:
                        tails.append((i, b.marker))
                    if i in range(49, 61) and re.fullmatch(r"\d{1,2}", b.marker):
                        japu.append((i, int(b.marker)))
                if t[:1].islower() and not t.startswith(("e.", "i.", "lit")):
                    R.warn("flow", i, f"verse line starts lower-case (wrapped line?): {t[:50]!r}")
            if b.kind == "para":
                if first_text and b.cont and prev_last_para is not None and re.search(r"[.!?:\"”’)]$", prev_last_para) and t[:1].isupper():
                    R.warn("flow", i, f"p+ joins onto a finished sentence: …{prev_last_para[-30:]!r} + {t[:30]!r}")
                if first_text and not b.cont and prev_last_para is not None and not re.search(r"[.!?:\"”’)—-]$", prev_last_para):
                    R.err("flow", i, f"page opens a new paragraph but the previous page ended mid-sentence: …{prev_last_para[-40:]!r}")
                if not b.cont and t[:1].islower():
                    R.err("flow", i, f"new paragraph starts lower-case: {t[:50]!r}")
                if b.cont and (not first_text):
                    R.err("flow", i, "p+ used after the start of the page")
                first_text = False
                prev_last_para = t
            elif b.kind in ("verse", "head", "label", "bq", "qv", "centre", "right", "sub", "inv"):
                if b.kind not in ("inv",) and first_text and b.kind != "head":
                    pass
                if b.kind != "inv":
                    first_text = False if b.kind not in ("bq", "qv") else first_text
                if b.kind in ("verse", "head", "label", "inv"):
                    prev_last_para = None
            # text debris
            if t:
                m = DEBRIS.search(t)
                if m:
                    R.err("text", i, f"debris {m.group(0)!r} in {b.kind}: …{t[max(0, m.start() - 30):m.start() + 30]}…")
                for g in GLUED.findall(t):
                    if not re.fullmatch(r"\d+(st|nd|rd|th|s)", g):
                        R.err("text", i, f"digit glued to word: {g!r} in {t[:60]!r}")
                for d in DOUBLE.findall(t):
                    if d.lower() not in OK_DOUBLE:
                        R.warn("text", i, f"doubled word {d!r}: {t[:70]!r}")
                for o, c in ("()", "[]"):
                    if t.count(o) != t.count(c):
                        R.warn("text", i, f"unbalanced {o}{c} in {b.kind}: {t[:70]!r}")
                for w in re.findall(r"[A-Za-zāīūĀĪŪñṇṛṣṭḍ’'-]+", t):
                    w = w.strip("’'-")
                    if len(w) > 1 and not INDIC.search(w):
                        words[w].add(i)
        # markers grammar within one hymn: numbers should not go backwards
        nums_seq = [int(m.split("-")[0]) for m in hymn_markers if m[0].isdigit()]
        if any(b < a for a, b in zip(nums_seq, nums_seq[1:])):
            R.warn("stanzas", i, f"stanza numbers go backwards within a hymn: {hymn_markers}")
    # ------------------------------------------------------------ across pages
    for (pa, a), (pb, b) in zip(tails, tails[1:]):
        ta, tb = a.split("-"), b.split("-")
        if len(ta) == len(tb) and len(ta) >= 2 and ta[-1].isdigit() and tb[-1].isdigit():
            if int(tb[-1]) not in (int(ta[-1]) + 1, 1):
                R.warn("stanzas", pb, f"running count {a} (p{pa}) -> {b}")
    for (pa, a), (pb, b) in zip(japu, japu[1:]):
        if b != a + 1 and not (b == 1):
            R.warn("stanzas", pb, f"Japu stanza {a} (p{pa}) -> {b}")
    # unknown words
    for w in words:
        lex.prime([w]) if False else None
    lex.bulk(set(words) | {w.lower() for w in words})
    for w, pgs in sorted(words.items()):
        if not lex.known(w) and not lex.known(w.lower()) and not lex.known(w.replace("ā", "a").replace("ī", "i").replace("ū", "u")):
            for i in sorted(pgs):
                R.warn("unknown", i, w)
    return R


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("dir")
    ap.add_argument("--from", dest="lo", type=int, default=0)
    ap.add_argument("--to", dest="hi", type=int, default=10 ** 4)
    ap.add_argument("--quiet", action="store_true", help="summary only")
    ap.add_argument("--no-unknown", action="store_true", help="omit the unknown-word list")
    a = ap.parse_args()
    pages, problems = load_dir(a.dir)
    R = check(pages, problems, a.lo, a.hi, Lexicon())
    by = defaultdict(lambda: [0, 0])
    for sev, cat, _, _ in R.items:
        by[cat][0 if sev == "ERROR" else 1] += 1
    if not a.quiet:
        for sev, cat, pg, msg in sorted(R.items, key=lambda x: (x[2], x[0] != "ERROR", x[1])):
            if cat == "unknown" and a.no_unknown:
                continue
            if cat == "unknown":
                continue
            print(f"{sev:5} p{pg:03d} {cat:9} {msg}")
        unk = defaultdict(list)
        for sev, cat, pg, msg in R.items:
            if cat == "unknown":
                unk[pg].append(msg)
        if unk and not a.no_unknown:
            print("\nunknown words (check against the scan; many are legitimate names/terms):")
            for pg in sorted(unk):
                print(f"  p{pg:03d}: " + " ".join(unk[pg]))
    print("\nsummary:", ", ".join(f"{c}: {e} errors / {w} warnings" for c, (e, w) in sorted(by.items())))
    n_err = sum(e for e, _ in by.values())
    print(f"pages checked: {len([i for i in pages if a.lo <= i <= a.hi])}; total errors: {n_err}")
    return 1 if n_err else 0


if __name__ == "__main__":
    sys.exit(main())
