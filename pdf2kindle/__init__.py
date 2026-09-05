"""pdf2kindle — convert PDFs into clean, reflowable Kindle-ready EPUBs."""

from .convert import convert_pdf, ConvertOptions, ConvertResult

__version__ = "0.1.0"
__all__ = ["convert_pdf", "ConvertOptions", "ConvertResult", "__version__"]
