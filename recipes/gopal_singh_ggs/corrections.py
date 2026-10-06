"""Hand-corrected pages: a plain-text format that overrides the OCR parse.

One file per PDF page, `pNNN.txt` (NNN = zero-based PDF page index). Each
non-blank line is one element of the page, in reading order:

    inv: short                  blackletter invocation, short form
    inv: long ^1 ^2             long Mool Mantar form; ^n = its footnote refs
    h: Gauri Guareri M. 3       hymn title (verse) / sub-heading (prose)
    s: (To be sung ...)         centred italic subtitle under a hymn title
    l: Shaloka M. 1             rubric label inside a Vār (Shaloka, Pauri, M. 2)
    v: verse line {1-Pause}     one verse line; {..} = the stanza number after it
    ang: 241                    "P. 241" in the margin, beside the next verse line
    p: text                     a new prose paragraph
    p+: text                    paragraph continued from the previous page
    bq: text                    a block quotation (indented prose)
    qv: line                    a line of quoted verse inside prose
    c: text / ci: text          a centred line / centred italic line
    r: text                     a right-aligned line (signature, attribution)
    n3: text                    footnote 3 of this page
    # ...                       comment, ignored

Inside text: [^3] places footnote 3's reference; *word* sets italics;
straight quotes ' " are curled by the renderer.
"""

from __future__ import annotations

import os
import re
from typing import Dict, List, Optional, Tuple

from parse import Block, Note, Page

ITAL_OPEN, ITAL_CLOSE = "", ""
_REF = re.compile(r"\[\^(\d+)\]")
_PREFIX = re.compile(r"^(inv|h|s|l|v|ang|p\+|p|bq|qv|ci|c|r|n\d+):\s?(.*)$")
_KIND = {"inv": "inv", "h": "head", "s": "sub", "l": "label", "v": "verse", "ang": "ang",
         "p": "para", "p+": "para", "bq": "bq", "qv": "qv", "c": "centre", "ci": "centre", "r": "right"}
_PH = re.compile("⁣(\\d+)⁣")


def _to_text(b_text: str) -> str:
    """Parser text (U+2063 placeholders) -> correction-file text ([^n])."""
    return _PH.sub(lambda m: f"[^{m.group(1)}]", b_text).replace(" ", " ")


def export_page(page: Page, printed: str, section: str) -> str:
    out = [f"# PDF page {page.index} · printed page {printed} · section: {section}"]
    for b in page.blocks:
        t = _to_text(b.text)
        if b.kind == "inv":
            out.append(f"inv: {b.text}" + "".join(f" ^{r}" for r in b.refs))
        elif b.kind == "ang":
            out.append(f"ang: {b.text}")
        elif b.kind == "head":
            out.append(f"h: {t}")
        elif b.kind == "sub":
            out.append(f"s: {t}")
        elif b.kind == "label":
            out.append(f"l: {t}")
        elif b.kind == "verse":
            out.append(f"v: {t}" + (f" {{{b.marker}}}" if b.marker else ""))
        elif b.kind == "para":
            first_para = next((x for x in page.blocks if x.kind == "para"), None) is b
            out.append(("p+: " if first_para and not b.indent else "p: ") + t)
        elif b.kind == "centre":
            out.append(("ci: " if b.italic else "c: ") + t)
        else:
            out.append(f"# ({b.kind}) {t}")
        # refs the parser could not place in the text
        placed = {int(x) for x in _PH.findall(b.text)}
        loose = [r for r in b.refs if r not in placed and b.kind != "inv"]
        if loose:
            out[-1] += "".join(f"[^{r}]" for r in loose)
    for n in page.notes:
        out.append(f"n{n.num}: {n.text}")
    return "\n".join(out) + "\n"


def _text(s: str) -> Tuple[str, List[int]]:
    refs = [int(x) for x in _REF.findall(s)]
    s = _REF.sub(lambda m: f"⁣{m.group(1)}⁣", s)
    s = re.sub(r"\*([^*\n]+?)\*", ITAL_OPEN + r"\1" + ITAL_CLOSE, s)
    # the 1960 setting's space before ; : ? ! -- kept, but never at a line start
    s = re.sub(r" ([;:?!])", " \\1", s)
    return s.strip(), refs


def load_page(path: str, index: int) -> Tuple[Page, List[str]]:
    """Parse one corrections file. Returns the page and a list of problems."""
    blocks: List[Block] = []
    notes: List[Note] = []
    problems: List[str] = []
    with open(path, encoding="utf-8") as f:
        lines = f.read().splitlines()
    for ln, raw in enumerate(lines, 1):
        line = raw.rstrip()
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        m = _PREFIX.match(line)
        if not m:
            problems.append(f"p{index:03d}.txt:{ln}: unknown line type: {line[:60]!r}")
            continue
        tag, body = m.group(1), m.group(2)
        if tag.startswith("n") and tag[1:].isdigit():
            txt, _ = _text(body)
            notes.append(Note(int(tag[1:]), txt, index))
            continue
        kind = _KIND[tag]
        if kind == "inv":
            form, *refs = body.split()
            if form not in ("short", "long"):
                problems.append(f"p{index:03d}.txt:{ln}: inv must be short or long")
            blocks.append(Block("inv", form, refs=[int(r.lstrip("^")) for r in refs if r.lstrip("^").isdigit()], page=index))
            continue
        if kind == "ang":
            if not body.strip().isdigit():
                problems.append(f"p{index:03d}.txt:{ln}: ang must be a number")
                continue
            blocks.append(Block("ang", body.strip(), page=index))
            continue
        marker = ""
        if kind == "verse":
            mm = re.search(r"\s*\{([^{}]*)\}\s*$", body)
            if mm:
                marker, body = mm.group(1).strip(), body[:mm.start()]
        txt, refs = _text(body)
        b = Block(kind, txt, marker=marker, refs=refs, page=index)
        b.cont = tag == "p+"
        b.italic = tag == "ci"
        blocks.append(b)
    return Page(index, blocks, notes), problems


def load_dir(d: Optional[str]) -> Tuple[Dict[int, Page], List[str]]:
    pages: Dict[int, Page] = {}
    problems: List[str] = []
    if not d or not os.path.isdir(d):
        return pages, problems
    for fn in sorted(os.listdir(d)):
        m = re.fullmatch(r"p(\d{3})\.txt", fn)
        if m:
            pg, pr = load_page(os.path.join(d, fn), int(m.group(1)))
            pages[pg.index] = pg
            problems += pr
    return pages, problems
