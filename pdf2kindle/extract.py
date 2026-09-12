"""Extract a PDF into the typed page/line/span model using PyMuPDF.

This stage is deliberately dumb: it faithfully records geometry and styling and
decides, per page, whether the page is "born-digital" (has a real text layer) or
image-only (a scan) and therefore needs OCR. All interpretation happens later.
"""

from __future__ import annotations

import difflib
import logging
import re
import unicodedata
from dataclasses import replace
from typing import Dict, List, Optional

import pymupdf

from .model import ImageBlock, Line, Page, Span
from . import ocr as ocr_mod
from . import spelling

log = logging.getLogger("pdf2kindle.extract")

# A page with fewer real text characters than this is treated as image-only.
_MIN_TEXT_CHARS = 12
# OCR yielding less than this is discarded as cover art / decoration.
_MIN_OCR_CHARS = 25
# A line whose characters are more than this fraction raw control codes is
# not text at all -- see _is_garbled().
_GARBLE_THRESHOLD = 0.04
# A page whose largest image covers more than this fraction of the page area
# is a photograph of the whole page -- see _is_scanned_page().
_SCAN_IMAGE_RATIO = 0.85
# A line scoring below this fraction of real dictionary words is enough to
# buy the page an OCR second opinion -- see _repair_page(). Set high on
# purpose: a badly mangled line is usually *mostly* right ("teologia şi
# trăirea (experienţa) Biseridi şi c împărtăşeşte" scores 0.875 with two
# words destroyed), so a strict threshold here silently skips exactly the
# pages that need the work. Being admitted costs only the OCR pass; whether
# any line is actually rewritten is decided per line, further down.
_SUSPICIOUS_SCORE = 0.95
# A line with this fraction of non-Latin letters is a Greek or Slavonic
# quotation: outside what a Latin-script dictionary or OCR model can judge.
_NON_LATIN_LIMIT = 0.2
# The runs of letters in a token: "(Bisericii," has one, "F.diţiile" and
# "rugându-se" have two. The longest is the word being judged -- taking the
# first would judge "F.diţiile" on its "F".
_WORD_CORE = re.compile(r"[^\W\d_]+", re.UNICODE)
# Punctuation that legitimately hugs a word. Anything else sitting against
# one in a misread token (".iradcmice") is OCR debris, not punctuation, and
# is dropped rather than carried over onto the corrected word.
_LEGIT_LEAD = set("„“”\"'‘’«»([{¿¡—–-")
# A trailing hyphen (hard or soft) matters most of all: it is what tells the
# de-hyphenation pass this word continues on the next line.
_LEGIT_TAIL = set(".,;:!?)]}\"'“”‘’«»…—–-\xad")
# Shortest replacement word accepted, unless the misread token has no
# letters at all. Two-letter "words" are where a dictionary throws false
# positives, and swapping on one would corrupt text that was merely ugly.
_MIN_SWAP_LEN = 3


def _is_garbled(text: str) -> bool:
    """Detect text produced by a broken font encoding, not real prose.

    Some PDFs (library/"downloaded from" copies especially) embed a footer or
    watermark in a subsetted font whose ToUnicode CMap is missing or wrong.
    PyMuPDF still extracts *something* for it, but the codepoints are raw
    control characters rather than the glyphs actually drawn -- unmistakable
    from ordinary text, which a professionally typeset PDF never contains.
    Filtering this out at the source keeps it from polluting body text,
    heading detection, and the running-head/margin statistics that later
    stages compute over every line on every page.
    """
    if not text:
        return False
    bad = sum(1 for c in text if unicodedata.category(c) == "Cc" and c not in "\t\n\r")
    return bad / len(text) > _GARBLE_THRESHOLD


def _line_from_dict(ld: dict) -> Optional[Line]:
    spans: List[Span] = []
    for sd in ld.get("spans", []):
        text = sd.get("text", "")
        if text == "":
            continue
        spans.append(
            Span(
                text=text,
                font=sd.get("font", ""),
                size=round(float(sd.get("size", 0.0)), 1),
                flags=int(sd.get("flags", 0)),
                color=int(sd.get("color", 0)),
                bbox=tuple(sd.get("bbox", (0, 0, 0, 0))),  # type: ignore[arg-type]
                origin=tuple(sd.get("origin", (0, 0))),  # type: ignore[arg-type]
            )
        )
    if not spans:
        return None
    text = "".join(s.text for s in spans)
    if _is_garbled(text):
        return None
    return Line(spans=spans, bbox=tuple(ld.get("bbox", (0, 0, 0, 0))))  # type: ignore[arg-type]


def _extract_text_page(page: "pymupdf.Page", number: int) -> Page:
    d = page.get_text("dict")
    out = Page(number=number, width=float(d.get("width", page.rect.width)),
               height=float(d.get("height", page.rect.height)))
    for block in d.get("blocks", []):
        if block.get("type") == 1:  # image block
            img = block.get("image")
            if img:
                out.images.append(
                    ImageBlock(
                        data=img,
                        ext=block.get("ext", "png"),
                        bbox=tuple(block.get("bbox", (0, 0, 0, 0))),  # type: ignore[arg-type]
                        width=int(block.get("width", 0)),
                        height=int(block.get("height", 0)),
                    )
                )
            continue
        block_lines: List[Line] = []
        for ld in block.get("lines", []):
            line = _line_from_dict(ld)
            if line is not None:
                block_lines.append(line)
        out.lines.extend(_merge_same_row_lines(block_lines))
    return out


def _merge_same_row_lines(lines: List[Line]) -> List[Line]:
    """Recombine fragments PyMuPDF split off from one visual line.

    Extreme word-spacing -- a short line stretched to fill a fully-justified
    paragraph's last line, common in Word output -- can push gaps between
    words wide enough that PyMuPDF's line clustering reports each run of
    words as its own "line", all sharing the identical y-position. Left
    alone, each fragment becomes its own paragraph, breaking a normal
    sentence into "word1" / "word2" / "word3" one-word paragraphs.
    """
    if len(lines) < 2:
        return lines
    groups: List[List[Line]] = []
    for ln in lines:
        if groups and abs(groups[-1][0].y0 - ln.y0) <= 1.5:
            groups[-1].append(ln)
        else:
            groups.append([ln])

    merged: List[Line] = []
    for group in groups:
        if len(group) == 1:
            merged.append(group[0])
            continue
        group.sort(key=lambda ln: ln.x0)
        spans: List[Span] = []
        for i, ln in enumerate(group):
            frag_spans = list(ln.spans)
            if i > 0 and frag_spans and spans:
                if not spans[-1].text.endswith((" ", "\t")) and not frag_spans[0].text.startswith((" ", "\t")):
                    spans[-1] = replace(spans[-1], text=spans[-1].text + " ")
            spans.extend(frag_spans)
        x0 = min(ln.x0 for ln in group)
        y0 = min(ln.y0 for ln in group)
        x1 = max(ln.x1 for ln in group)
        y1 = max(ln.y1 for ln in group)
        merged.append(Line(spans=spans, bbox=(x0, y0, x1, y1)))
    return merged


def _char_count(page: Page) -> int:
    return sum(len(s.text.strip()) for line in page.lines for s in line.spans)


def _image_coverage(page: Page) -> float:
    """Fraction of page area covered by the largest image block."""
    if not page.images or page.width <= 0 or page.height <= 0:
        return 0.0
    page_area = page.width * page.height
    best = 0.0
    for im in page.images:
        x0, y0, x1, y1 = im.bbox
        best = max(best, abs((x1 - x0) * (y1 - y0)))
    return best / page_area if page_area else 0.0


def _is_scanned_page(page: Page) -> bool:
    """Is this page really a photograph of the whole printed page?

    A scan-and-OCR pipeline (ABBYY FineReader and similar) embeds a
    full-page raster of the original photo *and* a text layer of whatever
    it recognized, so the page looks born-digital to a text extractor even
    though every word on it came from that OCR pass -- of unknown, often
    mediocre, quality (misread letters, not just missing structure -- see
    ocr.py's module docs for the geometric case; this is the same failure
    at the character level). A real digitally-typeset page essentially
    never has a background image covering nearly the whole page, so this
    signature is unambiguous even when the embedded text layer looks
    plausible enough to pass _is_garbled().
    """
    return _image_coverage(page) > _SCAN_IMAGE_RATIO


def _is_other_script(text: str) -> bool:
    """Is this line mostly Greek/Cyrillic/etc. rather than Latin script?

    This book's kind of scholarship quotes its sources in the original, and
    such a line is untouchable here twice over: a Latin-script dictionary
    scores it as nonsense however perfectly it was recognized, and a
    Latin-script OCR model asked to re-read it would return nonsense in
    fact. Left alone on both counts.
    """
    letters = [c for c in text if c.isalpha()]
    if len(letters) < 4:
        return False
    non_latin = sum(1 for c in letters if not ("A" <= c <= "Z" or "a" <= c <= "z"
                                               or unicodedata.name(c, "").startswith("LATIN")))
    return non_latin / len(letters) > _NON_LATIN_LIMIT


def _has_suspicious_line(page: Page, lex: "spelling.Lexicon") -> bool:
    """Is any line on this page poor enough Romanian/etc. to be worth re-reading?

    Purely an optimization: rendering and OCR-ing a page costs seconds, so a
    page whose existing text already reads as real words throughout is left
    alone without ever paying for it.
    """
    for line in page.lines:
        if _is_other_script(line.text):
            continue
        score = lex.score(line.text)
        if score is not None and score < _SUSPICIOUS_SCORE:
            return True
    return False


def _core(token: str) -> Optional[str]:
    """The longest run of letters in *token*, which is the word it holds."""
    runs = _WORD_CORE.findall(token)
    return max(runs, key=len) if runs else None


def _choose_token(original: str, candidate: str, lex: "spelling.Lexicon") -> Optional[str]:
    """Pick the better reading of one word, or None to keep *original*.

    Deliberately one-directional: the existing text is replaced only when it
    is *not* a word of the language and the fresh reading *is*. So a real
    word is never "corrected" (a proper name absent from the dictionary
    stays), a misreading is only swapped for something demonstrably real,
    and where both readings are nonsense nothing happens -- which is the
    right outcome for a Greek term or a name neither pass could manage.

    Real punctuation hugging the original word is kept, so "(Biseridi,"
    becomes "(Bisericii," rather than losing the bracket -- but debris the
    misreading picked up (".iradcmice") is not mistaken for punctuation and
    carried over.
    """
    if original == candidate:
        return None
    om, cm = _core(original), _core(candidate)
    if cm is None or not lex.is_known(cm):
        return None  # the new reading is not a word: nothing to gain
    if om is not None:
        if lex.is_known(om):
            return None  # what we already have is a word: leave it alone
        # Neither a one- or two-letter misreading nor a replacement that
        # short can be judged: "a" and "III" are both plausible, and a
        # dictionary's shortest entries are where its false positives live.
        if min(len(om), len(cm)) < _MIN_SWAP_LEN:
            return None
        # A digit fused to a word is usually a footnote marker OCR flattened
        # into the text ("1Adică din skevofylakion"), and notes are paired by
        # exactly those digits -- so a reading that dropped them is not an
        # improvement, whatever it did for the word. Only where the existing
        # token holds a word at all: a digit in a token with no letters in it
        # (".1" for "şi") is misread ink, not a marker.
        if any(c.isdigit() for c in original) and not any(c.isdigit() for c in candidate):
            return None
    result = _trim_debris(candidate)
    # Don't let the fresh reading open the word with punctuation the old one
    # never had: OCR readily sees a quote mark in a smudge, and inventing one
    # mid-sentence is more conspicuous than the misspelling being fixed.
    if original[:1].isalnum():
        result = result.lstrip("".join(_LEGIT_LEAD))
    # A hyphen at the end of the line is not decoration -- it is what marks
    # this word as continuing on the next one, so restore it if the fresh
    # reading failed to see it.
    if original[-1:] in ("-", "\xad") and result[-1:] not in ("-", "\xad"):
        result += original[-1]
    return result if result and result != original else None


def _trim_debris(token: str) -> str:
    """Drop leading/trailing characters that are neither letters nor punctuation.

    A misreading often picks up stray marks at a word's edges (".iradcmice"),
    and carrying them onto the corrected word would leave the repair looking
    half-done. Only the outer edges are touched, and only characters that no
    language puts there.
    """
    start, end = 0, len(token)
    while start < end and not token[start].isalnum() and token[start] not in _LEGIT_LEAD:
        start += 1
    while end > start and not token[end - 1].isalnum() and token[end - 1] not in _LEGIT_TAIL:
        end -= 1
    return token[start:end]


def _assign_words_to_lines(words: List[dict], lines: List[Line]) -> Dict[int, List[dict]]:
    """Group OCR word boxes by which existing line each one belongs to.

    A word goes to the line it overlaps vertically, and among several such
    lines (a two-column page has two at every height) to the one it is
    horizontally nearest -- so a word never jumps the gutter into the other
    column's line, while a word running past its own line's right edge still
    lands where it belongs.
    """
    out: Dict[int, List[dict]] = {}
    for w in words:
        cy, cx = (w["y0"] + w["y1"]) / 2, (w["x0"] + w["x1"]) / 2
        best, best_key = None, None
        for idx, line in enumerate(lines):
            tol = max(line.height * 0.25, 1.0)
            if not (line.y0 - tol <= cy <= line.y1 + tol):
                continue
            x_gap = 0.0 if line.x0 <= cx <= line.x1 else min(abs(cx - line.x0), abs(cx - line.x1))
            overlap = min(w["y1"], line.y1) - max(w["y0"], line.y0)
            key = (x_gap, -overlap)
            if best_key is None or key < best_key:
                best, best_key = idx, key
        if best is not None:
            out.setdefault(best, []).append(w)
    return out


def _repair_page(page: Page, pdf_page: "pymupdf.Page", lex: "spelling.Lexicon", *,
                 lang: str, dpi: int) -> int:
    """Re-read badly-recognized lines of a scanned page; return how many changed.

    A scanned page's "text layer" is some earlier OCR pass (ABBYY FineReader
    and similar), and where it misread the scan it leaves real prose as
    nonsense -- "descoperă" as "«It scoperă", "academice" as ".iradcmice".
    Replacing the whole page with a fresh OCR pass fixes the words but costs
    far more than it gains: our OCR reports one uniform size per line and no
    bold/italic/superscript flags, so heading levels, footnote markers and
    running heads stop being detectable anywhere on that page -- and on a
    title page it can even swap in the wrong title (structure.py's
    _guess_title compares font sizes across candidates, which only works if
    they were all measured the same way).

    So each line is arbitrated word by word, and only its *text* is ever
    replaced: the line keeps its bbox, size, and style flags, which is
    everything the later stages actually measure. A line is rewritten only
    when a fresh OCR of it reads as clearly better words in the book's own
    language than what the PDF already claimed (see spelling.py) -- so a
    line the existing layer got right, or one neither pass can read (a Greek
    or Slavonic quotation, in a Romanian dictionary's view), is left exactly
    as it was.
    """
    if not lex.available or not _has_suspicious_line(page, lex):
        return 0
    # Keep even the words Tesseract is unsure of: dropping them would make a
    # candidate look *better* by deleting the hard words, and a repair that
    # silently loses text is worse than the misreading it replaces.
    words = ocr_mod.ocr_words(pdf_page, lang=lang, dpi=dpi, min_conf=0)
    if not words:
        return 0

    grouped = _assign_words_to_lines(words, page.lines)
    repaired = 0
    for idx, line in enumerate(page.lines):
        if _is_other_script(line.text):
            continue
        cand_tokens = [w["text"] for w in sorted(grouped.get(idx, []), key=lambda w: w["x0"])]
        if not cand_tokens:
            continue
        for old, new in _token_repairs(line.text.split(), cand_tokens, lex):
            # Substitute inside the span that holds the words, so the line
            # keeps every span boundary, size and style flag it had. A phrase
            # straddling two spans simply isn't replaced.
            for si, span in enumerate(line.spans):
                if old in span.text:
                    line.spans[si] = replace(span, text=span.text.replace(old, new, 1))
                    repaired += 1
                    break
    return repaired


def _token_repairs(orig_tokens: List[str], cand_tokens: List[str],
                   lex: "spelling.Lexicon") -> List[tuple]:
    """Pair the two readings up and return the (old, new) swaps worth making.

    The readings rarely agree on word count -- exactly where the old one is
    most mangled, it has run a word together or split one in two ("descoperă"
    survives as "«It scoperă"). The words both passes agree on anchor an
    alignment of the rest, so each disagreement is compared as a unit: a run
    of misread tokens is replaced by the run the fresh pass read there, but
    only when *every* token going out fails to be a word and *every* token
    coming in is one. Runs the fresh pass merely dropped or added are refused
    outright, so a repair can neither lose nor invent text.
    """
    repairs: List[tuple] = []
    matcher = difflib.SequenceMatcher(a=orig_tokens, b=cand_tokens, autojunk=False)
    for op, i1, i2, j1, j2 in matcher.get_opcodes():
        if op == "equal":
            continue
        old_run, new_run = orig_tokens[i1:i2], cand_tokens[j1:j2]
        if op != "replace" or not old_run or not new_run:
            continue  # a pure deletion or insertion is never an improvement
        if len(old_run) == 1 and len(new_run) == 1:
            better = _choose_token(old_run[0], new_run[0], lex)
            if better is not None:
                repairs.append((old_run[0], better))
            continue
        if any(_holds_a_word(t, lex) for t in old_run):
            continue  # part of what we have is real: too risky to rewrite
        if not all(_holds_a_word(t, lex) for t in new_run):
            continue  # the replacement is not made of words
        old_text = " ".join(old_run)
        new_text = " ".join(_trim_debris(t) for t in new_run)
        # A footnote marker stands alone as its own token ("1 Ediţiile mai
        # noi..."), and notes are paired by exactly those digits, so a
        # replacement that has lost one is refused. Digits *inside* a token
        # that holds no word are misread ink, not a marker (".it«1," for
        # "cată,"), and must not block the repair.
        if (any(_is_bare_number(t) for t in old_run)
                and not any(c.isdigit() for c in new_text)):
            continue
        if old_text.endswith(("-", "\xad")) and not new_text.endswith(("-", "\xad")):
            new_text += old_text[-1]
        if new_text and new_text != old_text:
            repairs.append((old_text, new_text))
    return repairs


def _is_bare_number(token: str) -> bool:
    """Is this token nothing but a number -- the shape of a footnote marker?"""
    return token.strip("().,;:[]").isdigit()


def _holds_a_word(token: str, lex: "spelling.Lexicon") -> bool:
    """Does this token contain a real word of the language?"""
    core = _core(token)
    return core is not None and len(core) >= _MIN_SWAP_LEN and lex.is_known(core)


def _column_count(lines: List[Line], tol: float = 5.0) -> int:
    """Count distinct left-edge x-positions, merging ones within *tol* points.

    A tabular layout (a List of Illustrations, a Contents page) has just a
    handful of these -- one per column -- no matter how many rows it has.
    Place-name labels scattered across a map fall at dozens of distinct
    positions, since each sits wherever its city or region actually is.
    """
    xs = sorted(ln.x0 for ln in lines)
    groups = 0
    last: Optional[float] = None
    for x in xs:
        if last is None or x - last > tol:
            groups += 1
        last = x
    return groups


def _looks_like_map(lines: List[Line]) -> bool:
    """Detect a page that is really a vector map/diagram, not prose.

    A map's borders, coastlines and rivers are vector paths PyMuPDF's text
    extractor never sees at all; what it *does* see is the scatter of short
    text labels drawn on top (place names, a scale bar's "0 50 100 km", a
    legend's single letters). Each label lands as its own "paragraph" by the
    normal reading-order logic, littering the chapter with garbage lines like
    "I", "C", "50". The signature is unmistakable versus real prose: many
    lines, each only a word or two, none of them building a sentence.

    A tabular front-matter list (Contents, List of Illustrations) shares the
    short-fragment signature -- "List of maps", "xii" are just as terse as a
    map label -- so it is *not* enough on its own. What separates them is
    column structure: a table's fragments fall into a handful of x-positions
    (its columns); a map's are scattered across dozens.

    A centered title page shares it too, in a different way: Word happily
    emits a blank paragraph as a "line" containing just a run of spaces, and
    a title page is mostly blank spacer lines around a few short centered
    ones. Left uncounted, those blanks drag the word-per-line average toward
    zero and inflate the line count past the threshold on their own -- so
    they must be excluded before the shape is judged at all.
    """
    lines = [ln for ln in lines if ln.text.strip()]
    if len(lines) < 10:
        return False
    words = [len(ln.text.split()) for ln in lines]
    avg_words = sum(words) / len(lines)
    lens = sorted(len(ln.text.strip()) for ln in lines)
    median_len = lens[len(lens) // 2]
    return avg_words < 2.5 and median_len < 25 and _column_count(lines) > 10


def _rasterize_page(page: "pymupdf.Page") -> ImageBlock:
    """Render a full page to a PNG, for a map/diagram whose vector content
    (borders, rivers, roads) has no text/image representation to extract."""
    pix = page.get_pixmap(matrix=pymupdf.Matrix(2.2, 2.2), alpha=False)
    return ImageBlock(
        data=pix.tobytes("png"), ext="png",
        bbox=(0.0, 0.0, float(page.rect.width), float(page.rect.height)),
        width=pix.width, height=pix.height,
    )


def _render_cover(doc) -> Optional[dict]:
    """Rasterize page 1 so every book gets a cover, even without an embedded image."""
    if doc.page_count == 0:
        return None
    try:
        pix = doc[0].get_pixmap(matrix=pymupdf.Matrix(2.0, 2.0), alpha=False)
        for ext in ("jpeg", "png"):
            try:
                return {"data": pix.tobytes(ext), "ext": "jpg" if ext == "jpeg" else "png",
                        "width": pix.width, "height": pix.height}
            except Exception:
                continue
    except Exception as exc:  # pragma: no cover
        log.debug("cover render failed: %s", exc)
    return None


def extract(
    path: str,
    *,
    ocr_mode: str = "auto",  # "auto" | "force" | "never"
    ocr_lang: str = "eng",
    dpi: int = 300,
    repair_ocr: bool = False,
    progress=None,
) -> tuple[List[Page], dict]:
    """Return (pages, metadata) extracted from the PDF at *path*."""

    doc = pymupdf.open(path)
    meta = dict(doc.metadata or {})
    meta["_toc"] = doc.get_toc(simple=True) or []
    meta["_page_count"] = doc.page_count
    meta["_cover_render"] = _render_cover(doc)

    ocr_available = ocr_mod.is_available() if ocr_mode != "never" else False
    if ocr_mode == "force" and not ocr_available:
        log.warning("OCR forced but Tesseract is unavailable; falling back to text layer.")

    lex: Optional[spelling.Lexicon] = None
    if repair_ocr:
        if not ocr_available:
            log.warning("--repair-ocr needs Tesseract, which is unavailable; skipping repair.")
        else:
            lex = spelling.Lexicon(ocr_lang)
            if not lex.available:
                log.warning(
                    "--repair-ocr needs a hunspell dictionary for %r, which is not installed; "
                    "skipping repair.", ocr_lang,
                )
                lex = None
    repaired_lines = 0

    pages: List[Page] = []
    for i in range(doc.page_count):
        page = doc[i]
        p = _extract_text_page(page, i)

        if _looks_like_map(p.lines):
            # Vector line art has nothing for the text/image extractor to
            # find; keep the page as one picture instead of scattering its
            # labels through the surrounding chapter as bogus paragraphs.
            log.info("Rendering page %d/%d as a map/diagram image", i + 1, doc.page_count)
            p.lines = []
            p.images = [_rasterize_page(page)]
            pages.append(p)
            if progress:
                progress(i + 1, doc.page_count)
            continue

        needs_ocr = False
        if ocr_mode == "force":
            needs_ocr = ocr_available
        elif ocr_mode == "auto" and ocr_available:
            if _char_count(p) < _MIN_TEXT_CHARS and _image_coverage(p) > 0.5:
                needs_ocr = True

        if needs_ocr:
            log.info("OCR page %d/%d", i + 1, doc.page_count)
            ocr_page = ocr_mod.ocr_page(page, number=i, lang=ocr_lang, dpi=dpi)
            # A handful of characters off an image-only page is cover art or
            # decoration, not prose; OCR of display type is unreliable and the
            # fragment would land in the text as noise.
            if ocr_page is not None and _char_count(ocr_page) >= _MIN_OCR_CHARS:
                # Preserve any embedded figures that aren't the full-page scan.
                ocr_page.images = [
                    im for im in p.images
                    if (im.bbox[2] - im.bbox[0]) * (im.bbox[3] - im.bbox[1])
                    < 0.8 * p.width * p.height
                ]
                p = ocr_page
        elif lex is not None and _is_scanned_page(p):
            # A page that is a photograph of print, carrying someone else's
            # OCR as its text layer -- re-read the lines it got wrong. See
            # _repair_page for why only those lines, and only their text.
            fixed = _repair_page(p, page, lex, lang=ocr_lang, dpi=dpi)
            if fixed:
                log.info("Repaired %d line(s) on page %d/%d", fixed, i + 1, doc.page_count)
                repaired_lines += fixed

        pages.append(p)
        if progress:
            progress(i + 1, doc.page_count)

    doc.close()
    if repair_ocr:
        log.info("OCR repair rewrote %d line(s)", repaired_lines)
    meta["_repaired_lines"] = repaired_lines
    return pages, meta
