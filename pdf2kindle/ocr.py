"""OCR fallback for image-only (scanned) PDF pages, via Tesseract.

We render the page to a bitmap at a chosen DPI, run Tesseract with positional
output (``image_to_data``), and rebuild the same Line/Span model the born-digital
path produces — so the rest of the pipeline is agnostic about where text came
from. Font "size" is approximated from the OCR word box height, which is enough
for paragraph and heading heuristics downstream.
"""

from __future__ import annotations

import logging
from typing import Dict, List, Optional

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


def ocr_words(page: "pymupdf.Page", *, lang: str = "eng", dpi: int = 300,
              min_conf: float = 40.0) -> Optional[List[dict]]:
    """Run Tesseract over a rendered page and return word boxes in PDF points.

    Used by extract.py to patch individual corrupted lines of an otherwise
    good text layer, where whole-page replacement (ocr_page) would cost more
    -- the fine per-glyph size/bold/superscript geometry that heading,
    footnote and running-head detection all depend on -- than it's worth.
    """
    if not is_available():
        return None
    import pytesseract
    from PIL import Image
    import io

    scale = dpi / 72.0
    pix = page.get_pixmap(matrix=pymupdf.Matrix(scale, scale), alpha=False)
    img = Image.open(io.BytesIO(pix.tobytes("png")))
    data = pytesseract.image_to_data(img, lang=lang, output_type=pytesseract.Output.DICT)

    words: List[dict] = []
    for i in range(len(data["text"])):
        text = data["text"][i]
        if not text or not text.strip():
            continue
        try:
            conf = float(data["conf"][i])
        except (ValueError, TypeError):
            conf = -1.0
        if conf >= 0 and conf < min_conf:  # drop very low-confidence noise
            continue
        words.append({
            "text": text,
            "x0": data["left"][i] / scale,
            "y0": data["top"][i] / scale,
            "x1": (data["left"][i] + data["width"][i]) / scale,
            "y1": (data["top"][i] + data["height"][i]) / scale,
        })
    return words


def _words_from_data(data: dict, scale: float, min_conf: float) -> List[dict]:
    words: List[dict] = []
    for i in range(len(data["text"])):
        text = data["text"][i]
        if not text or not text.strip():
            continue
        try:
            conf = float(data["conf"][i])
        except (ValueError, TypeError):
            conf = -1.0
        if conf >= 0 and conf < min_conf:
            continue
        words.append({
            "text": text,
            "x0": data["left"][i] / scale,
            "y0": data["top"][i] / scale,
            "x1": (data["left"][i] + data["width"][i]) / scale,
            "y1": (data["top"][i] + data["height"][i]) / scale,
        })
    return words


def ocr_words_many(doc: "pymupdf.Document", indices: List[int], *, lang: str = "eng",
                   dpi: int = 300, min_conf: float = 0.0,
                   workers: Optional[int] = None, progress=None) -> Dict[int, List[dict]]:
    """ocr_words() for many pages at once, running Tesseract in parallel.

    Rendering stays on the calling thread (PyMuPDF objects are not thread
    safe); only the Tesseract subprocesses run concurrently, each pinned to
    one thread so they don't fight over cores. Setting PDF2KINDLE_OCR_CACHE
    to a directory caches results there, keyed by the rendered bitmap, so
    re-running a conversion while tuning later stages costs no OCR time.
    """
    if not is_available() or not indices:
        return {}
    import hashlib
    import io
    import json
    import os
    from concurrent.futures import ThreadPoolExecutor

    import pytesseract
    from PIL import Image

    os.environ.setdefault("OMP_THREAD_LIMIT", "1")
    workers = workers or max(1, os.cpu_count() or 1)
    cache_dir = os.environ.get("PDF2KINDLE_OCR_CACHE")
    if cache_dir:
        os.makedirs(cache_dir, exist_ok=True)
    scale = dpi / 72.0

    def run(png: bytes, cache_path: Optional[str]) -> List[dict]:
        img = Image.open(io.BytesIO(png))
        data = pytesseract.image_to_data(img, lang=lang, output_type=pytesseract.Output.DICT)
        if cache_path:
            with open(cache_path, "w", encoding="utf-8") as fh:
                json.dump(data, fh)
        return _words_from_data(data, scale, min_conf)

    out: Dict[int, List[dict]] = {}
    pending = {}
    done = 0
    with ThreadPoolExecutor(max_workers=workers) as pool:
        for n, idx in enumerate(indices):
            pix = doc[idx].get_pixmap(matrix=pymupdf.Matrix(scale, scale), alpha=False)
            png = pix.tobytes("png")
            cache_path = None
            if cache_dir:
                key = hashlib.sha1(png + lang.encode() + str(dpi).encode()).hexdigest()
                cache_path = os.path.join(cache_dir, key + ".json")
                if os.path.exists(cache_path):
                    with open(cache_path, encoding="utf-8") as fh:
                        out[idx] = _words_from_data(json.load(fh), scale, min_conf)
                    done += 1
                    continue
            pending[idx] = pool.submit(run, png, cache_path)
            # Bound the bitmaps held in memory to a few per worker.
            while len(pending) >= 2 * workers:
                first = next(iter(pending))
                out[first] = pending.pop(first).result()
                done += 1
                if progress:
                    progress(done, len(indices))
        for idx, fut in pending.items():
            out[idx] = fut.result()
            done += 1
            if progress:
                progress(done, len(indices))
    return out


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
