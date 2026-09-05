"""Top-level orchestration: PDF path in, EPUB path out."""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional

from .analyze import analyze
from .epub import build_epub
from .extract import extract
from .model import ElementKind, ImageBlock
from .structure import build_document

log = logging.getLogger("pdf2kindle")


@dataclass
class ConvertOptions:
    title: str = ""
    author: str = ""
    language: str = "en"
    ocr: str = "auto"  # "auto" | "force" | "never"
    ocr_lang: str = "eng"
    dpi: int = 300
    profile: str = "academic"  # "academic" | "general"
    keep_print_nav: bool = False  # keep the printed Contents/Index chapters


@dataclass
class ConvertResult:
    output_path: str
    title: str
    author: str
    pages: int = 0
    chapters: int = 0
    footnotes: int = 0
    images: int = 0
    ocr_pages: int = 0
    warnings: List[str] = field(default_factory=list)


def convert_pdf(
    input_path: str,
    output_path: str,
    options: Optional[ConvertOptions] = None,
    *,
    progress: Optional[Callable[[str, float], None]] = None,
) -> ConvertResult:
    """Convert *input_path* (PDF) into a Kindle-ready EPUB at *output_path*."""
    opts = options or ConvertOptions()
    if not os.path.isfile(input_path):
        raise FileNotFoundError(input_path)

    def report(stage: str, frac: float) -> None:
        if progress:
            progress(stage, frac)

    report("Reading PDF", 0.05)
    pages, meta = extract(
        input_path,
        ocr_mode=opts.ocr,
        ocr_lang=opts.ocr_lang,
        dpi=opts.dpi,
        progress=lambda done, total: report("Extracting pages", 0.05 + 0.45 * done / max(total, 1)),
    )

    page_images: Dict[int, List[ImageBlock]] = {p.number: p.images for p in pages}
    ocr_pages = sum(1 for p in pages if p.ocr)

    report("Analyzing layout", 0.6)
    analyzed = analyze(pages)

    report("Building structure", 0.75)
    doc = build_document(
        analyzed,
        meta,
        page_images,
        title=opts.title,
        author=opts.author,
        language=opts.language,
        profile=opts.profile,
        keep_print_nav=opts.keep_print_nav,
    )

    report("Writing EPUB", 0.9)
    build_epub(doc, output_path)

    num_footnotes = sum(len(ch.footnotes) for ch in doc.chapters)
    num_images = sum(
        1 for ch in doc.chapters for el in ch.elements if el.kind == ElementKind.IMAGE
    )

    warnings: List[str] = []
    if opts.ocr != "never":
        from . import ocr as ocr_mod

        if not ocr_mod.is_available():
            warnings.append("Tesseract not found; scanned pages were not OCR'd.")

    report("Done", 1.0)
    result = ConvertResult(
        output_path=output_path,
        title=doc.title,
        author=doc.author,
        pages=len(pages),
        chapters=len(doc.chapters),
        footnotes=num_footnotes,
        images=num_images,
        ocr_pages=ocr_pages,
        warnings=warnings,
    )
    log.info(
        "Converted %s: %d pages -> %d chapters, %d footnotes, %d images (%d OCR pages)",
        os.path.basename(input_path),
        result.pages,
        result.chapters,
        result.footnotes,
        result.images,
        result.ocr_pages,
    )
    return result
