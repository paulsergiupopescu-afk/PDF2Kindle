"""Quality report for a built EPUB: XML well-formedness, broken links,
leftover OCR debris, and the headings list for a human eye.

    python qa.py book.epub [--headings]
"""

from __future__ import annotations

import re
import sys
import zipfile
from collections import Counter

from lxml import etree

sys.path.insert(0, __file__.rsplit("/", 1)[0])
from lexicon import Lexicon  # noqa: E402

NS = {"x": "http://www.w3.org/1999/xhtml", "epub": "http://www.idpf.org/2007/ops"}


def main(path: str, show_headings: bool) -> int:
    z = zipfile.ZipFile(path)
    names = z.namelist()
    ids = {}
    docs = {}
    errors = 0
    for n in names:
        if n.endswith((".xhtml", ".opf", ".ncx")):
            try:
                docs[n] = etree.fromstring(z.read(n))
            except etree.XMLSyntaxError as e:
                print(f"XML  {n}: {e}")
                errors += 1
    for n, d in docs.items():
        ids[n] = {e.get("id") for e in d.iter() if e.get("id")}
    # links
    dead = 0
    for n, d in docs.items():
        base = n.rsplit("/", 1)[0]
        for a in d.iter("{http://www.w3.org/1999/xhtml}a"):
            href = a.get("href") or ""
            if href.startswith("http"):
                continue
            f, _, frag = href.partition("#")
            tgt = n if not f else (base + "/" + f).replace("text/../", "")
            tgt = re.sub(r"[^/]+/\.\./", "", tgt)
            if tgt not in docs or (frag and frag not in ids.get(tgt, ())):
                dead += 1
                if dead <= 10:
                    print(f"LINK {n}: {href}")
    # text debris
    lex = Lexicon()
    words = Counter()
    glued = Counter()
    odd = Counter()
    for n, d in docs.items():
        if not n.endswith(".xhtml") or "nav" in n:
            continue
        txt = " ".join(d.itertext())
        for w in re.findall(r"[A-Za-z]+", txt):
            words[w] += 1
        for m in re.findall(r"\b[A-Za-z]{3,}\d\b", txt):
            glued[m] += 1
        for m in re.findall(r"\S*[~<>{}|®@\\^]\S*", txt):
            odd[m] += 1
    lex.bulk(words)
    unknown = {w: c for w, c in words.items() if not lex.known(w)}
    total = sum(words.values())
    print(f"words {total:,}  unknown {sum(unknown.values()):,} ({100 * sum(unknown.values()) / total:.2f}%)  "
          f"dead links {dead}  xml errors {errors}")
    print("glued digits:", " ".join(f"{w}" for w, _ in glued.most_common(40)), f"({sum(glued.values())})")
    print("odd tokens:", " ".join(w for w, _ in odd.most_common(30)), f"({sum(odd.values())})")
    print("top unknown:", " ".join(f"{w}:{c}" for w, c in sorted(unknown.items(), key=lambda x: -x[1])[:150]))
    if show_headings:
        for n, d in sorted(docs.items()):
            for h in d.iter("{http://www.w3.org/1999/xhtml}h3", "{http://www.w3.org/1999/xhtml}h2"):
                print("H", n.split("/")[-1], "".join(h.itertext()))
    return errors


if __name__ == "__main__":
    sys.exit(main(sys.argv[1], "--headings" in sys.argv))
