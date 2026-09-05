"""Assemble the Document model into a valid EPUB3 file using ebooklib."""

from __future__ import annotations

import uuid
from typing import Dict

from ebooklib import epub

from .html import STYLESHEET, render_chapter
from .model import Document, Element, ElementKind

_IMAGE_MEDIA = {
    "jpg": "image/jpeg",
    "jpeg": "image/jpeg",
    "png": "image/png",
    "gif": "image/gif",
    "bmp": "image/bmp",
    "tiff": "image/tiff",
    "webp": "image/webp",
}


def _media_type(ext: str) -> str:
    return _IMAGE_MEDIA.get(ext.lower().lstrip("."), "image/png")


def build_epub(doc: Document, out_path: str) -> str:
    book = epub.EpubBook()
    book.set_identifier(f"urn:uuid:{uuid.uuid4()}")
    book.set_title(doc.title or "Untitled")
    book.set_language(doc.language or "en")
    if doc.author:
        book.add_author(doc.author)

    css = epub.EpubItem(
        uid="style",
        file_name="style.css",
        media_type="text/css",
        content=STYLESHEET.encode("utf-8"),
    )
    book.add_item(css)

    # Register every image up front so the renderer can resolve hrefs.
    href_map: Dict[int, str] = {}
    img_index = 0
    for chapter in doc.chapters:
        for el in chapter.elements:
            if el.kind == ElementKind.IMAGE and el.image is not None:
                ext = el.image.ext or "png"
                fname = f"images/img_{img_index}.{ext}"
                book.add_item(
                    epub.EpubImage(
                        uid=f"img_{img_index}",
                        file_name=fname,
                        media_type=_media_type(ext),
                        content=el.image.data,
                    )
                )
                href_map[id(el)] = fname
                img_index += 1

    def image_href_for(el: Element) -> str:
        return href_map.get(id(el), "")

    epub_chapters = []
    for i, chapter in enumerate(doc.chapters):
        item = epub.EpubHtml(
            title=chapter.title or f"Chapter {i + 1}",
            file_name=f"chap_{i:03d}.xhtml",
            lang=doc.language or "en",
        )
        item.content = render_chapter(chapter, image_href_for, doc.language or "en").encode("utf-8")
        book.add_item(item)
        epub_chapters.append(item)

    if doc.cover is not None:
        ext = doc.cover.ext or "jpg"
        book.set_cover(f"cover.{ext}", doc.cover.data, create_page=False)

    book.toc = tuple(epub_chapters)
    book.add_item(epub.EpubNcx())
    book.add_item(epub.EpubNav())
    book.spine = ["nav"] + epub_chapters

    epub.write_epub(out_path, book, {})
    return out_path
