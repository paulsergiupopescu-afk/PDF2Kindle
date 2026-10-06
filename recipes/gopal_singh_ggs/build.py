#!/usr/bin/env python3
"""Build a Kindle EPUB of Dr. Gopal Singh's English "Sri Guru Granth Sahib",
Vol. I (Allied Publishers, 1960; 2005 reprint), from the Namdhari eLibrary scan.

    python build.py SCAN.pdf OUT.epub [--ocr DIR] [--fonts DIR]

`--ocr` points at Tesseract TSVs (pNNN.eng.tsv, pNNN.ind.tsv) made by
ocr_pass.py; without them the scan's own text layer is used alone.
`--fonts` is a directory holding the OFL font files (see fonts.py).

The design follows the printed edition rather than a generic ebook template:
blackletter invocations and title as in 1960, verse set line-for-line with the
translator's stanza numbers, the original-text page numbers ("P. 241") kept in
the margin, the edition's own page numbers as the Kindle page-list, and the
per-page notes turned into pop-up footnotes.
"""

from __future__ import annotations

import argparse
import difflib
import datetime as dt
import html
import os
import pickle
import re
import sys
import uuid
import zipfile
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

from parse import INDIC, Block, Note, Page, Parser  # noqa: E402

BOOK_TITLE = "Sri Guru Granth Sahib"
SUBTITLE = "English Version · Volume I"
TRANSLATOR = "Dr. Gopal Singh"

# -------------------------------------------------------------------- layout of the scan
FRONT_PAGES = range(7, 49)        # PDF pages with the prose front matter (roman-numbered, VII-XLVIII)
VERSE_PAGES = range(49, 385)      # scripture: printed page = pdf index - 48
GLOSSARY_PAGES = range(385, 393)  # glossary, printed i..viii

INVOCATION_LONG = ("By the Grace of the One Supreme Being, the Eternal, the All-pervading Purusha, "
                   "the Creator, Without Fear, Without Hate, the Being Beyond Time, Not-incarnated, "
                   "Self-existent, the Enlightener.")
INVOCATION_SHORT = "By the Grace of the One Supreme Being, the Eternal, the Enlightener."
# where the Japu's six notes on the Mool Mantar attach
INVOCATION_LONG_NOTE_AT = ["Being,", "Eternal,", "All-pervading", "Purusha,", "Not-incarnated,", "Enlightener."]


def printed_page(pdf_index: int) -> str:
    if pdf_index in VERSE_PAGES:
        return str(pdf_index - 48)
    if pdf_index in GLOSSARY_PAGES:
        return roman(pdf_index - 384).lower()
    return roman(pdf_index)


def roman(n: int) -> str:
    vals = [(1000, "M"), (900, "CM"), (500, "D"), (400, "CD"), (100, "C"), (90, "XC"), (50, "L"),
            (40, "XL"), (10, "X"), (9, "IX"), (5, "V"), (4, "IV"), (1, "I")]
    out = ""
    for v, s in vals:
        while n >= v:
            out += s
            n -= v
    return out


# -------------------------------------------------------------------- the book's structure

@dataclass
class Section:
    key: str
    title: str                 # as in the printed Contents
    page: int                  # pdf index of the first page
    match: Optional[str]       # regex for the heading that opens it (None: page top)
    level: int = 1
    kind: str = "verse"        # verse | prose | glossary
    label: str = ""            # small line above a chapter title
    end_match: Optional[str] = None


SECTIONS: List[Section] = [
    Section("radhakrishnan", "Message from Dr. S. Radhakrishnan", 7, None, kind="prose", label="President of India"),
    Section("nehru", "Message from Jawaharlal Nehru", 8, None, kind="prose", label="Prime Minister of India"),
    Section("preface", "Preface", 9, None, kind="prose"),
    Section("introduction", "Introduction", 11, None, kind="prose"),
    Section("opinions", "Some Opinions", 14, None, kind="prose"),
    Section("compilation", "On the Compilation of the Guru Granth", 18, None, kind="prose", label="I"),
    Section("philosophy", "On the Philosophy of Sikh Religion", 20, None, kind="prose", label="II"),
    Section("story", "The Story of the Sikh Gurus", 35, None, kind="prose", label="III"),
    Section("japu", "Japu", 49, None, label="Meditations"),
    Section("sodaru", "So-Daru", 60, r"So.?Dar"),
    Section("sohila", "Sohilā", 63, r"Sohil"),
    Section("srirag", "Sri Rāg", 66, None, label="Rāg"),
    Section("srirag-asht", "Ashtapadis", 99, r"Ashtapadi", 2),
    Section("srirag-pahre", "Pahre", 116, r"Pahre", 2),
    Section("srirag-chhant", "Chhants", 119, r"Chhant", 2),
    Section("srirag-vanjara", "Vanjārā", 121, r"Vanj", 2),
    Section("srirag-var", "Vār of Sri Rāg (with Shalokas)", 122, r"Var of Sri", 2),
    Section("srirag-bhagat", "The Word of the Bhaktas", 130, r"Kabir", 2),
    Section("majh", "Rāg Mājh", 133, None, label="Rāg"),
    Section("majh-asht", "Ashtapadis", 148, r"Ashtapadi", 2),
    Section("majh-baramaha", "Bārā Māha M. 5", 172, r"B\s?A\s?R\s?A", 2),
    Section("majh-dinrain", "Night & Day", 175, r"Night|Din|Rain", 2),
    Section("majh-var", "Vār of Rāg Mājh and Shalokas", 176, r"Var", 2),
    Section("gauri", "Rāg Gauri", 192, None, label="Rāg"),
    Section("gauri-asht", "Ashtapadis", 269, r"Ashtapadi", 2),
    Section("gauri-bawan", "Bāwan Akhari, M. 5", 287, r"Bawan|Akhari", 2),
    Section("gauri-sukhmani", "Sukhmani, M. 5", 301, r"S[ou]k[bh]mani", 2),
    Section("gauri-thitti", "Thitti Gauri", 337, r"Thitti", 2),
    Section("gauri-var4", "Vār of Gauri, M. 4", 341, r"Var", 2),
    Section("gauri-var5", "Vār of Gauri, M. 5", 355, r"Var", 2),
    Section("gauri-bhagat", "The Word of the Bhaktas", 361, r"Bhakta|Kabir", 2),
    Section("glossary", "Glossary of Technical Terms", 385, None, kind="glossary"),
]


# -------------------------------------------------------------------- parsing (cached)

def parse_all(pdf: str, ocr: Optional[str], cache: str) -> Dict[int, Page]:
    pages: Dict[int, Page] = {}
    if os.path.exists(cache) and os.path.getmtime(cache) > max(
            os.path.getmtime(os.path.join(HERE, f)) for f in ("parse.py", "lexicon.py", "headings.py", "glossary.py")):
        with open(cache, "rb") as f:
            pages = pickle.load(f)
    wanted = list(FRONT_PAGES) + list(VERSE_PAGES)
    missing = [i for i in wanted if i not in pages]
    if missing or not any(i in pages for i in GLOSSARY_PAGES):
        P = Parser(pdf, ocr)
        for i in missing:
            pages[i] = P.parse(i, "prose" if i in FRONT_PAGES else "verse")
            if i % 25 == 0:
                print(f"  parsed page {i}", flush=True)
        if not any(i in pages for i in GLOSSARY_PAGES):
            from glossary import parse_glossary
            pages.update(parse_glossary(P, GLOSSARY_PAGES))
        with open(cache, "wb") as f:
            pickle.dump(pages, f)
    return pages


# pages replaced by hand-corrected text (corrections.py); their headings are
# taken verbatim, and every page's notes are reachable for references that a
# paragraph carries across a page break
CORRECTED: set = set()
PAGE_NOTES: Dict[int, Dict[int, Note]] = {}
PB_MARK = "\u2064"

# -------------------------------------------------------------------- rendering helpers

def esc(s: str) -> str:
    return html.escape(s, quote=False)


def indic_spans(s: str) -> str:
    """Escape text, wrapping Gurmukhi / Devanagari runs in language spans so
    the embedded Noto faces are used for them."""
    out = []
    for run in re.split(r"([ऀ-ॿ਀-੿][ऀ-ॿ਀-੿\s,.;:=\-‌‍]*)", s):
        if not run:
            continue
        if re.match(r"[਀-੿]", run):
            out.append(f'<span class="gu" lang="pa">{esc(run.rstrip())}</span>' + (" " if run.endswith(" ") else ""))
        elif re.match(r"[ऀ-ॿ]", run):
            out.append(f'<span class="dv" lang="hi">{esc(run.rstrip())}</span>' + (" " if run.endswith(" ") else ""))
        else:
            out.append(typo(esc(run)).replace("\ue000", "<i>").replace("\ue001", "</i>"))
    return "".join(out)


def typo(s: str) -> str:
    """Typographer's quotes and dashes on already-escaped text."""
    s = re.sub(r"(^|[\s(\[—\-])\"", "\\1“", s)
    s = s.replace('"', "”")
    s = re.sub(r"(^|[\s(\[—\-“])'", "\\1‘", s)
    s = s.replace("'", "’")
    s = re.sub(r"\s?--\s?|\s-\s", "—", s)
    s = re.sub(r":-\s*$", ":—", s)
    return s


ITALIC_WORDS = re.compile(r"\b(Lit\.|lit\.|Cf\.|cf\.|i\.\s?e\.|e\.\s?g\.)")


class Notes:
    """Collects a chapter's notes, renumbering them 1..n."""

    def __init__(self, chapter_id: str):
        self.cid = chapter_id
        self.items: List[Tuple[int, str]] = []

    def ref(self, note: Optional[Note]) -> str:
        if note is None:
            return ""
        n = len(self.items) + 1
        self.items.append((n, note.text))
        return (f'<a class="nr" epub:type="noteref" href="#{self.cid}-n{n}" id="{self.cid}-r{n}">'
                f'<sup>{n}</sup></a>')

    def render(self) -> str:
        if not self.items:
            return ""
        out = ['<section class="notes" epub:type="footnotes">',
               '<p class="notes-orn">❦</p>']
        for n, text in self.items:
            out.append(f'<aside class="fn" epub:type="footnote" id="{self.cid}-n{n}">'
                       f'<p><a href="#{self.cid}-r{n}">{n}.</a> {(indic_spans(text))}</p></aside>')
        out.append("</section>")
        return "\n".join(out)


def text_with_refs(text: str, refs: List[int], page_notes: Dict[int, Note], notes: Notes) -> str:
    """Render text with U+2063-delimited footnote placeholders as noterefs."""
    parts = re.split("\u2063((?:\\d+:)?\\d+)\u2063", text)
    out = []
    placed = set()
    for k, part in enumerate(parts):
        if k % 2:
            if ":" in part:
                pg, num = (int(x) for x in part.split(":"))
                note = PAGE_NOTES.get(pg, {}).get(num)
                key = (pg, num)
            else:
                num = int(part)
                note, key = page_notes.get(num), num
            if key not in placed:
                placed.add(key)
                out.append(notes.ref(note))
        else:
            for j, seg in enumerate(part.split(PB_MARK)):
                if j % 2:
                    out.append(f'<span class="pb" epub:type="pagebreak" id="pg-{esc(seg)}" title="{esc(seg)}" role="doc-pagebreak"></span>')
                else:
                    out.append(indic_spans(seg))
    html_ = "".join(out)
    html_ = re.sub(r"\s+(<a class=\"nr\")", r"\1", html_)
    for num in refs:  # references not tied to a position (fallback)
        if num not in placed:
            html_ += notes.ref(page_notes.get(num))
    return html_


def marker_html(mk: str) -> str:
    if not mk:
        return ""
    m = re.fullmatch(r"(\d+)-Pause(?:-(.*))?", mk)
    if m:
        inner = f'{m.group(1)}<span class="pause">‑Pause</span>' + (f"‑{m.group(2)}" if m.group(2) else "")
    else:
        inner = mk.replace("-", "‑")
    return f'<span class="mk">[{inner}]</span>'


def heading_html(t: str) -> str:
    """Hymn title (already rendered HTML): 'Gauri Guareri M. 3' → name in
    small caps, the mahala ("M. 3": the Guru's number) in italic."""
    m = re.match(r"^(.*?)(,?\s*M\.\s*(?:<a [^>]*><sup>\d+</sup></a>)?\s*\d+.*)$", t)
    if m and m.group(1).strip():
        return f'{m.group(1).strip()} <span class="mah">{m.group(2).strip(", ")}</span>'
    return t


from headings import fix_heading, fold  # noqa: E402


# -------------------------------------------------------------------- chapter assembly

@dataclass
class Chapter:
    sec: Section
    file: str
    blocks: List[Tuple[int, Block]] = field(default_factory=list)   # (pdf page, block)
    first_page_label: Optional[str] = None


def fix_angs(pages: Dict[int, Page]) -> None:
    """Make the original-text page numbers a clean increasing sequence.

    The margin numerals are small bold type the OCR often clips ("P.I02" read
    as 2, "P.l17" as 17). Each value is checked against its neighbours; a
    value out of order is replaced by the reading that fits (a dropped
    leading 1, 2 or 3), or removed. Ang 1 opens the Japu."""
    seq = [(i, b) for i in sorted(pages) if i in VERSE_PAGES for b in pages[i].blocks if b.kind == "ang"]

    def cands(v: int):
        s = str(v)
        out = [(v, 0)]
        for c in (v + 100, v + 200, v + 300, int("1" + s), int("2" + s), int("3" + s)):
            out.append((c, 1))
        if len(s) == 3:
            for c in (int(s[0] + "1" + s[2]), int("1" + s[1:]), int("2" + s[1:]), int("3" + s[1:])):
                out.append((c, 1))
        return sorted({c: p for c, p in reversed(out) if 0 < c < 1430}.items())

    # longest chain: one reading per marker, strictly increasing, and never
    # more than ~3 angs per printed page between kept markers
    C = [cands(int(b.text)) for _, b in seq]
    best = {}   # (k, value) -> (length, -penalty, prev)
    for k, (pi, _) in enumerate(seq):
        for v, pen in C[k]:
            top = (1, -pen, None)
            for j in range(max(0, k - 12), k):
                pj = seq[j][0]
                for u, _ in C[j]:
                    if (j, u) in best and u < v <= u + 3 * (pi - pj + 1) + 1:
                        L, P, _ = best[(j, u)]
                        cand = (L + 1, P - pen, (j, u))
                        if cand[:2] > top[:2]:
                            top = cand
            best[(k, v)] = top
    vals = [None] * len(seq)
    if best:
        node = max(best, key=lambda key: best[key][:2])
        while node is not None:
            vals[node[0]] = node[1]
            node = best[node][2]
    for (i, b), v in zip(seq, vals):
        if v is None:
            pages[i].blocks.remove(b)
        else:
            b.text = str(v)
    first = pages[min(VERSE_PAGES)]
    if not any(b.kind == "ang" for b in first.blocks):
        idx = next((k for k, b in enumerate(first.blocks) if b.kind == "verse"), 0)
        first.blocks.insert(idx, Block("ang", "1", page=first.index))


def recover_angs(pdf: str, pages: Dict[int, Page], work: str) -> int:
    """Look for margin numerals the OCR missed: for every gap in the Ang
    sequence, read the right-hand margin of the pages where it must fall."""
    import subprocess
    import pymupdf
    from parse import ang_value
    doc = pymupdf.open(pdf)
    seq = [(i, b) for i in sorted(pages) if i in VERSE_PAGES for b in pages[i].blocks if b.kind == "ang"]
    found = 0
    cache_dir = os.path.join(work, "margins")
    os.makedirs(cache_dir, exist_ok=True)
    for (pi, a), (pj, c) in zip(seq, seq[1:]):
        lo, hi = int(a.text), int(c.text)
        if hi - lo < 2:
            continue
        want = set(range(lo + 1, hi))
        for pg in range(pi, pj + 1):
            tsv = os.path.join(cache_dir, f"m{pg:03d}.tsv")
            if not os.path.exists(tsv):
                page = doc[pg]
                clip = pymupdf.Rect(page.rect.width * 0.80, 40, page.rect.width, page.rect.height - 40)
                png = tsv[:-4] + ".png"
                page.get_pixmap(dpi=400, clip=clip, colorspace=pymupdf.csGRAY).save(png)
                subprocess.run(["tesseract", png, tsv[:-4], "--psm", "11", "tsv"], capture_output=True)
                os.remove(png)
            words = []
            with open(tsv, encoding="utf-8") as f:
                for line in f.read().splitlines()[1:]:
                    parts = line.split("\t")
                    if len(parts) == 12 and parts[11].strip():
                        words.append((parts[11].strip(), 40 + int(parts[7]) * 72 / 400))
            for k, (w, y) in enumerate(words):
                cand = w if re.match(r"^[PF][.,]?\S", w) else ("P." + words[k + 1][0] if re.fullmatch(r"[PF][.,]?", w) and k + 1 < len(words) else None)
                v = ang_value(cand) if cand else None
                if v in want and (pg != pi or y > a.y) and (pg != pj or y < c.y):
                    blocks = pages[pg].blocks
                    idx = len(blocks)
                    for n, bl in enumerate(blocks):
                        if bl.kind in ("verse", "para") and bl.y > y - 4:
                            idx = n
                            break
                    blocks.insert(idx, Block("ang", str(v), y=y, page=pg))
                    want.discard(v)
                    found += 1
    return found


def assign_blocks(pages: Dict[int, Page]) -> List[Chapter]:
    chapters = [Chapter(s, f"{n:02d}-{s.key}.xhtml") for n, s in enumerate(SECTIONS, start=10)]
    # sequence of (page, block) with page-break markers
    seq: List[Tuple[int, Block]] = []
    for i in sorted(pages):
        seq.append((i, Block("pb", printed_page(i), page=i)))
        for b in pages[i].blocks:
            seq.append((i, b))
    # locate the opening block of each section
    starts: List[int] = []
    for ch in chapters:
        s = ch.sec
        idx = None
        for k, (pg, b) in enumerate(seq):
            if pg < s.page:
                continue
            if pg > s.page + 1:
                break
            if s.match is None:
                if b.kind == "pb" and pg == s.page:
                    idx = k
                    break
            elif b.kind in ("head", "sub") and re.search(s.match, fold(fix_heading(b.text)), re.I):
                idx = k
                # pull in the invocation (and page break) that precede the heading
                j = k - 1
                while j >= 0 and seq[j][1].kind in ("inv", "pb", "head", "sub") and k - j <= 3:
                    idx = j
                    j -= 1
                break
        if idx is None:
            print(f"  ! section {s.key}: opening heading not found on page {s.page}; starting at page top")
            idx = next(k for k, (pg, b) in enumerate(seq) if pg == s.page and b.kind == "pb")
        starts.append(idx)
    for n, ch in enumerate(chapters):
        end = starts[n + 1] if n + 1 < len(chapters) else len(seq)
        ch.blocks = seq[starts[n]:end]
    return chapters


# -------------------------------------------------------------------- XHTML

DOC_HEAD = ('<?xml version="1.0" encoding="utf-8"?>\n<!DOCTYPE html>\n'
            '<html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops" '
            'lang="en" xml:lang="en">\n<head>\n<meta charset="utf-8"/>\n<title>{title}</title>\n'
            '<link rel="stylesheet" type="text/css" href="../css/book.css"/>\n</head>\n'
            '<body{bodyattr}>\n')
DOC_TAIL = "</body>\n</html>\n"


def ornament_rule() -> str:
    return '<div class="chain"><img src="../images/chain.png" alt=""/></div>'


def invocation_html(form: str, refs: List[int], page_notes: Dict[int, Note], notes: Notes) -> str:
    if form == "long":
        words = INVOCATION_LONG.split(" ")
        if refs:
            queue = list(refs)
            for k, w in enumerate(words):
                if queue and w in INVOCATION_LONG_NOTE_AT:
                    words[k] = esc(w) + "⁣%d⁣" % queue.pop(0)
                else:
                    words[k] = esc(w)
            text = " ".join(words)
            body = text_with_refs(text, queue, page_notes, notes)
        else:
            body = esc(INVOCATION_LONG)
        return (f'<div class="invocation long"><p class="ik" lang="pa">ੴ</p>'
                f'<p class="inv">{body}</p></div>')
    body = text_with_refs(esc(INVOCATION_SHORT), refs, page_notes, notes)
    return f'<div class="invocation"><p class="inv">{body}</p></div>'


# The two messages are typewritten letters (no line runs full measure, so
# paragraphs cannot be inferred); they are short and set here by hand from the scan.
LETTERS = {
    "radhakrishnan": """
<p class="letter-p">I have now looked through the volumes of <span class="sc">guru granth sahib</span>
(English Version). I read the different introductions and glanced through the Translation.
It is an impressive work which will be found extremely valuable to all students of Sikhism as well
as comparative religions.</p>
<p class="letter-sig">(Sd.) S. <span class="sc">radhakrishnan</span></p>
<p class="letter-place">New Delhi:<br/>21 November, 1961</p>
""",
    "nehru": """
<p class="letter-head">Prime Minister’s House<br/>New Delhi</p>
<p class="letter-date">March 27, 1960</p>
<p class="letter-p">I was presented with a copy of the English version of Shri Guru Granth Sahib translated
and annotated by Dr. Gopal Singh. In looking through this monumental work, I have admired the labour and
scholarship of the translator and I must congratulate him on this achievement. He has performed a worthy
and necessary task. I am glad that this famous book has now been brought to a wider circle of readers.
It is a great book and all who read it will profit by it. To the Sikhs it is Holy Scripture. But, even
by others who are not Sikhs it is greatly respected and many have profited by its reading.</p>
<p class="letter-p">I welcome this fine edition of Shri Guru Granth Sahib.</p>
<div class="letter-sig"><img src="../images/sig-nehru.png" alt="Jawaharlal Nehru"/></div>
""",
}


def render_letter(ch: "Chapter") -> str:
    s = ch.sec
    pb = "".join(f'<span class="pb" epub:type="pagebreak" id="pg-{esc(b.text)}" title="{esc(b.text)}" role="doc-pagebreak"></span>'
                 for _, b in ch.blocks if b.kind == "pb")
    return DOC_HEAD.format(title=esc(s.title), bodyattr="") + f"""
<section class="chapter letter" epub:type="chapter" id="{s.key}">
{pb}{ornament_rule()}
<p class="ch-label">{esc(s.label)}</p>
<h1 class="ch-title">{esc(s.title)}</h1>
<p class="fleuron">❦</p>
{LETTERS[s.key]}
</section>
""" + DOC_TAIL


def prepare_blocks(ch: "Chapter") -> List[Tuple[int, Block]]:
    """The chapter's blocks, ready to render: note placeholders qualified
    with their page (so a paragraph may carry them across a page break), and
    paragraphs that run over a page joined, with the page break kept inside."""
    import copy
    items: List[Tuple[int, Block]] = []
    pending: List[Tuple[int, Block]] = []
    last_para: Optional[Block] = None
    para_pages: set = set()
    for pg, b in ch.blocks:
        if b.kind == "pb":
            pending.append((pg, b))
            continue
        b = copy.copy(b)
        if b.kind in ("head", "label", "sub") and pg not in CORRECTED:
            b.text = fix_heading(b.text)        # before placeholders gain their page
        if b.kind != "inv" and b.text is not None:
            b.text = re.sub("\u2063(\\d+)\u2063", lambda m: f"\u2063{pg}:{m.group(1)}\u2063", b.text)
            placed = {int(x) for x in re.findall(f"\u2063{pg}:(\\d+)\u2063", b.text)}
            b.text += "".join(f"\u2063{pg}:{r}\u2063" for r in b.refs if r not in placed)
            b.refs = []
        cont = b.kind == "para" and (b.cont if pg in CORRECTED else (pg not in para_pages and not b.indent))
        if b.kind == "para":
            para_pages.add(pg)
        if cont and last_para is not None:
            marks = "".join(f"{PB_MARK}{p.text}{PB_MARK}" for _, p in pending)
            pending = []
            if re.search(r"[A-Za-z]-$", last_para.text) and b.text[:1].islower():
                word, _, rest = b.text.partition(" ")
                last_para.text = last_para.text[:-1] + word + marks + (" " + rest if rest else "")
            else:
                last_para.text = last_para.text + marks + " " + b.text
            continue
        items.extend(pending)
        pending = []
        items.append((pg, b))
        if b.kind == "para":
            last_para = b
        elif b.kind != "ang":
            last_para = None
    items.extend(pending)
    return items


def render_chapter(ch: Chapter, pages: Dict[int, Page], angs: List[Tuple[int, str, str]]) -> str:
    s = ch.sec
    if s.key in LETTERS:
        return render_letter(ch)
    cid = s.key
    notes = Notes(cid)
    out: List[str] = []
    title_html = esc(s.title)
    # chapter opener
    if s.level == 1:
        out.append(f'<section class="chapter {s.kind}" epub:type="chapter" id="{cid}">')
        out.append(ornament_rule())
        if s.label:
            out.append(f'<p class="ch-label">{esc(s.label)}</p>')
        out.append(f'<h1 class="ch-title">{title_html}</h1>')
        out.append('<p class="fleuron">❦</p>')
    else:
        out.append(f'<section class="chapter sub {s.kind}" epub:type="chapter" id="{cid}">')
        out.append(f'<h2 class="sub-title">{title_html}</h2>')
        out.append('<p class="fleuron small">☙ ❦ ❧</p>')

    stanza: List[str] = []
    qverse: List[str] = []
    first_text_done = False

    def close_stanza(pause=False):
        nonlocal stanza, qverse
        if stanza:
            cls = "stanza pause" if pause else "stanza"
            out.append(f'<div class="{cls}">' + "".join(stanza) + "</div>")
            stanza = []
        if qverse:
            out.append('<div class="qverse">' + "".join(qverse) + "</div>")
            qverse = []

    def repeats_opener(b: Block) -> bool:
        """A prose chapter's printed number ("II") or title, already set by the opener."""
        t = re.sub("\u2063[\\d:]+\u2063", "", b.text or "").strip(" .")
        if re.fullmatch(r"[IVXL]+", t):
            return True
        a = re.sub(r"[^a-z]", "", fold(t).lower())
        z = re.sub(r"[^a-z]", "", fold(s.title).lower())
        return bool(a) and difflib.SequenceMatcher(None, a, z).ratio() > 0.85

    for pg, b in prepare_blocks(ch):
        page_notes = {n.num: n for n in pages[pg].notes} if pg in pages else {}
        if s.kind == "prose" and not first_text_done and b.kind in ("head", "para", "centre") and repeats_opener(b):
            continue
        fix_heading_ = lambda t: t                 # applied in prepare_blocks
        if b.kind == "qv":
            if stanza:
                close_stanza()
            qverse.append(f'<p class="v">{text_with_refs(b.text, [], page_notes, notes)}</p>')
            continue
        if qverse and b.kind not in ("pb",):
            close_stanza()
        if b.kind == "bq":
            close_stanza()
            out.append(f'<blockquote><p>{text_with_refs(b.text, [], page_notes, notes)}</p></blockquote>')
            continue
        if b.kind == "right":
            close_stanza()
            out.append(f'<p class="right">{text_with_refs(b.text, [], page_notes, notes)}</p>')
            continue
        if b.kind == "pb":
            pb = f'<span class="pb" epub:type="pagebreak" id="pg-{esc(b.text)}" title="{esc(b.text)}" role="doc-pagebreak"></span>'
            if stanza:
                stanza.append(pb)
            else:
                out.append(pb)
            continue
        if b.kind == "ang":
            aid = f"ang-{b.text}"
            if any(a[1] == aid for a in angs):
                aid += f"-{len(angs)}"
            angs.append((int(b.text), aid, ch.file))
            mark = f'<span class="ang" id="{aid}">P.&#160;{esc(b.text)}</span>'
            stanza.append(mark) if s.kind == "verse" else out.append(f'<p class="ang-p">{mark}</p>')
            continue
        if b.kind == "inv":
            close_stanza()
            # suppress the invocation that merely repeats the chapter opener
            out.append(invocation_html(b.text, b.refs, page_notes, notes))
            continue
        if b.kind == "head":
            close_stanza()
            t = fix_heading_(b.text)
            if len(re.findall(r"[A-Za-z]", t)) < 3:
                continue
            if s.kind == "prose":
                if not first_text_done and re.search(re.escape(s.title.split()[0]), t, re.I):
                    continue  # repeats the chapter title
                out.append(f'<h3 class="prose-head">{text_with_refs(t, b.refs, page_notes, notes)}</h3>')
            else:
                if re.fullmatch(re.escape(fix_heading(s.title)), fold(t), re.I) or fold(t).lower() == fold(s.title).lower():
                    continue
                if len(re.findall(r"[A-Za-z]", t)) < 3:
                    continue
                out.append(f'<h3 class="hymn">{heading_html(text_with_refs(t, b.refs, page_notes, notes))}</h3>')
            continue
        if b.kind == "sub":
            close_stanza()
            out.append(f'<p class="measure">{text_with_refs(fix_heading_(b.text), b.refs, page_notes, notes)}</p>')
            continue
        if b.kind == "centre":
            close_stanza()
            cls = "centre it" if b.italic else "centre"
            out.append(f'<p class="{cls}">{text_with_refs(b.text, b.refs, page_notes, notes)}</p>')
            continue
        if b.kind == "label":
            close_stanza()
            t = fix_heading_(b.text)
            out.append(f'<p class="label">{heading_html(text_with_refs(t, b.refs, page_notes, notes))}</p>')
            continue
        if b.kind == "para":
            close_stanza()
            body = text_with_refs(b.text, b.refs, page_notes, notes)
            cls = "first" if not first_text_done else ("it" if b.italic else "")
            if not first_text_done:
                body = dropcap(body)
            out.append(f'<p class="{cls}">{body}</p>' if cls else f"<p>{body}</p>")
            first_text_done = True
            continue
        if b.kind == "verse":
            body = text_with_refs(b.text, b.refs, page_notes, notes)
            if not body.strip():
                continue
            is_pause = "Pause" in b.marker
            stanza.append(f'<p class="v">{body}{marker_html(b.marker)}</p>')
            first_text_done = True
            if b.marker:
                close_stanza(pause=is_pause)
            continue
    close_stanza()
    out.append(notes.render())
    out.append("</section>")
    return DOC_HEAD.format(title=esc(s.title), bodyattr="") + "\n".join(out) + "\n" + DOC_TAIL


def dropcap(body_html: str) -> str:
    m = re.match(r"^([“‘\"]?)([A-Z])([a-z]*)(\s)", body_html)
    if not m:
        return body_html
    rest = body_html[m.end():]
    return (f'{m.group(1)}<span class="dropcap">{m.group(2)}</span>'
            f'<span class="lead">{m.group(3)}</span>{m.group(4)}{rest}')


# -------------------------------------------------------------------- front & back matter

def title_page() -> str:
    return DOC_HEAD.format(title=BOOK_TITLE, bodyattr=' class="titlepage"') + f"""
<section epub:type="titlepage" class="titlepage">
<div class="chain"><img src="../images/chain.png" alt=""/></div>
<p class="tp-ik" lang="pa">ੴ</p>
<h1 class="tp-title"><span class="bl">Sri</span><br/><span class="bl">Guru Granth Sahib</span></h1>
<p class="tp-ev">[<i>English Version</i>]</p>
<p class="tp-vol">VOL. I</p>
<p class="tp-rev">(Revised in modern idiom)</p>
<p class="fleuron">❦</p>
<p class="tp-by"><i>Translated and annotated by</i></p>
<p class="tp-name">D<span class="sc">r</span>. GOPAL SINGH, <span class="sc">m.a., ph.d.</span></p>
<div class="tp-foot">
<p class="tp-pub">ALLIED PUBLISHERS PVT. LIMITED</p>
<p class="tp-cities">New Delhi · Mumbai · Kolkata · Chennai · Nagpur<br/>Ahmedabad · Bangalore · Hyderabad · Lucknow</p>
</div>
<div class="chain"><img src="../images/chain.png" alt=""/></div>
</section>
""" + DOC_TAIL


def copyright_page() -> str:
    return DOC_HEAD.format(title="Copyright", bodyattr="") + """
<section epub:type="copyright-page" class="imprint">
<p>© Author, 1960<br/><i>(All Rights Reserved by the Author)</i></p>
<p>ISBN 81-7764-304-5 (Set)<br/>ISBN 81-7764-305-3</p>
<p>First Edition, 1960<br/>Reprinted, 1962, 1963, 1965<br/>Revised Edition, 1978<br/>
Reprinted, 1984, 1987, 1989, 1993, 1996, 2002, 2005</p>
<p>Published by Sunil Sachdev and printed by Ravi Sachdev at Allied Publishers Private Limited,
Printing Division, A-104 Mayapuri, Phase-II, New Delhi 110 064</p>
<p class="fleuron small">❦</p>
<p class="note-edition"><i>This Kindle edition</i> was typeset from the scan of the 2005 reprint
distributed by the Sri Satguru Jagjit Singh Ji eLibrary (archive.org/details/namdhari).
The text was recovered by optical character recognition and corrected by machine; a scan of
1960s letterpress will have left occasional errors, chiefly in the Gurmukhi and Devanagari
words quoted in the notes. The pages of this edition are kept as the Kindle page numbers;
numerals in the margin (P.&#160;241) give, as in the printed book, the page of the original
Gurmukhi text; and footnotes, printed at the foot of each page, open here as pop-up notes.</p>
</section>
""" + DOC_TAIL


def dedication_page() -> str:
    return DOC_HEAD.format(title="Dedication", bodyattr="") + """
<section epub:type="dedication" class="dedication">
<p class="ded-head">Dedicated</p>
<p class="ded">to<br/>the great Sikh People<br/>whose fraternity<br/>my revered father, now in heavens,<br/>
joined, blest by the Guru’s Grace, and<br/>turning his back on worldly affluence,<br/>died a martyr to the Cause.</p>
<p class="fleuron">❦</p>
</section>
""" + DOC_TAIL


def contents_page(chapters: List[Chapter]) -> str:
    rows = []
    for ch in chapters:
        s = ch.sec
        cls = "toc1" if s.level == 1 else "toc2"
        if s.key == "japu":
            rows.append('<p class="toc-div">❦</p>')
        rows.append(f'<p class="{cls}"><a href="{ch.file}">{esc(s.title)}</a>'
                    f'<span class="toc-pg">{esc(ch.first_page_label or "")}</span></p>')
    rows.append('<p class="toc1"><a href="90-angs.xhtml">Index of Original Pages (Ang)</a></p>')
    return DOC_HEAD.format(title="Contents", bodyattr="") + f"""
<section epub:type="toc" class="contents">
<h1 class="toc-title">Table of Contents</h1>
<p class="fleuron small">☙ ❦ ❧</p>
{chr(10).join(rows)}
<p class="toc-note"><i>The pages of the original text are given in black type in the margin.
Words with a spiritual significance begin with capital letters throughout the translated version.</i></p>
</section>
""" + DOC_TAIL


def angs_page(angs: List[Tuple[int, str, str]]) -> str:
    seen = {}
    for n, aid, f in angs:
        seen.setdefault(n, (aid, f))
    # an ang with no printed numeral begins somewhere after the previous one
    cells = []
    last = None
    for n in range(1, max(seen) + 1 if seen else 1):
        if n in seen:
            last = seen[n]
            cells.append(f'<a href="{last[1]}#{last[0]}">{n}</a> ')
        elif last:
            cells.append(f'<a class="near" href="{last[1]}#{last[0]}">{n}</a> ')
    cells = "".join(cells)
    return DOC_HEAD.format(title="Index of Original Pages", bodyattr="") + f"""
<section class="chapter angs" id="angs">
{ornament_rule()}
<p class="ch-label">Index</p>
<h1 class="ch-title">Pages of the Original</h1>
<p class="fleuron">❦</p>
<p class="angs-note">The Gurmukhi Sri Guru Granth Sahib is cited by page (<i>ang</i>).
Each number below opens the translation where that page of the original begins;
numbers in a lighter tint were not marked in the printed edition and open at the nearest marked page before them.</p>
<p class="angs-grid">{cells}</p>
</section>
""" + DOC_TAIL


# -------------------------------------------------------------------- packaging

def nav_xhtml(chapters: List[Chapter], page_labels: List[Tuple[str, str]]) -> str:
    items = []
    open_sub = False
    for k, ch in enumerate(chapters):
        s = ch.sec
        li = f'<li><a href="text/{ch.file}">{esc(s.title)}</a>'
        nxt = chapters[k + 1].sec.level if k + 1 < len(chapters) else 1
        if s.level == 1:
            if open_sub:
                items.append("</ol></li>")
                open_sub = False
            if nxt == 2:
                items.append(li + "<ol>")
                open_sub = True
            else:
                items.append(li + "</li>")
        else:
            items.append(li + "</li>")
    if open_sub:
        items.append("</ol></li>")
    items.append('<li><a href="text/90-angs.xhtml">Index of Original Pages (Ang)</a></li>')
    plist = "\n".join(f'<li><a href="text/{f}#pg-{esc(lbl)}">{esc(lbl)}</a></li>' for lbl, f in page_labels)
    return f"""<?xml version="1.0" encoding="utf-8"?>
<!DOCTYPE html>
<html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops" lang="en" xml:lang="en">
<head><meta charset="utf-8"/><title>Contents</title></head>
<body>
<nav epub:type="toc" id="toc"><h1>Contents</h1>
<ol>
<li><a href="text/01-title.xhtml">Title Page</a></li>
<li><a href="text/03-dedication.xhtml">Dedication</a></li>
<li><a href="text/04-contents.xhtml">Table of Contents</a></li>
{chr(10).join(items)}
</ol></nav>
<nav epub:type="landmarks" id="landmarks" hidden="hidden"><ol>
<li><a epub:type="cover" href="text/00-cover.xhtml">Cover</a></li>
<li><a epub:type="toc" href="text/04-contents.xhtml">Table of Contents</a></li>
<li><a epub:type="bodymatter" href="text/{next(c.file for c in chapters if c.sec.key == 'japu')}">Japu</a></li>
</ol></nav>
<nav epub:type="page-list" id="page-list" hidden="hidden"><ol>
{plist}
</ol></nav>
</body></html>
"""


def ncx(chapters: List[Chapter], uid: str) -> str:
    pts = []
    order = 1
    for ch in chapters:
        pts.append(f'<navPoint id="np{order}" playOrder="{order}"><navLabel><text>{esc(ch.sec.title)}</text></navLabel>'
                   f'<content src="text/{ch.file}"/></navPoint>')
        order += 1
    return f"""<?xml version="1.0" encoding="utf-8"?>
<ncx xmlns="http://www.daisy.org/z3986/2005/ncx/" version="2005-1">
<head><meta name="dtb:uid" content="urn:uuid:{uid}"/><meta name="dtb:depth" content="2"/></head>
<docTitle><text>{BOOK_TITLE}</text></docTitle>
<navMap>{''.join(pts)}</navMap>
</ncx>
"""


def opf(uid: str, manifest: List[Tuple[str, str, str, str]], spine: List[str]) -> str:
    now = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    items = "\n".join(f'<item id="{i}" href="{h}" media-type="{m}"{p}/>' for i, h, m, p in manifest)
    sp = "\n".join(f'<itemref idref="{i}"/>' for i in spine)
    return f"""<?xml version="1.0" encoding="utf-8"?>
<package xmlns="http://www.idpf.org/2007/opf" version="3.0" unique-identifier="uid" xml:lang="en">
<metadata xmlns:dc="http://purl.org/dc/elements/1.1/">
<dc:identifier id="uid">urn:uuid:{uid}</dc:identifier>
<dc:title id="t1">{BOOK_TITLE}</dc:title>
<meta refines="#t1" property="title-type">main</meta>
<dc:title id="t2">English Version, Volume I</dc:title>
<meta refines="#t2" property="title-type">subtitle</meta>
<dc:creator id="c1">Gopal Singh</dc:creator>
<meta refines="#c1" property="role" scheme="marc:relators">trl</meta>
<meta refines="#c1" property="file-as">Singh, Gopal</meta>
<dc:publisher>Allied Publishers Pvt. Limited</dc:publisher>
<dc:date>1960</dc:date>
<dc:language>en</dc:language>
<dc:subject>Sikhism -- Sacred books</dc:subject>
<meta property="dcterms:modified">{now}</meta>
<meta name="cover" content="cover-img"/>
<meta name="primary-writing-mode" content="horizontal-lr"/>
</metadata>
<manifest>
{items}
</manifest>
<spine toc="ncx">
{sp}
</spine>
</package>
"""


def build(pdf: str, out: str, ocr: Optional[str], fonts_dir: Optional[str], work: str,
          corrections_dir: Optional[str] = None) -> None:
    os.makedirs(work, exist_ok=True)
    pages = parse_all(pdf, ocr, os.path.join(work, "pages.pkl"))
    fix_angs(pages)
    print(f"  recovered {recover_angs(pdf, pages, work)} margin page numbers")
    from corrections import load_dir
    fixed, problems = load_dir(corrections_dir)
    for p in problems:
        print("  ! " + p)
    pages.update(fixed)
    CORRECTED.update(fixed)
    if fixed:
        print(f"  {len(fixed)} hand-corrected pages from {corrections_dir}")
    PAGE_NOTES.update({i: {n.num: n for n in p.notes} for i, p in pages.items()})
    chapters = assign_blocks(pages)
    for ch in chapters:
        pb = next((b for _, b in ch.blocks if b.kind == "pb"), None)
        first = next((pg for pg, b in ch.blocks), None)
        ch.first_page_label = printed_page(first) if first is not None else ""

    angs: List[Tuple[int, str, str]] = []
    files: Dict[str, bytes] = {}
    from glossary import render_glossary
    for ch in chapters:
        if ch.sec.kind == "glossary":
            xhtml = render_glossary(ch, pages, DOC_HEAD, DOC_TAIL)
        else:
            xhtml = render_chapter(ch, pages, angs)
        files[f"OEBPS/text/{ch.file}"] = xhtml.encode("utf-8")
    files["OEBPS/text/00-cover.xhtml"] = (DOC_HEAD.format(title="Cover", bodyattr=' class="cover"') +
                                          '<div class="cover"><img src="../images/cover.jpg" alt="Sri Guru Granth Sahib"/></div>\n'
                                          + DOC_TAIL).encode()
    files["OEBPS/text/01-title.xhtml"] = title_page().encode()
    files["OEBPS/text/02-copyright.xhtml"] = copyright_page().encode()
    files["OEBPS/text/03-dedication.xhtml"] = dedication_page().encode()
    files["OEBPS/text/04-contents.xhtml"] = contents_page(chapters).encode()
    files["OEBPS/text/90-angs.xhtml"] = angs_page(angs).encode()

    # page-list: every printed page break, in reading order
    page_labels: List[Tuple[str, str]] = []
    for ch in chapters:
        for _, b in ch.blocks:
            if b.kind == "pb":
                page_labels.append((b.text, ch.file))
    uid = str(uuid.uuid5(uuid.NAMESPACE_URL, "gopal-singh-ggs-vol1"))
    files["OEBPS/nav.xhtml"] = nav_xhtml(chapters, page_labels).encode()
    files["OEBPS/toc.ncx"] = ncx(chapters, uid).encode()

    # styles, fonts, images
    with open(os.path.join(HERE, "book.css"), encoding="utf-8") as f:
        css = f.read()
    import fonts as fontmod
    import art
    alltext = "".join(v.decode("utf-8") for k, v in files.items() if k.endswith(".xhtml"))
    font_files = fontmod.prepare(fonts_dir, work, alltext)
    css = fontmod.font_face_css(font_files) + css
    files["OEBPS/css/book.css"] = css.encode()
    for name, path in font_files.items():
        with open(path, "rb") as f:
            files[f"OEBPS/fonts/{name}"] = f.read()
    files["OEBPS/images/cover.jpg"] = art.cover(fonts_dir)
    files["OEBPS/images/chain.png"] = art.chain_band()
    files["OEBPS/images/sig-nehru.png"] = art.signature(pdf, 8, (340, 519, 495, 552))

    manifest: List[Tuple[str, str, str, str]] = []
    spine: List[str] = []
    order = ["00-cover.xhtml", "01-title.xhtml", "02-copyright.xhtml", "03-dedication.xhtml", "04-contents.xhtml"]
    order += [c.file for c in chapters] + ["90-angs.xhtml"]
    for k, fn in enumerate(order):
        iid = f"x{k:02d}"
        manifest.append((iid, f"text/{fn}", "application/xhtml+xml", ""))
        spine.append(iid)
    manifest.append(("nav", "nav.xhtml", "application/xhtml+xml", ' properties="nav"'))
    manifest.append(("ncx", "toc.ncx", "application/x-dtbncx+xml", ""))
    manifest.append(("css", "css/book.css", "text/css", ""))
    manifest.append(("cover-img", "images/cover.jpg", "image/jpeg", ' properties="cover-image"'))
    manifest.append(("chain", "images/chain.png", "image/png", ""))
    manifest.append(("sig", "images/sig-nehru.png", "image/png", ""))
    for k, name in enumerate(font_files):
        manifest.append((f"f{k}", f"fonts/{name}", "font/ttf", ""))
    files["OEBPS/content.opf"] = opf(uid, manifest, spine).encode()

    with zipfile.ZipFile(out, "w") as z:
        z.writestr(zipfile.ZipInfo("mimetype"), "application/epub+zip", compress_type=zipfile.ZIP_STORED)
        z.writestr("META-INF/container.xml", '<?xml version="1.0"?>\n<container version="1.0" '
                   'xmlns="urn:oasis:names:tc:opendocument:xmlns:container"><rootfiles>'
                   '<rootfile full-path="OEBPS/content.opf" media-type="application/oebps-package+xml"/>'
                   '</rootfiles></container>', compress_type=zipfile.ZIP_DEFLATED)
        for name, data in files.items():
            z.writestr(name, data, compress_type=zipfile.ZIP_DEFLATED)
    n_notes = sum(len(p.notes) for p in pages.values())
    print(f"wrote {out}: {len(chapters)} sections, {len(page_labels)} pages, {len(set(a[0] for a in angs))} angs, {n_notes} notes")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("pdf")
    ap.add_argument("out")
    ap.add_argument("--ocr", help="directory of Tesseract TSVs from ocr_pass.py")
    ap.add_argument("--fonts", help="directory with the OFL font files")
    ap.add_argument("--work", default=os.path.join(HERE, ".work"))
    ap.add_argument("--corrections", help="directory of hand-corrected pages (pNNN.txt, see corrections.py)")
    a = ap.parse_args()
    build(a.pdf, a.out, a.ocr, a.fonts, a.work, a.corrections)


if __name__ == "__main__":
    main()
