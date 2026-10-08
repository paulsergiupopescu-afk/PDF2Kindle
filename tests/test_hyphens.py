"""Mid-line stranded hyphens and hyphens typed for dashes (pdf2kindle/hyphens.py)."""

from pdf2kindle.hyphens import repair_hyphens
from pdf2kindle.model import Chapter, Document, Element, ElementKind, InlineRun


def _doc(*paragraphs, language="en"):
    """A one-chapter document; each paragraph is a string or a list of runs."""
    elements = []
    for p in paragraphs:
        runs = [InlineRun(text=p)] if isinstance(p, str) else p
        elements.append(Element(kind=ElementKind.PARAGRAPH, runs=runs))
    return Document(chapters=[Chapter(title="One", elements=elements)], language=language)


def _texts(doc):
    return [el.text for el in doc.chapters[0].elements]


def test_a_word_broken_into_syllables_mid_line_is_rejoined():
    doc = _doc("who by his Death and Res- urrection has overcome the world",
               "it is pre- cisely here, in the face of economic pros- pects")
    repair_hyphens(doc)
    assert _texts(doc) == [
        "who by his Death and Resurrection has overcome the world",
        "it is precisely here, in the face of economic prospects",
    ]


def test_the_books_own_spelling_decides_between_joining_and_hyphenating():
    doc = _doc("in present- day culture and the health- care profession",
               "present-day thought; the health-care system",
               "after the Resurrection",
               "the Res- urrection")
    repair_hyphens(doc)
    assert _texts(doc)[0] == "in present-day culture and the health-care profession"
    assert _texts(doc)[3] == "the Resurrection"


def test_an_inflection_found_elsewhere_counts_as_evidence():
    doc = _doc("buying or selling or ex- changing them", "an exchange of goods")
    repair_hyphens(doc)
    assert _texts(doc)[0] == "buying or selling or exchanging them"


def test_a_split_compound_keeps_its_hyphen():
    doc = _doc("a sort of self- portrait of Christ by Pseudo- Dionysius",
               "the pro- abortion culture", "procured abortion")
    repair_hyphens(doc)
    assert _texts(doc)[:2] == ["a sort of self-portrait of Christ by Pseudo-Dionysius",
                               "the pro-abortion culture"]


def test_a_stranded_hyphen_before_a_function_word_is_a_dash():
    doc = _doc("collaboration with others- can be necessary")
    repair_hyphens(doc)
    assert _texts(doc) == ["collaboration with others—can be necessary"]


def test_a_suspended_hyphen_is_left_hanging():
    doc = _doc("in pre- and post-war Europe")
    repair_hyphens(doc)
    assert _texts(doc) == ["in pre- and post-war Europe"]


def test_a_spaced_hyphen_between_words_is_a_dash_but_a_range_is_not():
    doc = _doc("the deposit of faith - Sacred Scripture - entrusted", "see pp. 3 - 5")
    repair_hyphens(doc)
    assert _texts(doc) == ["the deposit of faith — Sacred Scripture — entrusted",
                           "see pp. 3 - 5"]


def test_a_bare_hyphen_into_a_function_word_is_a_dash():
    doc = _doc("Capacity for work-that is to say, life-the gift of God-can be welcomed")
    repair_hyphens(doc)
    assert _texts(doc) == ["Capacity for work—that is to say, life—the gift of God—can be welcomed"]


def test_real_compounds_ending_in_a_small_word_are_kept():
    text = ("an unheard-of number, a built-in flaw, an up-to-date list, "
            "a well-known so-called self-evident non-existent add-on")
    doc = _doc(text)
    repair_hyphens(doc)
    assert _texts(doc) == [text]


def test_a_dash_at_an_italic_boundary_is_converted():
    doc = _doc([InlineRun(text="called a "), InlineRun(text="family wage-", italic=True),
                InlineRun(text="that is, a single salary")],
               [InlineRun(text="Capacity for work", italic=True), InlineRun(text="-that is to say")])
    repair_hyphens(doc)
    assert _texts(doc) == ["called a family wage—that is, a single salary",
                           "Capacity for work—that is to say"]


def test_dash_guessing_is_english_only():
    doc = _doc("cu alții- care au", "omul-cel nou", language="ro")
    repair_hyphens(doc)
    assert _texts(doc)[1] == "omul-cel nou"
    assert "—" not in _texts(doc)[0]
