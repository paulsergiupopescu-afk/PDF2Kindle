"""Tests for --page-range/--max-pages and the optional AI cleanup pass."""
import json
from types import SimpleNamespace

import pytest

from pdf2kindle import ConvertOptions, convert_pdf
from pdf2kindle import ai_refine
from pdf2kindle.model import Chapter, Document, Element, ElementKind, InlineRun
from tests.make_bookish import main as make_bookish


@pytest.fixture(scope="module")
def pdf(tmp_path_factory):
    out = tmp_path_factory.mktemp("rng") / "b.pdf"
    make_bookish(str(out))
    return str(out)


def test_page_range_limits_pages(pdf, tmp_path):
    full = convert_pdf(pdf, str(tmp_path / "a.epub"))
    part = convert_pdf(pdf, str(tmp_path / "b.epub"), ConvertOptions(page_range=(1, 2)))
    assert part.pages == 2 < full.pages
    capped = convert_pdf(pdf, str(tmp_path / "c.epub"), ConvertOptions(max_pages=1))
    assert capped.pages == 1


def _doc(*texts):
    els = [Element(ElementKind.PARAGRAPH, runs=[InlineRun(t)]) for t in texts]
    return Document(chapters=[Chapter(title="c", elements=els)]), els


class FakeClient:
    def __init__(self, fn):
        self.fn = fn
        self.messages = SimpleNamespace(create=self._create)

    def _create(self, **kw):
        items = json.loads(kw["messages"][0]["content"])
        out = json.dumps(self.fn(items))
        return SimpleNamespace(content=[SimpleNamespace(text=out)],
                               usage=SimpleNamespace(input_tokens=100, output_tokens=50))


OLD = "The rnan walked to the tovvn and bought some bread there."


def test_ai_applies_small_fix_and_rejects_rewrite():
    doc, els = _doc(OLD, "Another perfectly ordinary paragraph of text here.")
    def fn(items):
        return [{"id": 0, "text": OLD.replace("rnan", "man").replace("tovvn", "town")},
                {"id": 1, "text": "Completely different content that the model invented."}]
    stats = ai_refine.refine_document(doc, client=FakeClient(fn))
    assert els[0].text == "The man walked to the town and bought some bread there."
    assert els[1].text.startswith("Another perfectly")
    assert (stats.changed, stats.rejected) == (1, 1)
    assert stats.cost_usd > 0


def test_ai_skips_paragraphs_with_note_markers():
    doc, els = _doc("x")
    els[0].runs = [InlineRun("Some long enough text before a note"), InlineRun("1", noteref="n1")]
    called = []
    stats = ai_refine.refine_document(doc, client=FakeClient(lambda i: called.append(i) or []))
    assert stats.eligible == 0 and not called


def test_ai_cost_cap_blocks_before_any_request():
    doc, _ = _doc(OLD * 50)
    def boom(items):
        raise AssertionError("no request should be sent")
    with pytest.raises(ai_refine.CostLimitExceeded):
        ai_refine.refine_document(doc, client=FakeClient(boom), max_cost=0.0)
    with pytest.raises(ai_refine.CostLimitExceeded):
        ai_refine.refine_document(doc, client=FakeClient(boom), confirm=lambda c: False)


def test_ai_bad_reply_keeps_original():
    doc, els = _doc(OLD)
    client = FakeClient(lambda i: i)
    client.messages = SimpleNamespace(create=lambda **kw: SimpleNamespace(
        content=[SimpleNamespace(text="not json")], usage=None))
    ai_refine.refine_document(doc, client=client)
    assert els[0].text == OLD
