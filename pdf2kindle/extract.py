"""Extract a PDF into the typed page/line/span model using PyMuPDF.

This stage is deliberately dumb: it faithfully records geometry and styling and
decides, per page, whether the page is "born-digital" (has a real text layer) or
image-only (a scan) and therefore needs OCR. All interpretation happens later.
"""

from __future__ import annotations

import logging
from typing import List, Optional

import pymupdf

from .model import ImageBlock, Line, Page, Span
from . import ocr as ocr_mod

log = logging.getLogger("pdf2kindle.extract")

# A page with fewer real text characters than this is treated as image-only.
_MIN_TEXT_CHARS = 12


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

    ocr_available = ocr_mod.is_available() if ocr_mode != "never" else False
    if ocr_mode == "force" and not ocr_available:
        log.warning("OCR forced but Tesseract is unavailable; falling back to text layer.")

    pages: List[Page] = []
    for i in range(doc.page_count):
        page = doc[i]
        p = _extract_text_page(page, i)

        needs_ocr = False
        if ocr_mode == "force":
            needs_ocr = ocr_available
        elif ocr_mode == "auto" and ocr_available:
            if _char_count(p) < _MIN_TEXT_CHARS and _image_coverage(p) > 0.5:
                needs_ocr = True

        if needs_ocr:
            log.info("OCR page %d/%d", i + 1, doc.page_count)
            ocr_page = ocr_mod.ocr_page(page, number=i, lang=ocr_lang, dpi=dpi)
            if ocr_page is not None and _char_count(ocr_page) > 0:
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
