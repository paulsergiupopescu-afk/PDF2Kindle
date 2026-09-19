"""Generate a plain cover for an article.

A paper has no cover art, and rasterizing its first page gives a library a
thumbnail of dense two-column type with a publisher's banner across the top --
unreadable at the size a device actually shows it. A generated cover carrying
nothing but the title and the author reads at a glance, which is the whole job
of a cover in a book list.
"""
from __future__ import annotations

import logging
from typing import List, Optional

import pymupdf

from .model import ImageBlock

log = logging.getLogger(__name__)

# Kindle wants a tall cover; this is the usual 1:1.6 at a comfortable size.
WIDTH, HEIGHT = 1000, 1600
MARGIN = 110

_INK = (0.10, 0.11, 0.13)
_MUTED = (0.38, 0.40, 0.44)
_RULE = (0.72, 0.74, 0.78)
_PAPER = (0.99, 0.985, 0.97)

_TITLE_FONT = "tiro"  # Times roman: a serif, for the title
_META_FONT = "helv"

_TITLE_MAX = 68
_TITLE_MIN = 34
_AUTHOR_SIZE = 34


def _wrap(text: str, font: str, size: float, width: float) -> List[str]:
    """Greedy word wrap at the width the glyphs actually measure."""
    lines: List[str] = []
    current = ""
    for word in text.split():
        trial = f"{current} {word}".strip()
        if current and pymupdf.get_text_length(trial, fontname=font, fontsize=size) > width:
            lines.append(current)
            current = word
        else:
            current = trial
    if current:
        lines.append(current)
    return lines


def _fit_title(title: str, width: float, max_lines: int = 7) -> tuple:
    """Largest size at which the title fits in a few lines."""
    size = _TITLE_MAX
    while size > _TITLE_MIN:
        lines = _wrap(title, _TITLE_FONT, size, width)
        if len(lines) <= max_lines:
            return size, lines
        size -= 2
    return _TITLE_MIN, _wrap(title, _TITLE_FONT, _TITLE_MIN, width)


def render_article_cover(title: str, author: str = "") -> Optional[ImageBlock]:
    """A plain cover: the title, the author, and nothing else."""
    title = (title or "Untitled").strip()
    author = (author or "").strip()
    try:
        doc = pymupdf.open()
        page = doc.new_page(width=WIDTH, height=HEIGHT)
        page.draw_rect(page.rect, color=None, fill=_PAPER)

        inner = WIDTH - 2 * MARGIN
        size, lines = _fit_title(title, inner)
        leading = size * 1.22

        # Centre the whole block -- title and byline together, not the title
        # alone -- then lift it slightly, since a block set on the exact
        # arithmetic centre always reads as low.
        author_lines = _wrap(author, _META_FONT, _AUTHOR_SIZE, inner) if author else []
        block_h = len(lines) * leading
        if author_lines:
            block_h += leading * 0.97 + len(author_lines) * _AUTHOR_SIZE * 1.3
        y = max(MARGIN + size, (HEIGHT - block_h) / 2 - HEIGHT * 0.05)

        for line in lines:
            page.insert_text((MARGIN, y), line, fontsize=size,
                             fontname=_TITLE_FONT, color=_INK)
            y += leading

        if author_lines:
            y += leading * 0.35
            page.draw_line(pymupdf.Point(MARGIN, y), pymupdf.Point(MARGIN + inner * 0.22, y),
                           color=_RULE, width=1.6)
            y += leading * 0.62
            for line in author_lines:
                page.insert_text((MARGIN, y), line, fontsize=_AUTHOR_SIZE,
                                 fontname=_META_FONT, color=_MUTED)
                y += _AUTHOR_SIZE * 1.3

        pix = page.get_pixmap(alpha=False)
        for fmt, ext in (("jpeg", "jpg"), ("png", "png")):
            try:
                data = pix.tobytes(fmt)
            except Exception:
                continue
            doc.close()
            return ImageBlock(data=data, ext=ext,
                              bbox=(0.0, 0.0, float(WIDTH), float(HEIGHT)),
                              width=pix.width, height=pix.height)
        doc.close()
        return None
    except Exception as exc:  # pragma: no cover - defensive
        log.debug("article cover render failed: %s", exc)
        return None
