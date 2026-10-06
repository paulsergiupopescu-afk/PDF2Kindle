"""Embedded typefaces (all SIL Open Font License), instanced and subset.

  EB Garamond          body text -- Claude Garamond's 16th-century roman, the
                       old-style face closest to the edition's letterpress
  UnifrakturMaguntia   blackletter for the invocations and title, as printed
  Noto Serif Gurmukhi  Gurmukhi words in notes and glossary, and the ੴ
  Noto Serif Devanagari Sanskrit / Hindi quotations in the notes

Variable fonts are pinned to static instances (Kindle readers ignore
variation axes) and subset to the characters the book actually uses, keeping
the OpenType layout tables Indic shaping depends on.

Download them with `python fonts.py DIR` (from the google/fonts repository).
"""

from __future__ import annotations

import os
import sys
import urllib.request
from typing import Dict, Optional

SOURCES = {
    "EBGaramond[wght].ttf": "ofl/ebgaramond/EBGaramond%5Bwght%5D.ttf",
    "EBGaramond-Italic[wght].ttf": "ofl/ebgaramond/EBGaramond-Italic%5Bwght%5D.ttf",
    "UnifrakturMaguntia-Book.ttf": "ofl/unifrakturmaguntia/UnifrakturMaguntia-Book.ttf",
    "NotoSerifGurmukhi[wght].ttf": "ofl/notoserifgurmukhi/NotoSerifGurmukhi%5Bwght%5D.ttf",
    "NotoSerifDevanagari[wdth,wght].ttf": "ofl/notoserifdevanagari/NotoSerifDevanagari%5Bwdth,wght%5D.ttf",
}
BASE = "https://raw.githubusercontent.com/google/fonts/main/"

# output name -> (source, axis pins, family, weight, style)
FACES = {
    "garamond-regular.ttf": ("EBGaramond[wght].ttf", {"wght": 400}, "EB Garamond", 400, "normal"),
    "garamond-semibold.ttf": ("EBGaramond[wght].ttf", {"wght": 600}, "EB Garamond", 700, "normal"),
    "garamond-italic.ttf": ("EBGaramond-Italic[wght].ttf", {"wght": 400}, "EB Garamond", 400, "italic"),
    "garamond-semibolditalic.ttf": ("EBGaramond-Italic[wght].ttf", {"wght": 600}, "EB Garamond", 700, "italic"),
    "fraktur.ttf": ("UnifrakturMaguntia-Book.ttf", {}, "Unifraktur Maguntia", 400, "normal"),
    "gurmukhi.ttf": ("NotoSerifGurmukhi[wght].ttf", {"wght": 400}, "Noto Serif Gurmukhi", 400, "normal"),
    "devanagari.ttf": ("NotoSerifDevanagari[wdth,wght].ttf", {"wght": 400, "wdth": 100}, "Noto Serif Devanagari", 400, "normal"),
}


def download(dest: str) -> None:
    os.makedirs(dest, exist_ok=True)
    for name, path in SOURCES.items():
        out = os.path.join(dest, name)
        if not os.path.exists(out):
            print("fetching", name)
            urllib.request.urlretrieve(BASE + path, out)


def _find(fonts_dir: str, name: str) -> Optional[str]:
    for cand in (name, name.replace("[", "%5B").replace("]", "%5D").replace(",", ",")):
        p = os.path.join(fonts_dir, cand)
        if os.path.exists(p):
            return p
    return None


def prepare(fonts_dir: Optional[str], work: str, text: str) -> Dict[str, str]:
    """Instance + subset every face whose source is present. Returns name -> path."""
    if not fonts_dir:
        return {}
    from fontTools.ttLib import TTFont
    from fontTools.varLib import instancer
    from fontTools import subset

    out_dir = os.path.join(work, "fonts")
    os.makedirs(out_dir, exist_ok=True)
    chars = set(text) | set("ੴ0123456789[]().,;:!?‘’“”—–-·•❦❧☙ ") | {chr(c) for c in range(0x20, 0x7F)}
    gur = {c for c in chars if "਀" <= c <= "੿"} | {"਼", "੍", "ੱ", "ੰ"}
    dev = {c for c in chars if "ऀ" <= c <= "ॿ"} | {"्", "़"}
    latin = {c for c in chars if not ("ऀ" <= c <= "੿")}
    result: Dict[str, str] = {}
    for name, (src, pins, family, weight, style) in FACES.items():
        p = _find(fonts_dir, src)
        if not p:
            print(f"  (font {src} not found in {fonts_dir}; skipped)")
            continue
        dst = os.path.join(out_dir, name)
        f = TTFont(p)
        if "fvar" in f and pins:
            f = instancer.instantiateVariableFont(f, pins, inplace=False)
        uni = gur | {"ੴ"} | set(" ()-.,") if "gurmukhi" in name else dev | set(" ()-.,") if "devanagari" in name else latin
        opts = subset.Options()
        opts.layout_features = ["*"]
        opts.name_IDs = ["*"]
        opts.name_languages = ["*"]
        opts.notdef_outline = True
        opts.glyph_names = False
        opts.hinting = False
        sub = subset.Subsetter(opts)
        sub.populate(unicodes=[ord(c) for c in uni])
        sub.subset(f)
        f.save(dst)
        result[name] = dst
    return result


def font_face_css(files: Dict[str, str]) -> str:
    css = []
    for name in files:
        _, _, family, weight, style = FACES[name]
        css.append(f'@font-face {{ font-family: "{family}"; font-weight: {weight}; font-style: {style}; '
                   f'src: url("../fonts/{name}"); }}')
    return "\n".join(css) + "\n\n"


if __name__ == "__main__":
    download(sys.argv[1] if len(sys.argv) > 1 else "fonts")
