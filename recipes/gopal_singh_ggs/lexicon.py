"""Word validity and OCR repair for the Gopal Singh translation.

The scan carries an OCR text layer that is good for English (~97% of words
read) but wrong in systematic ways: the macron of Nānak/Rāg/Māyā comes out as
"ii", "li", "fi" or "5"; "h" is read as "b" (Majb, Sbaloka); "l" and "I" swap;
the closing bracket of a stanza number becomes "J". Each token is checked
against hunspell plus a curated list of the book's own vocabulary (proper
names, Panjabi terms, the translator's archaic verb forms), and repaired from
(a) a second, independent Tesseract reading of the same spot, then
(b) a small table of known confusions -- accepting a change only when it
turns an unknown token into a known word.
"""

from __future__ import annotations

import re
import subprocess
from typing import Dict, Iterable, List, Optional

# Spellings the book itself uses, with diacritics as printed in the 1960 edition.
# key = ASCII fold (lower-case), value = canonical form.
CANON = {w.lower().translate(str.maketrans("āīūĀ", "aiuA")): w for w in """
Nānak Rāg Rāga Rāgas Māyā Mājh Vār Vārs Sohilā Vanjārā Bārā Māha Bāwan Chandāl Onkār
Dharmarāja Gopāl Bairāgan Nirvān Āsā Shāstras Purānas Ravidās Ravidāsji Dhanāsri
Khākhā Mammā Dhadhā Kakkā Rārā Nām Kartā Sanskāra Amrit Yāma Akāl Brahmā Shāstra Purāna
""".split()}

# Book vocabulary hunspell does not know: names, Panjabi/Sanskrit terms, and
# the King-James register the translation is written in.
EXTRA = set("""
Gauri Guareri Pauri Paori Shaloka Shalokas Kabir Kabirji Blest blest Majh Poorbi Yond yond Mantram
Unstruck unstruck Tis tis forsure Forsure Bairagan Ashtapadi Ashtapadis Chandan chandan Chatrik Smiritis
Ravidas Cheti Lalla Teja downsitting Amar Chaupadas Chaupada Dupadas Dupada Jodh Khand Nirvan Panjab
Arjun Damodara Indras Japu Jnanindriyas Karmindriyas Maghara Maghar Qazis Qazi wrapt innerself innerselves
Shastras Shastra amness Ahankara Akal Anhad Asarh Bhadon Bhai Bhakta Bhaktas Chaitra Chhant Chhants
Gopis Gujri Gunas Gurdas Gurmukh Gurmukhs Hiranyakashipu Jainas Kashi Krishna Krishnas Madhu Narada Namdeva
Namdev Prehlada Prahlada Puranas Ranjit Sanaka Sanyasin Sanyasins Sheshnaga Shudras Shunya Kartik Magha
Pahre Vanjara Dakhna Bhagauti Chakvi Chakora Dharmaraja Dharmarajas Mandukopanishad Patanjali Pirs Pir
Rishis Rishi Saram Shravan Sushmana Svasti Trumpp Turiya Yogi Yogis Siddha Siddhas Sikh Sikhs Sikhism
Guru Gurus Granth Sahib Nanak Angad Amardas Ramdas Arjan Har Gobind Govind Tegh Bahadur Gopal Singh
Maya Rag Raga Ragas Var Vars Sohila Sukhmani Thitti Bawan Akhari Bara Maha Mahala Ek Svan Onkar Nam
Waheguru Vaheguru Brahma Shiva Vishnu Lakshmi Parvati Yama Yamas Kali Treta Duapar Satyuga Kaliyuga
Vedas Veda Vedic Quran Purana Smritis Simritis Sankhya Prakriti Purusha Purkhu Hukum Raza Hari Ram
Pundit Pundits Brahmin Brahmins Kshatriya Vaishya Shudra Khatri Jat Mullah Mullahs Sufi Sufis Vaishnava
Vaishnavic Maharaja Lahore Amritsar Goindwal Kartarpur Talwandi Sultanpur Mardana Bala Kapur Mohan
Radhakrishnan Nehru Jawaharlal Dhebar Gadgil Allied Sachdev Namdhari Jagjit Satguru
Abidest Attainest Believest Bestoweth Contemplatest Createst Createth Deliverest Destroyest Doeth Dwellest
Engagest Findest Forgivest Gatherest Givest Grantest Hearest Keepest Knowest Knoweth Losest Lovest
Makest Mergest Pervadest Playest Pleaseth Receivest Savest Sayeth Seekest Servest Serveth Singest Sittest
Supportest Sustainest Takest Unitest Willest Willeth Workest Blessest Becomest Belongest Comest Goest
Thee Thou Thy Thine Ye Hath Doth Art Shalt Wilt Canst Dost
""".split())
EXTRA |= {w.lower() for w in EXTRA}

_ARCHAIC = re.compile(r"^(.{3,}?)(est|eth|st)$", re.I)
_WORD = re.compile(r"[A-Za-zÀ-ɏḀ-ỿ]+(?:['’][A-Za-z]+)?")

def fold(w: str) -> str:
    return w.lower().translate(str.maketrans("āīūĀĪŪ", "aiuaiu"))


class Lexicon:
    def __init__(self) -> None:
        self.cache: Dict[str, bool] = {}
        self._proc = None

    def _pipe(self):
        if self._proc is None:
            self._proc = subprocess.Popen(["hunspell", "-a", "-d", "en_GB,en_US"], stdin=subprocess.PIPE,
                                          stdout=subprocess.PIPE, text=True, bufsize=1)
            self._proc.stdout.readline()  # version banner
        return self._proc

    def _check(self, w: str) -> bool:
        p = self._pipe()
        p.stdin.write("^" + w + "\n")
        p.stdin.flush()
        ok = True
        while True:
            line = p.stdout.readline()
            if line in ("\n", ""):
                break
            if line[:1] in "&#":
                ok = False
        return ok

    def bulk(self, words: Iterable[str]) -> None:
        todo = sorted({w for w in words if w and w not in self.cache and _WORD.fullmatch(w)})
        if not todo:
            return
        out = subprocess.run(["hunspell", "-d", "en_GB,en_US", "-l"], input="\n".join(todo),
                             capture_output=True, text=True).stdout.split()
        bad = set(out)
        for w in todo:
            self.cache[w] = w not in bad

    def prime(self, words: Iterable[str]) -> None:
        todo = sorted({w for w in words if w and w not in self.cache and _WORD.fullmatch(w)})
        if len(todo) > 2000:
            out = subprocess.run(["hunspell", "-d", "en_GB,en_US", "-l"], input="\n".join(todo),
                                 capture_output=True, text=True).stdout.split()
            bad = set(out)
            for w in todo:
                self.cache[w] = w not in bad
            return
        p = self._pipe()
        for k in range(0, len(todo), 200):     # small batches keep the pipe from filling
            chunk = todo[k:k + 200]
            p.stdin.write("".join("^" + w + "\n" for w in chunk))
            p.stdin.flush()
            for w in chunk:
                ok = True
                while True:
                    line = p.stdout.readline()
                    if line in ("\n", ""):
                        break
                    if line[:1] in "&#":
                        ok = False
                self.cache[w] = ok

    def known(self, w: str) -> bool:
        if not w:
            return False
        if w in EXTRA or fold(w) in CANON:
            return True
        if not _WORD.fullmatch(w):
            return False
        if w not in self.cache:
            self.prime([w])
        if self.cache[w]:
            return True
        m = _ARCHAIC.match(w)
        if m:  # Givest, Knoweth, Pervadest ...
            stem = m.group(1)
            for s in (stem, stem + "e", stem[:-1] if len(stem) > 3 and stem[-1] == stem[-2] else stem):
                if s not in self.cache:
                    self.prime([s])
                if self.cache.get(s):
                    return True
        return False


# Single-character (and digraph) OCR confusions seen in this scan, in rough
# order of frequency.
_CONFUSIONS = [
    ("b", "h"), ("I", "l"), ("l", "I"), ("1", "l"), ("rn", "m"), ("c", "e"), ("e", "c"),
    ("li", "h"), ("ii", "u"), ("n", "u"), ("u", "n"), ("lI", "ll"), ("Il", "ll"), ("II", "ll"),
    ("tI", "th"), ("0", "o"), ("Q", "O"), ("f", "t"), ("t", "f"), ("i", "l"), ("l", "i"),
    ("cl", "d"), ("d", "cl"), ("h", "b"), ("S", "s"), ("5", "s"), ("8", "s"), ("v", "y"),
]
# OCR renderings of the macron vowel ā.
_MACRON = re.compile(r"ii|li|il|1i|i1|fi|fl|ll|lI|Il|I1|5|ci|ri|ã|á|à|â|ä|ii")


def _subs(w: str, frm: str, to: str):
    i = w.find(frm)
    while i != -1:
        yield w[:i] + to + w[i + len(frm):]
        i = w.find(frm, i + 1)


def variants(w: str) -> List[str]:
    out: List[str] = []
    # macron restorations: compare against the canonical list only
    for m in _MACRON.finditer(w):
        cand = w[:m.start()] + "a" + w[m.end():]
        if fold(cand) in CANON:
            out.append(cand)
    for frm, to in _CONFUSIONS:
        out.extend(_subs(w, frm, to))
    first = list(out)
    for v in first[:40]:
        for frm, to in _CONFUSIONS[:8]:
            out.extend(_subs(v, frm, to))
    return out


def canonical(w: str) -> str:
    """Apply the book's diacritic spellings (Nanak -> Nānak) preserving case."""
    c = CANON.get(fold(w))
    if not c:
        return w
    if w.isupper() and len(w) > 1:
        return c.upper()
    if w[0].islower():
        return c[0].lower() + c[1:]
    return c


class Repairer:
    def __init__(self, lex: Lexicon) -> None:
        self.lex = lex
        self._memo: Dict = {}

    def word(self, core: str, alt: Optional[str] = None) -> str:
        """Return the best reading of one OCR word (letters only, no punctuation)."""
        if not core:
            return core
        if self.lex.known(core):
            return canonical(core)
        if alt:
            alt_core = alt.strip(".,;:!?\"'()[]{}‘’“”")
            if alt_core and self.lex.known(alt_core) and abs(len(alt_core) - len(core)) <= 2:
                return canonical(alt_core)
        key = (core, alt)
        if key in self._memo:
            return self._memo[key]
        res = core
        m = re.fullmatch(r"(of|to|in|on|and|the|is|by|at|as|for|with|his|His|thy|Thy|my|not|be|all|from|unto|Of|In|If|To)([A-Za-z]{2,})", core)
        if m and self.lex.known(m.group(2)) and not self.lex.known(core):
            res = m.group(1) + " " + canonical(m.group(2))          # "ofthe", "ofHis", "IfI"
            self._memo[key] = res
            return res
        if len(core) >= 3 and re.search(r"[A-Za-z]", core):
            vs = variants(core)
            self.lex.prime(vs)                     # one batched hunspell call
            for v in vs:
                if self.lex.known(v):
                    res = canonical(v)
                    break
        self._memo[key] = res
        return res

    def text(self, s: str, alt: Optional[str] = None) -> str:
        """Repair every word in a run of text."""
        def rep(m: re.Match) -> str:
            return self.word(m.group(0))
        if alt is not None and len(_WORD.findall(s)) == 1 and len(_WORD.findall(alt)) == 1:
            w = _WORD.search(s)
            fixed = self.word(w.group(0), _WORD.search(alt).group(0))
            return s[:w.start()] + fixed + s[w.end():]
        return re.sub(r"[A-Za-z0-9ÀāĀ]+", rep, s)
