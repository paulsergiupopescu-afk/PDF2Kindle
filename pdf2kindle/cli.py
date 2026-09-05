"""Command-line interface for pdf2kindle."""

from __future__ import annotations

import argparse
import logging
import os
import sys

from . import __version__
from .convert import ConvertOptions, convert_pdf


def _add_convert(sub: argparse._SubParsersAction) -> None:
    p = sub.add_parser("convert", help="Convert a PDF to a Kindle-ready EPUB")
    p.add_argument("input", help="Path to the input PDF")
    p.add_argument("-o", "--output", help="Output .epub path (default: alongside input)")
    p.add_argument("--title", default="", help="Override book title")
    p.add_argument("--author", default="", help="Override author")
    p.add_argument("--lang", default="en", help="Language code (default: en)")
    p.add_argument(
        "--ocr",
        choices=["auto", "force", "never"],
        default="auto",
        help="OCR scanned pages: auto (default), force all pages, or never",
    )
    p.add_argument("--ocr-lang", default="eng", help="Tesseract language(s), e.g. 'eng' or 'eng+fra'")
    p.add_argument("--dpi", type=int, default=300, help="Render DPI for OCR (default: 300)")
    p.add_argument("-q", "--quiet", action="store_true", help="Suppress progress output")


def _add_serve(sub: argparse._SubParsersAction) -> None:
    p = sub.add_parser("serve", help="Run the local drag-and-drop web app")
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=8000)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="pdf2kindle", description="PDF → Kindle EPUB converter")
    parser.add_argument("--version", action="version", version=f"pdf2kindle {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)
    _add_convert(sub)
    _add_serve(sub)

    args = parser.parse_args(argv)

    if args.command == "serve":
        from .server import run

        run(host=args.host, port=args.port)
        return 0

    # convert
    logging.basicConfig(
        level=logging.WARNING if args.quiet else logging.INFO,
        format="%(message)s",
    )
    input_path = args.input
    output_path = args.output or (os.path.splitext(input_path)[0] + ".epub")

    def progress(stage: str, frac: float) -> None:
        if not args.quiet:
            bar = "#" * int(frac * 30)
            sys.stderr.write(f"\r[{bar:<30}] {int(frac * 100):3d}%  {stage:<22}")
            sys.stderr.flush()
            if frac >= 1.0:
                sys.stderr.write("\n")

    opts = ConvertOptions(
        title=args.title,
        author=args.author,
        language=args.lang,
        ocr=args.ocr,
        ocr_lang=args.ocr_lang,
        dpi=args.dpi,
    )
    try:
        result = convert_pdf(input_path, output_path, opts, progress=progress)
    except FileNotFoundError:
        sys.stderr.write(f"error: input file not found: {input_path}\n")
        return 2
    except Exception as exc:  # pragma: no cover
        sys.stderr.write(f"error: conversion failed: {exc}\n")
        return 1

    print(f"\nWrote {result.output_path}")
    print(
        f"  title: {result.title or '(none)'} | author: {result.author or '(none)'}\n"
        f"  {result.pages} pages -> {result.chapters} chapters, "
        f"{result.footnotes} footnotes, {result.images} images"
        + (f", {result.ocr_pages} OCR pages" if result.ocr_pages else "")
    )
    for w in result.warnings:
        print(f"  warning: {w}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
