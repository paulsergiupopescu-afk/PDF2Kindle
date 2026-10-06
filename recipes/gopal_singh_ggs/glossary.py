"""The two-column "Glossary of Technical Terms" at the end of the volume.

Each column has terms (romanised, with the Gurmukhi in parentheses) at one x
position and definitions, opened by a colon, at another. Columns are found
from the x positions of those colons; entries are then rebuilt per column in
reading order.
"""

from __future__ import annotations

import html
import re
from collections import Counter
from typing import Dict, List, Tuple

from parse import Block, Line, Page, Tok, merge_lines, page_tokens, is_furniture


def _columns(lines: List[Line]) -> List[Tuple[float, float]]:
    """Return [(term_x, colon_x)] per column, left to right."""
    colons = Counter(round(l.x0 / 8) * 8 for l in lines if l.text.startswith(":"))
    cs = sorted(x for x, n in colons.items() if n >= 3)
    merged: List[float] = []
    for x in cs:
        if merged and x - merged[-1] < 40:
            continue
        merged.append(x)
    cols = []
    for k, c in enumerate(merged):
        lo = merged[k - 1] + 120 if k else 0
        terms = Counter(round(l.x0 / 4) * 4 for l in lines
                        if lo <= l.x0 < c - 25 and re.match(r"[A-ZĀ]", l.text))
        if terms:
            cols.append((min(x for x, n in terms.items() if n >= max(2, terms.most_common(1)[0][1] // 3)), c))
    return cols


def parse_glossary(P, page_range) -> Dict[int, Page]:
    out: Dict[int, Page] = {}
    for i in page_range:
        page = P.doc[i]
        H = page.rect.height
        teng, tind = P.tess(i, "eng"), P.tess(i, "ind")
        raw = [Line(sorted(t, key=lambda x: x.bbox[0])) for t in page_tokens(page)]
        raw = [l for l in raw if not is_furniture(l, H, P.lex) and l.y0 > 80]
        cols = _columns(raw)
        blocks: List[Block] = []
        for k, (tx, cx) in enumerate(cols):
            right = cols[k + 1][0] - 4 if k + 1 < len(cols) else 9999
            col = [l for l in raw if tx - 6 <= l.x0 < right]
            entries: List[Dict] = []
            for l in sorted(col, key=lambda l: (round(l.y0 / 3), l.x0)):
                is_def = l.x0 >= cx - 4
                txt = P.repair_line(l, teng, tind, indic_ok=True) if not is_def else \
                    P.repair_line(l, teng, tind, indic_ok=False)
                if not txt:
                    continue
                if not is_def:
                    starts = abs(l.x0 - tx) < 7 and re.match(r"[A-ZĀ(]", txt) and not txt.startswith("(")
                    if starts or not entries:
                        entries.append({"y": l.y0, "term": txt, "def": ""})
                    else:
                        entries[-1]["term"] += " " + txt
                else:
                    if txt.startswith(":"):
                        # attach to the term on the same line, else the latest
                        tgt = min(entries, key=lambda e: abs(e["y"] - l.y0)) if entries else None
                        if tgt is None or abs(tgt["y"] - l.y0) > 8:
                            tgt = entries[-1] if entries else None
                        if tgt is None:
                            continue
                        tgt["def"] = (tgt["def"] + "; " if tgt["def"] else "") + txt.lstrip(": ").strip()
                        last_def = tgt
                    elif entries:
                        tgt = locals().get("last_def") or entries[-1]
                        d = tgt["def"]
                        tgt["def"] = (d[:-1] + txt) if d.endswith("-") and txt[:1].islower() else (d + " " + txt)
            for e in entries:
                blocks.append(Block("gloss", e["term"].strip(), marker=e["def"].strip(), page=i, y=e["y"]))
        out[i] = Page(i, blocks, [])
    return out


def render_glossary(ch, pages, DOC_HEAD, DOC_TAIL) -> str:
    from build import esc, indic_spans, typo, ornament_rule
    out = [f'<section class="chapter glossary" epub:type="glossary" id="{ch.sec.key}">', ornament_rule(),
           '<p class="ch-label">Glossary</p>',
           '<h1 class="ch-title">Technical Terms employed in the Guru Granth</h1>',
           '<p class="fleuron">❦</p>']
    letter = None
    for pg, b in ch.blocks:
        if b.kind == "pb":
            out.append(f'<span class="pb" epub:type="pagebreak" id="pg-{esc(b.text)}" title="{esc(b.text)}" role="doc-pagebreak"></span>')
            continue
        if b.kind != "gloss":
            continue
        term = b.text
        if term.isupper() and len(term.split()) >= 3:
            continue                      # the page title, read as an entry
        # keep a parenthesis only when it holds real Gurmukhi/Devanagari
        term = re.sub(r"\(([^()]*)\)?", lambda mm: mm.group(0) if re.search(r"[\u0900-\u0A7F]", mm.group(1)) else "", term)
        term = re.sub(r"(?<=[a-z])V(?=[a-z])|^AV(?=[a-z])", lambda mm: mm.group(0).lower() if mm.group(0) == "V" else "Av", term)
        m = re.match(r"^([^(]+?)\s*(\(.*)?$", term)
        name, extra = (m.group(1), m.group(2) or "") if m else (term, "")
        first = name[:1].upper().replace("Ā", "A")
        if first.isalpha() and first != letter:
            letter = first
            out.append(f'<p class="gl-letter">{esc(letter)}</p>')
        out.append(f'<p class="gl"><b>{esc(name)}</b> {indic_spans(extra)}'
                   f'{" <span class=\"gl-sep\">—</span> " + typo(indic_spans(b.marker)) if b.marker else ""}</p>')
    out.append("</section>")
    return DOC_HEAD.format(title="Glossary", bodyattr="") + "\n".join(out) + "\n" + DOC_TAIL
