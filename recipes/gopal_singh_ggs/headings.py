"""Hymn titles: recognition and OCR clean-up.

Titles in this volume are built from a small vocabulary -- the rāg, its
variant, the poetic form, and the author ("M. 5" = the fifth Guru, Arjan;
"Kabirji", "Ravidāsji" for the Bhaktas). Matching each word fuzzily against
that vocabulary both repairs the OCR ("Gaur66" -> Gauri, "gig" -> Rāg) and
tells a real title from a short centred line of verse.
"""

from __future__ import annotations

import difflib
import re

VOCAB = """
Sri Rāg Rāga Mājh Gauri Āsā Gujri Dhanāsri Sorath Poorbi Guareri Bairāgan Cheti Deepaki Mālwā Mālā
Dakhni Karhale Chhant Chhants Ashtapadi Ashtapadis Chaupadas Chaupada Dupadas Dupada Panchpadas Panchpada
Tipadas Tipada Pahre Vanjārā Vār Vārs Shaloka Shalokas Pauri Kabirji Kabir Ravidāsji Ravidās Nāmdevji
Nāmdev Trilochan Beni Bhagat Bhaktas Sukhmani Bāwan Akhari Thitti Bārā Māha Japu So-Dar So-Purukhu Sohilā
Hymns Couplets Ekam Purnima Measure Days Seven Night Day Rain Din Sohila Āratī Arti Jaidev Dhanna Sain Pipa
Sadhna Farid Sundar Mardana Satta Balwand Chaupai
""".split()
_FOLD = str.maketrans("āīūĀĪŪ", "aiuAIU")
_VFOLD = {w.translate(_FOLD).lower(): w for w in VOCAB}
_COMMON = {"of", "and", "the", "with", "by", "in", "to", "a"}


def fold(s: str) -> str:
    return s.translate(_FOLD)


def _fix_word(w: str) -> str:
    core = re.sub(r"[^A-Za-zāĀīū\-]", "", w)
    if len(core) < 3 or core.lower() in _COMMON:
        return w
    key = fold(core).lower()
    if key in _VFOLD:
        return w.replace(core, _VFOLD[key])
    m = difflib.get_close_matches(key, list(_VFOLD), n=1, cutoff=0.74)
    if m:
        return re.sub(re.escape(core) + r"[^\s,;:()]*", _VFOLD[m[0]], w, count=1)
    return w


def fix_heading(text: str) -> str:
    t = re.sub(r"^[^A-Za-zĀ(⁣]+", "", text)
    t = t.replace(" ", " ")
    t = re.sub(r"(?<=[a-zā\d!&])M\s*(?=[.,;:])", " M", t)              # "GauriM. 1"
    t = re.sub(r"\bGaud\b", "Gauri", t)
    t = re.sub(r"B\s?[AĀ]\s?R\s?[AĀ]\s?M\s?[AĀ]\s?H\s?[AĀ]\w?", "Bārā Māha", t, flags=re.I)
    t = re.sub(r"\bAs[&a](?!\w)", "Āsā", t)
    # the mahala: "M. 5" in every OCR disguise (M,5  M.'S  1.\1. 5  M .. 1  MajbM.3)
    t = re.sub(r"(?:\bM|1\.\\1|J\\1|IVI|\\1)\s*[.,;:'’\-]*\s*['’]?\s*([0-9IlSitg§])(?!\w)\s*[.,]?(?!\w)",
               lambda m: " M. " + m.group(1).translate(str.maketrans("IlSitg§", "1151195")), t)
    t = re.sub(r"\bM\. ([1-59])\d\b", r"M. \1", t)                    # "M. 31": mahala 3 + a note
    t = re.sub(r"(M\. \d+)(?=[A-Za-z(])", r"\1 ", t)
    t = re.sub(r"(?<=[kcsgdjtpKCSGDJTP])b(?=[aeiou])", "h", t)        # Dakbni, Cbhant, Sokbmani
    t = re.sub(r"\bA\.?s[aiā]\b\.?['’]?\w?\.?", "Āsā", t)
    t = re.sub(r"Āsā['’]i\.?", "Āsā", t)
    t = re.sub(r"(M\. \d) \d\b", r"\1", t)
    t = re.sub(r"\bR[a-zāi1l5!]{1,2}g\b|\bgig\b", "Rāg", t)
    t = re.sub(r"(?<=[A-Za-zā])\d+(?=[\s,.:;)]|$)", "", t)          # stray note digits: "Chhants4"
    t = " ".join(_fix_word(w) for w in t.split())
    t = re.sub(r"\s+([,:;])", r"\1", t)
    t = re.sub(r"\s{2,}", " ", t)
    t = re.sub(r"[\s.,;:\\\-'’“”\"]+$", "", t)
    return t.strip()


def is_heading(text: str) -> bool:
    """True when a centred line reads as a hymn title rather than verse."""
    t = fix_heading(text)
    if re.search(r"\bM\. \d\b", t):
        return True
    words = [re.sub(r"[^A-Za-zāĀ\-]", "", w) for w in t.split()]
    words = [w for w in words if w]
    if not words or len(words) > 9:
        return False
    hits = sum(1 for w in words if w in VOCAB)
    return hits >= 1 and hits * 2 >= len([w for w in words if w.lower() not in _COMMON]) - 1


if __name__ == "__main__":
    for s in ["Sri gigM. 1", "Sri Rāg M.'t", "Rāg Gaur66 MalwaM. 5", "Rāg Gaur! 11", "Mājh M. §",
              "Rāg Āsā'i.M. 1", "As&M. 1", "Sri Rāg-M. 3", "Rāg Gauri Chetiof Nimdevji", "Sri Rāg 1.\\1. 5",
              "So-Dar2, Rig A.si. M.3 1", "GauriM. 1 Dakbni", "Gauri Sokbmani⁣1⁣ , M. 5",
              "fixed upon the orbit of its own sky.", "He whom the Lord Trusts", "Or, the water and the Waves?",
              "Thitti of Kabirji", "Rāg Gauri. Seven Days of Kabirji", "Vār of Gaud M. 4", "Shaloka M; 1 .",
              "Pauri (Ekam)", "Rāg Gauri Guareri, Couplets of Ravidāsji", "Rāg Gauri, Ashtapadts"]:
        print(f"{s!r:45} -> {fix_heading(s)!r:42} {is_heading(s)}")
