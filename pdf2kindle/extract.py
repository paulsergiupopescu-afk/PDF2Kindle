"""Extract a PDF into the typed page/line/span model using PyMuPDF.

This stage is deliberately dumb: it faithfully records geometry and styling and
decides, per page, whether the page is "born-digital" (has a real text layer) or
image-only (a scan) and therefore needs OCR. All interpretation happens later.
"""

from __future__ import annotations

import logging
import unicodedata
from typing import List, Optional

import pymupdf

from .model import ImageBlock, Line, Page, Span
from . import ocr as ocr_mod

log = logging.getLogger("pdf2kindle.extract")

# A page with fewer real text characters than this is treated as image-only.
_MIN_TEXT_CHARS = 12
# OCR yielding less than this is discarded as cover art / decoration.
_MIN_OCR_CHARS = 25
# A line whose characters are more than this fraction raw control codes is
# not text at all -- see _is_garbled().
_GARBLE_THRESHOLD = 0.04


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
        for ld in block.get("lines", []):
            line = _line_from_dict(ld)
            if line is not None:
                out.lines.append(line)
    return out


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
    """
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

        pages.append(p)
        if progress:
            progress(i + 1, doc.page_count)

    doc.close()
    return pages, meta
