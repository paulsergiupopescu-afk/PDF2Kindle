"""Extract Hegel's text and Inwood's apparatus from the OUP Phenomenology EPUB.

The source is Michael Inwood's translation (Oxford, 2018).  This script reads
the publisher's EPUB and writes a JSON file that the builder uses:

  paras     every numbered paragraph (¶1-¶808) with its heading path, its
            XHTML body (Inwood's note anchors replaced by {{n:ID}} tokens),
            and a plain-text version for proofreading
  notes     Inwood's commentary notes, keyed by their EPUB id
  front     the translator's Introduction, Note on the Translation,
            Abbreviations and Glossary, as XHTML fragments

Nothing in Hegel's text is altered.  The only markup kept inline is <i>, <b>
and Inwood's note anchors; page-number milestones are dropped.

Usage:  python3 -I parse_source.py SOURCE.epub OUT.json
"""

from __future__ import annotations

import html
import json
import re
import sys
import zipfile

NOTE_HREF = re.compile(
    r'<a href="020_BM_endNotes\.xhtml#([^"]+)" id="r_[^"]+"><sup>(\d+)</sup></a>')
PARA = re.compile(r'<p class="(para|parafl)">(.*?)</p>', re.S)
HEAD = re.compile(r'<h([12]) class="([^"]+)"[^>]*>(.*?)</h\1>', re.S)
NOTE = re.compile(r'<div class="refnote[^"]*" id="([^"]+)">(.*?)</div>', re.S)

# Files holding Hegel's text, in reading order.
TEXT_FILES = [
    "011_FM_preface.xhtml",
    "012_chapter.xhtml",
    "013_chapter.xhtml",
    "014_chapter.xhtml",
    "015_chapter.xhtml",
    "016_chapter.xhtml",
    "017_chapter.xhtml",
    "018_chapter.xhtml",
]


def strip_tags(s: str) -> str:
    return html.unescape(re.sub(r"<[^>]+>", "", s))


def clean_inline(s: str) -> str:
    """Keep <i>, <b> and note tokens; drop milestones and stray spans."""
    s = NOTE_HREF.sub(lambda m: "{{n:%s}}" % m.group(1), s)
    s = re.sub(r'<span id="[^"]*"/>', "", s)
    s = re.sub(r'<span class="strikethrough">(.*?)</span>', r"\1", s)
    s = re.sub(r'</?span[^>]*>', "", s)
    s = re.sub(r'<a [^>]*>|</a>', "", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s


def heading_text(raw: str) -> str:
    return re.sub(r"\s+", " ", strip_tags(raw)).strip()


def parse_text(z: zipfile.ZipFile):
    paras = []
    path = {"part": "", "chapter": "", "section": ""}
    for fname in TEXT_FILES:
        src = z.read("OEBPS/" + fname).decode("utf-8")
        events = []
        for m in HEAD.finditer(src):
            events.append((m.start(), "h", m))
        for m in PARA.finditer(src):
            events.append((m.start(), "p", m))
        events.sort(key=lambda e: e[0])
        for _, kind, m in events:
            if kind == "h":
                level, cls, raw = m.group(1), m.group(2), heading_text(m.group(3))
                if cls in ("fm_title1", "chaptertitlea", "chaptertitle"):
                    path = {"part": raw, "chapter": "", "section": ""}
                elif cls == "h1":
                    path["chapter"], path["section"] = raw, ""
                else:
                    path["section"] = raw
                continue
            body = clean_inline(m.group(2))
            # The publisher's markup splits a few labels ("<b>¶</b><b>32</b>").
            body = re.sub(r"^<b>¶\s*</b>\s*<b>", "<b>¶", body)
            num = re.match(r"(?:<b>)?¶\s*(?:</?b>)*\s*(\d+)\s*(?:</b>)?\s*\.?\s*", body)
            if not num:
                # A few numbered paragraphs run on into a second block.
                if not paras:
                    raise SystemExit(f"unnumbered first paragraph in {fname}")
                prev = paras[-1]
                prev["more"].append(body)
                prev["text"] += "\n" + strip_tags(re.sub(r"\{\{n:[^}]+\}\}", "", body))
                prev["notes"] += re.findall(r"\{\{n:([^}]+)\}\}", body)
                continue
            n = int(num.group(1))
            body = body[num.end():]
            paras.append({
                "n": n,
                "part": path["part"],
                "chapter": path["chapter"],
                "section": path["section"],
                "html": body,
                "more": [],
                "text": strip_tags(re.sub(r"\{\{n:[^}]+\}\}", "", body)),
                "notes": re.findall(r"\{\{n:([^}]+)\}\}", body),
            })
    nums = [p["n"] for p in paras]
    if nums != list(range(1, len(nums) + 1)):
        raise SystemExit("paragraph numbering is not continuous")
    return paras


def parse_notes(z: zipfile.ZipFile):
    src = z.read("OEBPS/020_BM_endNotes.xhtml").decode("utf-8")
    notes = {}
    para = None
    for m in NOTE.finditer(src):
        nid, body = m.group(1), m.group(2)
        lab = re.search(r'<span class="label">(.*?)</span>', body, re.S)
        label = strip_tags(lab.group(1)) if lab else ""
        pm = re.search(r"¶(\d+)", label)
        if pm:
            para = int(pm.group(1))
        num = re.search(r"(\d+)\.\s*$", label)
        text = body[lab.end():] if lab else body
        text = re.sub(r'^\s*</a>', "", text)
        notes[nid] = {
            "para": para,
            "num": num.group(1) if num else "",
            "html": clean_inline(text),
        }
    return notes


def fragment(z: zipfile.ZipFile, name: str) -> str:
    """Return the body of a front-matter file as simplified XHTML."""
    src = z.read("OEBPS/" + name).decode("utf-8")
    body = src[src.find("<body>") + 6: src.rfind("</body>")]
    body = re.sub(r'<span id="[^"]*"/>', "", body)
    body = re.sub(r'<a href="[^"]*"[^>]*>|</a>', "", body)
    body = re.sub(r'\s(id|class)="[^"]*"', "", body)
    body = re.sub(r"</?(div|span)[^>]*>", "", body)
    body = re.sub(r"<h1>", "<h2>", body).replace("</h1>", "</h2>")
    return re.sub(r"\n\s*\n+", "\n", body).strip()


def main(src_path: str, out_path: str) -> None:
    with zipfile.ZipFile(src_path) as z:
        data = {
            "paras": parse_text(z),
            "notes": parse_notes(z),
            "front": {
                "introduction": fragment(z, "006_FM_introduction.xhtml"),
                "translation": fragment(z, "007_FM_other.xhtml"),
                "abbreviations": fragment(z, "008_FM_abbrev.xhtml"),
                "glossary": fragment(z, "019_BM_glossary.xhtml"),
                "titlepage": fragment(z, "009_FM_titlepage.xhtml"),
            },
        }
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)
    print(f"{len(data['paras'])} paragraphs, {len(data['notes'])} notes -> {out_path}")


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
