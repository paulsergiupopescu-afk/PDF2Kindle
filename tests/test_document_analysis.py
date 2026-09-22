from pdf2kindle.document_analysis import PageType, classify_pages
from pdf2kindle.model import Line, Page, Span

def span(text, size=11, bold=False):
    flags = 16 if bold else 0
    return Span(text=text, font="Times-Bold" if bold else "Times", size=size,
                flags=flags, color=0, bbox=(72, 72, 72 + len(text) * 5, 84),
                origin=(72, 84))

def line(text, size=11, bold=False, y=72):
    sp = span(text, size, bold)
    return Line([sp], (72, y, 72 + len(text) * 5, y + 12))

def page(number, lines):
    return Page(number=number, width=612, height=792, lines=lines)

def test_document_analysis_detects_contents_and_chapter_opening():
    pages = [
        page(0, [line("Who are the people of Cyprus?", 24, True)]),
        page(1, [line("Contents", 18, True)] + [line("1. Introduction ........ 3") for _ in range(10)]),
        page(2, [line("1. Introduction", 18, True), line("This is ordinary prose.")]),
    ]
    stats = classify_pages(pages)
    assert stats.classifications[1].page_type == PageType.CONTENTS
    assert stats.classifications[2].page_type == PageType.CHAPTER_OPENING

def test_document_analysis_defaults_to_prose():
    pages = [page(0, [line("This is ordinary body text.")] * 12)]
    stats = classify_pages(pages)
    assert stats.classifications[0].page_type in {PageType.PROSE, PageType.COVER}


def test_document_analysis_detects_romanian_contents_at_back_of_book():
    pages = [
        page(0, [line("Liturghia ortodoxă", 24, True)]),
        page(370, [line("Bibliografie generală", 18, True), line("Author, Title")]),
        page(371, [line("CUPRINS", 18, True)] + [
            line("Introducere ................................ 5") for _ in range(8)
        ]),
    ]
    stats = classify_pages(pages)
    assert stats.classifications[2].page_type == PageType.CONTENTS
    assert stats.classifications[2].confidence >= 0.80


def test_structure_drops_classified_contents_pages():
    from pdf2kindle.document_analysis import DocumentStatistics, PageClassification, PageType
    from pdf2kindle.model import Element, ElementKind, InlineRun
    from pdf2kindle.structure import _drop_classified_print_nav

    stats = DocumentStatistics(
        body_size=11.0,
        line_height=12.0,
        body_left=72.0,
        common_sizes=[11.0],
        recurring_margin_text={},
        classifications=[
            PageClassification(10, PageType.CONTENTS, 0.95),
            PageClassification(11, PageType.PROSE, 0.90),
        ],
    )
    flat = [
        (10, Element(ElementKind.PARAGRAPH, [InlineRun(text="Printed contents")])),
        (11, Element(ElementKind.PARAGRAPH, [InlineRun(text="Real book text")])),
    ]
    kept = _drop_classified_print_nav(flat, stats)
    assert [el.text for _, el in kept] == ["Real book text"]
