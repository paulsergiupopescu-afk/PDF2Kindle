"""OCR fallback for image-only (scanned) PDF pages, via Tesseract.

We render the page to a bitmap at a chosen DPI, run Tesseract with positional
output (``image_to_data``), and rebuild the same Line/Span model the born-digital
path produces — so the rest of the pipeline is agnostic about where text came
from. Font "size" is approximated from the OCR word box height, which is enough
for paragraph and heading heuristics downstream.
"""

from __future__ import annotations

import logging
from typing import List, Optional

import pymupdf

from .model import Line, Page, Span

log = logging.getLogger("pdf2kindle.ocr")

_AVAILABLE: Optional[bool] = None


def is_available() -> bool:
    """True if pytesseract and the tesseract binary are usable."""
    global _AVAILABLE
    if _AVAILABLE is not None:
        return _AVAILABLE
    try:
        import pytesseract  # noqa: F401

        pytesseract.get_tesseract_version()
        _AVAILABLE = True
    except Exception as exc:  # pragma: no cover - environment dependent
        log.debug("Tesseract unavailable: %s", exc)
        _AVAILABLE = False
    return _AVAILABLE


def ocr_page(page: "pymupdf.Page", *, number: int, lang: str = "eng", dpi: int = 300) -> Optional[Page]:
    if not is_available():
        return None
    import pytesseract
    from PIL import Image
    import io

    scale = dpi / 72.0
    pix = page.get_pixmap(matrix=pymupdf.Matrix(scale, scale), alpha=False)
    img = Image.open(io.BytesIO(pix.tobytes("png")))

    data = pytesseract.image_to_data(img, lang=lang, output_type=pytesseract.Output.DICT)
    out = Page(number=number, width=float(page.rect.width), height=float(page.rect.height), ocr=True)

    n = len(data["text"])
    # Group words into lines keyed by (block, par, line).
    lines: dict = {}
    for i in range(n):
        text = data["text"][i]
        if not text or not text.strip():
            continue
        try:
            conf = float(data["conf"][i])
        except (ValueError, TypeError):
            conf = -1.0
        if conf >= 0 and conf < 40:  # drop very low-confidence noise
            continue
        key = (data["block_num"][i], data["par_num"][i], data["line_num"][i])
        # Convert bitmap coords back to PDF points.
        x = data["left"][i] / scale
        y = data["top"][i] / scale
        w = data["width"][i] / scale
        h = data["height"][i] / scale
        lines.setdefault(key, []).append((text, x, y, w, h))

    for key in sorted(lines.keys()):
        words = lines[key]
        words.sort(key=lambda t: t[1])  # left to right
        x0 = min(w[1] for w in words)
        y0 = min(w[2] for w in words)
        x1 = max(w[1] + w[3] for w in words)
        y1 = max(w[2] + w[4] for w in words)
        size = round(sorted(w[4] for w in words)[len(words) // 2], 1)  # median glyph height
        text = " ".join(w[0] for w in words)
        span = Span(
            text=text,
            font="OCR",
            size=size,
            flags=0,
            color=0,
            bbox=(x0, y0, x1, y1),
            origin=(x0, y1),
        )
        out.lines.append(Line(spans=[span], bbox=(x0, y0, x1, y1)))

    return out
