"""Page analysis for the scanned Gopal Singh "Sri Guru Granth Sahib" (Vol. I).

Turns the OCR text layer of each page into a list of typed blocks:

    inv      a blackletter invocation (Mool Mantar, long or short form)
    head     a centred hymn title ("Gauri Guareri M. 3")
    sub      a centred italic subtitle ("(To be sung in the Measure of ...)")
    label    a left-set bold label inside a Vār ("Shaloka M. 1", "Pauri")
    verse    one line of verse, with an optional stanza marker ("1", "1-Pause", "4-5-25")
    para     a paragraph of prose (front matter)
    ang      a marginal "P. 241": page of the original Gurmukhi text
    pagenum  the printed page number of this edition (for the EPUB page-list)

and per-page footnotes, with their in-text reference positions resolved.
"""

from __future__ import annotations

import csv
import os
import re
import statistics
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

import pymupdf

from lexicon import Lexicon, Repairer
from headings import is_heading

INDIC = re.compile(r"[ऀ-ॿ਀-੿]")
BLACKLETTER_HINTS = re.compile(r"upr|tl?ye|tqe|t[Il]i?e\b|®|~|@|ein|[Gg]rac|rnte|rare|ternal|nlig|ener")


@dataclass
class Tok:
    t: str
    font: str
    size: float
    flags: int
    bbox: Tuple[float, float, float, float]

    @property
    def bold(self) -> bool:
        return bool(self.flags & 16) or "Bold" in self.font

    @property
    def italic(self) -> bool:
        return bool(self.flags & 2) or "Italic" in self.font or "Oblique" in self.font


@dataclass
class Line:
    toks: List[Tok]

    @property
    def bbox(self):
        xs0, ys0, xs1, ys1 = zip(*(t.bbox for t in self.toks))
        return min(xs0), min(ys0), max(xs1), max(ys1)

    @property
    def x0(self): return self.bbox[0]

    @property
    def x1(self): return self.bbox[2]

    @property
    def y0(self): return self.bbox[1]

    @property
    def y1(self): return self.bbox[3]

    @property
    def size(self) -> float:
        big = [t for t in self.toks if len(t.t.strip()) > 1] or self.toks
        return statistics.median(t.size for t in big)

    @property
    def text(self) -> str:
        return join_toks(self.toks)

    @property
    def bold(self) -> bool:
        letters = [t for t in self.toks if re.search(r"[A-Za-z]{2}", t.t)]
        return bool(letters) and sum(t.bold for t in letters) * 2 > len(letters)

    @property
    def italic(self) -> bool:
        letters = [t for t in self.toks if re.search(r"[A-Za-z]{2}", t.t)]
        return bool(letters) and sum(t.italic for t in letters) * 2 > len(letters)


def join_toks(toks: List[Tok]) -> str:
    out = ""
    prev = None
    for t in toks:
        s = t.t.strip()
        if not s:
            continue
        if prev is not None and (t.bbox[0] - prev.bbox[2] > 1.2 or t.t[:1].isspace() or prev.t[-1:].isspace()):
            out += " "
        out += s
        prev = t
    return re.sub(r"\s+", " ", out).strip()


@dataclass
class Block:
    kind: str
    text: str = ""
    marker: str = ""          # stanza marker for verse
    refs: List[int] = field(default_factory=list)   # footnote numbers referenced (page-local)
    y: float = 0.0
    x: float = 0.0
    page: int = 0
    cont: bool = False        # verse line continued from a wrapped previous line
    indent: bool = False      # prose paragraph opened with an indent
    italic: bool = False
    bold: bool = False


@dataclass
class Note:
    num: int
    text: str
    page: int


@dataclass
class Page:
    index: int
    blocks: List[Block]
    notes: List[Note]


# ---------------------------------------------------------------- tesseract

class TessPage:
    """Word boxes from a Tesseract TSV, converted to PDF points."""

    def __init__(self, path: str, scale: float):
        self.words: List[Tuple[str, Tuple[float, float, float, float], float]] = []
        if not os.path.exists(path):
            return
        with open(path, newline="", encoding="utf-8") as f:
            for r in csv.DictReader(f, delimiter="\t", quoting=csv.QUOTE_NONE):
                txt = (r.get("text") or "").strip()
                if not txt or r["level"] != "5":
                    continue
                l, t, w, h = (int(r[k]) for k in ("left", "top", "width", "height"))
                self.words.append((txt, (l * scale, t * scale, (l + w) * scale, (t + h) * scale), float(r["conf"])))

    def within(self, bb, pad: float = 1.5) -> List[str]:
        x0, y0, x1, y1 = bb
        out = []
        for txt, (a, b, c, d), _ in self.words:
            cx, cy = (a + c) / 2, (b + d) / 2
            if x0 - pad <= cx <= x1 + pad and y0 - pad <= cy <= y1 + pad:
                out.append((a, txt))
        return [t for _, t in sorted(out)]


# ---------------------------------------------------------------- lines

def page_tokens(page) -> List[List[Tok]]:
    lines = []
    for b in page.get_text("dict")["blocks"]:
        for l in b.get("lines", []):
            toks = [Tok(s["text"], s["font"], s["size"], s["flags"], tuple(s["bbox"]))
                    for s in l["spans"] if s["text"].strip()]
            if toks:
                lines.append(toks)
    return lines


def merge_lines(raw: List[List[Tok]]) -> List[Line]:
    """Merge text-layer lines that sit on the same baseline (the OCR layer
    splits a printed line at every sentence and at every stanza number)."""
    raw = sorted(raw, key=lambda ts: min(t.bbox[1] for t in ts))
    out: List[Line] = []
    for toks in raw:
        ln = Line(toks)
        cy = (ln.y0 + ln.y1) / 2
        best = None
        for cand in out[-6:]:
            c0, c1 = cand.y0, cand.y1
            ccy = (c0 + c1) / 2
            tol = max(3.0, 0.38 * min(cand.size, ln.size))
            overlap_x = not (ln.x0 >= cand.x1 - 1 or ln.x1 <= cand.x0 + 1)
            if overlap_x:
                continue
            if abs(cy - ccy) <= tol or (ln.size < 0.75 * cand.size and c0 - 2 <= cy <= c1):
                best = cand
        if best is not None:
            best.toks = sorted(best.toks + toks, key=lambda t: t.bbox[0])
        else:
            out.append(Line(sorted(toks, key=lambda t: t.bbox[0])))
    out.sort(key=lambda l: l.y0)
    return out


# ---------------------------------------------------------------- classification helpers

MARKER_RE = re.compile(
    r"\s*[\[\(\{/|]\s*([0-9IlJjiSOoZ\s\-–—~:]+(?:\s*-\s*[PF]a[uo]se)?(?:\s*-\s*[0-9IlJjiSOoZ]+)*)\s*[\]\)\}J/|1I]?\s*\.?\s*$")


def norm_marker(s: str) -> Optional[str]:
    """Normalise the inside of a stanza marker as the OCR read it:
    "IJ" -> "1", "1!-PaJise" -> "1-Pause", "4-f3-83" -> "4-13-83"."""
    s = s.replace("–", "-").replace("—", "-").replace("~", "-").replace(":", "").replace("_", "-")
    s = re.sub(r"\s+", "", s)
    s = re.sub(r"-?[PF]\w{0,2}[auiJ]{1,3}s\w?e?|-?Pause|-?Pa\w{1,3}e", "-P", s)
    if not s or len(s) > 26:
        return None
    s = re.sub(r"(?<=[0-9IlJjif!/|\]t])[Jj\]]+(?=$|-)", "", s)      # closing bracket read as J / ]
    s = s.translate(str.maketrans("IlijJ!/|]tfOoSZ", "111111111110052"))
    if re.search(r"[^0-9P\-]", s):
        return None
    s = re.sub(r"(?<=\d)1+(?=$)", lambda m: m.group(0), s)
    s = s.strip("-").replace("P", "Pause")
    s = re.sub(r"^(1)\1+$", "1", s)                                 # "[JJ" -> 11 -> 1
    if s == "Pause":
        return s
    if re.fullmatch(r"\d{1,3}(-\d{1,3}){0,7}", s) or re.fullmatch(r"\d{1,2}-Pause(-\d{1,3}){0,2}", s):
        return s
    return None


_OPEN = re.compile(r"(?:\[|\{|//|\(|\s[f/|])")


def split_marker(text: str) -> Tuple[str, Optional[str]]:
    tail = text[-30:]
    best = None
    for m in _OPEN.finditer(tail):
        best = m
    if best is None:
        return text, None
    start = len(text) - len(tail) + best.start()
    inner = text[start:].strip()
    inner = re.sub(r"^(\[|\{|//|\(|[f/|])", "", inner)
    inner = re.sub(r"[\]\}\)J|/\s.,;:'’]*$", lambda m: m.group(0).replace("J", "J"), inner)
    core = re.sub(r"[\]\})|\s.,;:'’]+$", "", inner)
    # "[1J" / "[JJ": a trailing J is the closing bracket unless it is all there is
    if len(core) > 1 and core.endswith(("J", "j")):
        core = core[:-1]
    mk = norm_marker(core)
    if mk is None:
        return text, None
    if inner.startswith("/") or best.group(0) == "//":
        pass
    if re.fullmatch(r"1*-?Pause", mk):
        mk = "1-Pause"                      # the refrain is "[1-Pause]" throughout
    return text[:start].rstrip(), mk


SPACED_CAPS = re.compile(r"[A-Z][A-Z\s.,\d]+[A-Za-z0-9]?")
MISREAD = {1: "l]Ii'", 2: "zZ", 3: "'J", 4: "A", 5: "sS", 6: "bG", 7: "?'T", 8: "sSB", 9: "gq", 0: "oO"}

ANG_RE = re.compile(r"^[P|]\s*[\.,·]?\s*[iI]?\s*([0-9IlOoSZsB]{1,4})\s*[\.\-·',]*\s*$")


def ang_value(text: str) -> Optional[int]:
    m = ANG_RE.match(text.strip())
    if not m:
        return None
    digits = m.group(1).translate(str.maketrans("IlOoSZsB", "11005258"))
    return int(digits) if digits.isdigit() else None


def word_score(lex: Lexicon, text: str) -> float:
    words = re.findall(r"[A-Za-z][A-Za-z']+", text)
    if not words:
        return 0.0
    good = sum(len(w) for w in words if lex.known(w.strip("'")))
    return good / max(1, sum(len(w) for w in words))


def is_furniture(line: Line, page_h: float, lex: Lexicon) -> bool:
    t = line.text
    if any(tk.font.startswith("Arial") for tk in line.toks) or "eLibrary" in t or "archive.org" in t:
        return True
    if line.y0 < 76 and len(t) <= 12:
        return True  # running page number
    if not re.search(r"[A-Za-z0-9ऀ-੿]", t):
        return True
    alnum = sum(c.isalnum() for c in t) / max(1, len(t))
    if line.y0 > page_h - 72 and ("~~" in t or "@" in t or "OO" in t or re.search(r"uru.{0,4}r?ant|ranth|Sahib\b", t) or (alnum < 0.5 and line.x0 < 160)):
        return True  # "Guru-Granth Sahib" footer and chain border
    if re.search(r"(~[^~]{0,3}){5,}|O{5,}|(@.?){4,}", t):
        return True  # chain border fragments
    return False


# ---------------------------------------------------------------- page parser

class Parser:
    def __init__(self, pdf_path: str, tess_dir: Optional[str]):
        self.doc = pymupdf.open(pdf_path)
        self.tess_dir = tess_dir
        self.lex = Lexicon()
        self.rep = Repairer(self.lex)
        self._prime()

    def _prime(self) -> None:
        """Check the whole vocabulary (both OCR readings, and every repair
        candidate of each unknown word) in two bulk hunspell passes."""
        from lexicon import variants, _ARCHAIC
        words = set()
        for p in self.doc:
            words.update(re.findall(r"[A-Za-z]+", p.get_text()))
        if self.tess_dir and os.path.isdir(self.tess_dir):
            for fn in os.listdir(self.tess_dir):
                if fn.endswith(".eng.tsv"):
                    with open(os.path.join(self.tess_dir, fn), encoding="utf-8") as f:
                        words.update(re.findall(r"[A-Za-z]+", f.read()))
        self.lex.bulk(words)
        second = set()
        for w in words:
            if not self.lex.cache.get(w):
                if len(w) >= 3:
                    second.update(variants(w))
                m = _ARCHAIC.match(w)
                if m:
                    s = m.group(1)
                    second.update({s, s + "e", s[:-1]})
        self.lex.bulk(second)

    def tess(self, i: int, kind: str) -> TessPage:
        if not self.tess_dir:
            return TessPage("", 1)
        return TessPage(os.path.join(self.tess_dir, f"p{i:03d}.{kind}.tsv"), 72 / 300)

    # ---- token-level repair -------------------------------------------------

    def repair_line(self, line: Line, teng: TessPage, tind: TessPage, indic_ok: bool) -> str:
        """Rebuild a line's text, repairing English words and recovering
        Gurmukhi/Devanagari runs from the multilingual OCR pass."""
        parts: List[str] = []
        prev = None
        bad_run: List[Tok] = []

        def flush_bad():
            if not bad_run:
                return
            bb = (min(t.bbox[0] for t in bad_run), min(t.bbox[1] for t in bad_run),
                  max(t.bbox[2] for t in bad_run), max(t.bbox[3] for t in bad_run))
            ind_words = [w for w in tind.within(bb) if INDIC.search(w)]
            ind = " ".join(ind_words)
            if ind_words:
                parts.append(clean_indic(ind))
            else:
                eng = " ".join(teng.within(bb))
                if eng and word_score(self.lex, eng) > 0.6:
                    parts.append(" ".join(self.rep.word(w) for w in eng.split()))
                elif not indic_ok:
                    parts.append(" ".join(self.rep.text(t.t.strip()) for t in bad_run))
                # else: unreadable Indic garbage in a note: dropped
            bad_run.clear()

        for tk in line.toks:
            s = tk.t.strip()
            if not s:
                continue
            gap = prev is not None and (tk.bbox[0] - prev.bbox[2] > 1.2 or tk.t[:1].isspace() or prev.t[-1:].isspace())
            core_words = re.findall(r"[A-Za-z]+", s)
            garbage = (
                indic_ok and (
                    tk.font.startswith("Hidden") and not all(self.lex.known(w) for w in core_words)
                    or (re.search(r"[~<>{}|®@\\^]|[a-z][A-Z]{2}|\bfu\b|[A-Za-z][!?;:][A-Za-z]", s) and not re.fullmatch(r"\[.*\]", s))
                    or (not any(self.lex.known(w) for w in core_words) and indic_share(" ".join(tind.within(tk.bbox, 1.0))) > 0.5
                        and fold_eq(s, " ".join(teng.within(tk.bbox, 1.0))) is False)
                )
            )
            if garbage:
                if gap and parts and not bad_run:
                    parts.append(" ")
                bad_run.append(tk)
                prev = tk
                continue
            flush_bad()
            if gap and parts:
                parts.append(" ")
            alt = " ".join(teng.within(tk.bbox, 0.8))
            if alt and not indic_ok and any(not self.lex.known(w) for w in re.findall(r"[A-Za-z]{3,}", s)) \
                    and re.search(r"[A-Za-z][^A-Za-z\s'’\-][A-Za-z]|^[A-Z]-[A-Z]|~", s) \
                    and all(self.lex.known(w) for w in re.findall(r"[A-Za-z]+", alt)) and abs(len(alt) - len(s)) <= 3:
                parts.append(" ".join(self.rep.word(w) if re.fullmatch(r"[A-Za-z]+", w) else w for w in alt.split()))
            elif alt and (re.search(r"[~<>{}|®@\\^]|\w[\]\)]\w", s) or re.search(r"[A-Za-z][^A-Za-z\s'\-.,;:!?][A-Za-z]|[A-Za-z][:;][A-Za-z]", s)) \
                    and word_score(self.lex, alt) > 0.7 and abs(len(alt) - len(s)) <= 3:
                parts.append(" ".join(self.rep.word(w) if re.fullmatch(r"[A-Za-z]+", w) else w for w in alt.split()))
            else:
                parts.append(self.rep.text(s, alt if alt else None))
            prev = tk
        flush_bad()
        txt = "".join(parts)
        txt = re.sub(r"\s+", " ", txt).strip()
        return txt

    # ---- footnote references ------------------------------------------------

    @staticmethod
    def find_ref(line: Line, body_size: float, expect: int, lex: Optional[Lexicon] = None) -> Optional[int]:
        """Locate footnote reference `expect` on this line; strip it from the
        tokens and return the index of the token it follows."""
        e = str(expect)
        for i, tk in enumerate(line.toks):
            s = tk.t.strip()
            small = tk.size < 0.78 * body_size
            raised = tk.bbox[3] < line.y1 - 2.0
            if (small or raised) and re.fullmatch(r"[\(\[]?" + e + r"[\)\],.;:]?", s) and i > 0:
                tail = s[len(re.match(r"[\(\[]?", s).group(0)) + len(e):]
                tk.t = tail
                return i - 1 if not tail else i
            m = re.fullmatch(r"([A-Za-z\)\(',\-]{2,}[a-z\)'])" + e + r"([,.;:!?\)]*)", s)
            if m:
                tk.t = m.group(1) + m.group(2)
                return i
            if expect == 1 and lex is not None:
                m = re.fullmatch(r"([A-Za-z\-']{3,}[a-z])[l\]I]([,.;:!?\)]*)", s)
                if m and lex.known(m.group(1).split("-")[-1]) and not lex.known(s.strip(",.;:!?)").split("-")[-1]):
                    tk.t = m.group(1) + m.group(2)
                    return i
        return None

    # ---- main -----------------------------------------------------------------

    def parse(self, i: int, mode: str) -> Page:
        page = self.doc[i]
        W, H = page.rect.width, page.rect.height
        teng, tind = self.tess(i, "eng"), self.tess(i, "ind")
        # Ang markers ("P. 241") in the margin, taken out before lines merge
        angs: List[Tuple[float, int]] = []
        raw = []
        ptoks = page_tokens(page)
        from collections import Counter as _C
        longs = [Line(t) for t in ptoks if Line(t).x1 - Line(t).x0 > 150]
        lm = min((x for x, n in _C(round(l.x0) for l in longs).items() if n >= 3), default=0)
        rm = max((l.x1 for l in longs), default=W)
        for toks in ptoks:
            # the chain border down the page edge reads as stray "8", "~", "l?)"
            toks = [t for t in toks if not (
                (t.bbox[2] < lm - 10 or t.bbox[0] > rm + 12) and len(t.t.strip()) <= 3
                and not re.search(r"[A-Za-z]{2}", t.t) and ang_value(t.t) is None
                and not re.fullmatch(r"[\dIlS]{1,2}\.", t.t.strip()))]
            if not toks:
                continue
            ln = Line(toks)
            v = ang_value(ln.text)
            if v is not None and (ln.bold or ln.x0 > 400 or ln.x1 < 130):
                angs.append((ln.y0, v))
                continue
            # a marker can share a raw line with the verse it stands beside
            k = len(toks)
            while k > 0 and toks[k - 1].bbox[0] > 420:
                k -= 1
            tail = Line(toks[k:]) if k < len(toks) else None
            if tail is not None and k > 0 and ang_value(tail.text) is not None and toks[k].bbox[0] - toks[k - 1].bbox[2] > 25:
                angs.append((tail.y0, ang_value(tail.text)))
                toks = toks[:k]
            raw.append(toks)
        lines = [l for l in merge_lines(raw) if not is_furniture(l, H, self.lex)]
        # margin numbers the text layer missed, from the Tesseract reading
        tw = teng.words
        for k, (txt, bb, _) in enumerate(tw):
            if bb[0] < 470 or not re.fullmatch(r"[PF][.,]?", txt):
                continue
            nxt = [w for w in tw[k + 1:k + 3] if abs(w[1][1] - bb[1]) < 4 and w[1][0] - bb[2] < 30]
            val = ang_value("P." + (nxt[0][0] if nxt else ""))
            if val is not None and not any(abs(y - bb[1]) < 14 for y, _ in angs):
                angs.append((bb[1], val))

        # body size from the upper part of the page: footnotes can outnumber the verse
        long_lines = [l for l in lines if l.x1 - l.x0 > W * 0.25 and l.y0 < H * 0.5 and word_score(self.lex, l.text) > 0.6]
        long_lines = long_lines or [l for l in lines if l.x1 - l.x0 > W * 0.25]
        body_size = statistics.median([l.size for l in long_lines]) if long_lines else 10.0

        # split off footnotes: first small line, low on the page, opening "1."
        notes_start = len(lines)
        for k, l in enumerate(lines):
            if l.y0 > H * 0.3 and l.size < 0.93 * body_size and re.match(r"^\s*[\dIl]{1,2}\s*[\.,]\s*\S", l.text):
                rest = [m for m in lines[k:] if len(m.text) > 6]
                if all(m.size < 0.95 * body_size or word_score(self.lex, m.text) < 0.5 for m in rest):
                    notes_start = k
                    break
        body, note_lines = lines[:notes_start], lines[notes_start:]

        notes = self.parse_notes(note_lines, i, teng, tind)

        # resolve reference markers in reading order
        refs_at: Dict[int, List[Tuple[int, int]]] = {}
        expect_list = [n.num for n in notes]
        li = 0
        for num in expect_list:
            for k in range(li, len(body)):
                pos = self.find_ref(body[k], body_size, num, self.lex)
                if pos is not None:
                    refs_at.setdefault(k, []).append((pos, num))
                    li = k
                    break

        # second pass: superscripts the OCR misread as a letter or mark
        # ("oneself?" for oneself-7, "Wills" for Will-8), searched only between
        # the neighbouring resolved references
        resolved = {num: k for k, lst in refs_at.items() for _, num in lst}
        for num in expect_list:
            if num in resolved:
                continue
            lo = max([k for n, k in resolved.items() if n < num] or [0])
            hi = min([k for n, k in resolved.items() if n > num] or [len(body) - 1])
            chars = MISREAD.get(num % 10, "")
            hit, best = None, -1
            for k in range(lo, hi + 1):
                for j, tk in enumerate(body[k].toks):
                    s = tk.t.strip()
                    m = re.fullmatch(r"([A-Za-z\-']{3,})([" + re.escape(chars) + r"])([,.;:!)]*)", s)
                    if not m or not self.lex.known(m.group(1).split("-")[-1]):
                        continue
                    if k == lo and lo in refs_at and j <= max(p for p, _ in refs_at[lo]):
                        continue
                    alt = " ".join(teng.within(tk.bbox, 0.8))
                    score = 0
                    if re.search(r"[0-9*°®'’\"?]\W*$", alt) and not alt.endswith(m.group(2)):
                        score += 2
                    if not self.lex.known(s.strip(",.;:!?)")):
                        score += 1
                    if m.group(2) in "sS" and score == 0:
                        continue
                    if score > best:
                        hit, best = (k, j, m), score
            if hit:
                k, j, m = hit
                body[k].toks[j].t = m.group(1) + m.group(3)
                refs_at.setdefault(k, []).append((j, num))
                resolved[num] = k

        # third pass: any remaining "word<n>" for an unresolved note n
        for num in expect_list:
            if num in resolved:
                continue
            for k, l in enumerate(body):
                for j, tk in enumerate(l.toks):
                    m = re.fullmatch(r"([A-Za-z\-'’)]{2,})" + str(num) + r"([,.;:!?)]*)", tk.t.strip())
                    if m:
                        tk.t = m.group(1) + m.group(2)
                        refs_at.setdefault(k, []).append((j, num))
                        resolved[num] = k
                        break
                if num in resolved:
                    break

        if mode == "verse":
            blocks = self.verse_blocks(body, W, i, teng, tind, refs_at, body_size)
        elif mode == "prose":
            blocks = self.prose_blocks(body, W, i, teng, tind, refs_at, body_size)
        else:
            raise ValueError(mode)

        # place Ang markers before the block nearest in y
        for y, v in angs:
            best = None
            for b in blocks:
                if b.kind in ("verse", "para") and b.y <= y + 4:
                    best = b
            idx = blocks.index(best) if best else 0
            blocks.insert(idx, Block("ang", str(v), y=y, page=i))
        unresolved = [n.num for n in notes if not any(n.num in b.refs for b in blocks)]
        first_resolved = min([n for b in blocks for n in b.refs] or [99])
        inv = next((b for b in blocks if b.kind == "inv"), None)
        if inv is not None:
            for num in [u for u in unresolved if u < first_resolved]:
                inv.refs.append(num)
            unresolved = [u for u in unresolved if u >= first_resolved]
        for num in unresolved:  # attach to the last body block so the note is never lost
            for b in reversed(blocks):
                if b.kind in ("verse", "para"):
                    b.refs.append(num)
                    break
        return Page(i, blocks, notes)

    def parse_notes(self, lines: List[Line], i: int, teng: TessPage, tind: TessPage) -> List[Note]:
        notes: List[Note] = []
        expect = 1
        for l in lines:
            txt = self.repair_line(l, teng, tind, indic_ok=True)
            m = re.match(r"^[\s\-–~.,]*([\dIlSsbOoZ]{1,2})\s*[\.,]\s*(.*)$", txt)
            num = None
            if m:
                n = int(m.group(1).translate(str.maketrans("IlSsbOoZ", "11556002")))
                if n == expect:
                    num = n
            if num is not None:
                notes.append(Note(num, m.group(2), i))
                expect += 1
            elif notes:
                prev = notes[-1].text
                if prev.endswith("-") and txt[:1].islower():
                    notes[-1].text = prev[:-1] + txt
                else:
                    notes[-1].text = prev + " " + txt
        for n in notes:
            n.text = tidy(n.text)
        return notes

    def line_text(self, l: Line, refs: List[Tuple[int, int]], teng, tind) -> Tuple[str, List[int]]:
        # insert a placeholder token after each reference position
        nums = []
        if refs:
            toks = list(l.toks)
            for pos, num in sorted(refs, reverse=True):
                pos = max(0, min(pos, len(toks) - 1))
                ph = Tok(f"⁣{num}⁣", toks[pos].font, toks[pos].size, 0,
                         (toks[pos].bbox[2], toks[pos].bbox[1], toks[pos].bbox[2] + 0.1, toks[pos].bbox[3]))
                toks.insert(pos + 1, ph)
                nums.append(num)
            l = Line(toks)
        txt = self.repair_line(l, teng, tind, indic_ok=False)
        return txt, sorted(nums)

    def verse_blocks(self, body: List[Line], W: float, i: int, teng, tind, refs_at, body_size) -> List[Block]:
        from collections import Counter
        xs = Counter(round(l.x0 / 3) * 3 for l in body if l.x1 - l.x0 > 100 and l.size > 0.9 * body_size)
        margin = xs.most_common(1)[0][0] if xs else 60
        right = max([l.x1 for l in body if l.x1 - l.x0 > 120] or [W - 60])
        mid = (margin + right) / 2
        blocks: List[Block] = []
        k = 0
        while k < len(body):
            l = body[k]
            raw = l.text
            score = word_score(self.lex, raw)
            # blackletter invocation (possibly several lines)
            spaced_caps = SPACED_CAPS.fullmatch(raw)   # "BARA M A HA"
            if not spaced_caps and ((score < 0.55 and BLACKLETTER_HINTS.search(raw) and len(raw) > 12) or (
                    l.size > 1.12 * body_size and score < 0.6 and len(raw) > 6)):
                grp = [l]
                while k + 1 < len(body):
                    n = body[k + 1]
                    if n.y0 - grp[-1].y1 < 12 and word_score(self.lex, n.text) < 0.6 and not SPACED_CAPS.fullmatch(n.text) and (
                            BLACKLETTER_HINTS.search(n.text) or n.size > 1.05 * body_size):
                        grp.append(n)
                        k += 1
                    else:
                        break
                joined = " ".join(g.text for g in grp)
                long_form = len(grp) > 1 or re.search(r"per|Hate|[JF]ear|exist|urus|reat", joined)
                if blocks and blocks[-1].kind == "inv" and blocks[-1].y > l.y0 - 40:
                    blocks[-1].text = "long" if long_form or blocks[-1].text == "long" else "short"
                else:
                    blocks.append(Block("inv", "long" if long_form else "short", y=l.y0, page=i))
                k += 1
                continue
            text, nums = self.line_text(l, refs_at.get(k, []), teng, tind)
            centre = (l.x0 + l.x1) / 2
            text_wo, mk = split_marker(text)
            centred = l.x0 > margin + 22 and abs(centre - mid) < 40 and (l.x1 - l.x0) < (right - margin) * 0.8
            if not text_wo and mk:
                # bare marker on its own line: belongs to previous verse line
                for b in reversed(blocks):
                    if b.kind == "verse":
                        b.marker = b.marker or mk
                        break
                k += 1
                continue
            if centred and not mk and not is_heading(text) and not (l.italic or text.startswith("(")):
                centred = False          # a short centred line of verse, not a title
            if centred and not mk:
                kind = "sub" if (l.italic or text.startswith("(")) else "head"
                blocks.append(Block(kind, tidy(text), y=l.y0, page=i, refs=nums))
            elif (abs(l.x0 - margin) < 10 and not mk and len(text) < 34 and
                  (l.bold or re.match(r"^(Shaloka|Shalok|Pauri|Paori|M\.\s*\d|Chhant|Dakhna|Dakhne|Slok|Ashtapadi|Shabad|Chhaka|Rahao)\b", text))
                  and not re.search(r"[,;]$", text)):
                blocks.append(Block("label", tidy(text.rstrip(":").rstrip()), y=l.y0, page=i, bold=True, refs=nums))
            else:
                b = Block("verse", tidy(re.sub(r"^[.,·'`\-_~]+\s*(?=[A-Z(“\"'‘])|^~\s*", "", text_wo)), marker=mk or "", y=l.y0, x=l.x0, page=i, refs=nums)
                prevv = blocks[-1] if blocks else None
                if (prevv is not None and prevv.kind == "verse" and not prevv.marker and
                        margin + 5 < l.x0 < margin + 45 and l.y0 - prevv.y < body_size * 1.6):
                    # wrapped continuation of the previous line
                    prevv.text = join_wrap(prevv.text, b.text)
                    prevv.marker = b.marker
                    prevv.refs += b.refs
                else:
                    blocks.append(b)
            k += 1
        return blocks

    def prose_blocks(self, body: List[Line], W: float, i: int, teng, tind, refs_at, body_size) -> List[Block]:
        from collections import Counter
        xs = Counter(round(l.x0 / 3) * 3 for l in body if l.x1 - l.x0 > W * 0.5)
        margin = min(xs, key=lambda x: (-xs[x], x)) if xs else 50
        right = max([l.x1 for l in body if l.x1 - l.x0 > W * 0.5] or [W - 50])
        mid = (margin + right) / 2
        blocks: List[Block] = []
        prev_line: Optional[Line] = None
        for k, l in enumerate(body):
            text, nums = self.line_text(l, refs_at.get(k, []), teng, tind)
            if not text:
                continue
            gap = l.y0 - prev_line.y1 if prev_line else 99
            centre = (l.x0 + l.x1) / 2
            full = l.x1 > right - 25
            centred = abs(centre - mid) < 30 and l.x0 > margin + 25 and not full
            indented = margin + 12 < l.x0 < margin + 70
            if centred:
                kind = "head" if (l.size > body_size * 1.05 or l.bold or text.isupper()) else "centre"
                blocks.append(Block(kind, tidy(text), y=l.y0, page=i, italic=l.italic, refs=nums))
                prev_line = l
                continue
            cur = blocks[-1] if blocks and blocks[-1].kind == "para" else None
            new_para = cur is None or indented or gap > body_size * 1.0 or cur.text.endswith(":-") or getattr(cur, "_closed", False)
            if new_para:
                b = Block("para", tidy(text), y=l.y0, x=l.x0, page=i, refs=nums, indent=indented, italic=l.italic)
                blocks.append(b)
            else:
                cur.text = join_wrap(cur.text, tidy(text))
                cur.refs += nums
            # a short last line closes the paragraph
            if not full and blocks and blocks[-1].kind == "para":
                setattr(blocks[-1], "_closed", True)
            prev_line = l
        return blocks


def indic_share(s: str) -> float:
    letters = [c for c in s if c.isalpha()]
    return sum(bool(INDIC.match(c)) for c in letters) / len(letters) if letters else 0.0


def fold_eq(a: str, b: str):
    """True when two OCR readings agree (ignoring case/punctuation); None when b is empty."""
    if not b:
        return None
    f = lambda s: re.sub(r"[^a-z]", "", s.lower())
    return f(a) == f(b) and bool(f(a))


def join_wrap(a: str, b: str) -> str:
    if a.endswith("-") and b[:1].islower() and not a.endswith(" -"):
        return a[:-1] + b
    return a + " " + b


def clean_indic(s: str) -> str:
    s = re.sub(r"[|]+", "", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s


def tidy(s: str) -> str:
    s = s.replace("\u2063", "\u2063")
    s = re.sub(r"\s+", " ", s).strip()
    s = re.sub(r"(^|[\s\"'“(])[0o](?=\s+[A-Za-z])", r"\1O", s)      # vocative O read as 0 / o
    s = re.sub(r"(^|[,;:!?“\"(]\s?)[ab](?=\s+(Lord|God|Thou|my|dear|friend|mind|brother|brothers|Master|Merciful|Beloved|Saints?|Yogi|Pundit|Mullah|Qazi|Nānak|Kabir|soul|self|sister|mother|man|men|bride|Bride|Creator|Pure|ignorant|foolish|self-willed|Compassionate)\b)", r"\1O", s)
    s = re.sub(r"\bN\S{1,3}[nh]ak\b", lambda m: m.group(0) if m.group(0) in ("Nānak",) else "Nānak", s)
    s = re.sub(r"(?<=[^\W\d_]{2})\.(?=[A-Z][^\W\d_])", ". ", s)
    s = re.sub(r"(?<=[a-z]{2})\d(?=[\s,.;:!?)’”]|$)", "", s)              # stray superscript digits
    s = re.sub(r"(?<=[,.;:!?])(\s*\.)+$", "", s)                          # specks after the stop: "Lord, ."
    s = re.sub(r"(?<=\w)\s+\.(\s*\.)*$", ".", s) if not s.endswith("...") else s
    s = re.sub(r"(^|\s)-\s?[0o](?=,|\s)", r"\1—O", s)                    # "-0, cursed be"
    s = re.sub(r"\(\s*\)", "", s)
    s = re.sub(r"\s*,(?=[A-Za-z])", ", ", s)
    s = s.replace("''", "\u201d").replace("``", "\u201c")
    # the 1960 setting puts a space before ; : ? ! -- keep it, but never let
    # the mark wrap onto a line of its own
    s = re.sub(r"\s+([;:?!])", "\u00a0\\1", s)
    return s
