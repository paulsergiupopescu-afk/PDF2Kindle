"""Generate a journal-paper PDF with the faults a real one carries.

Built with low-level text placement rather than an HTML box, because the
faults being reproduced are all matters of *geometry*:

* a typesetter that emits no space glyphs, leaving the word boundary as a
  wider gap between characters ("Thestateisoneofseries");
* section headings set at body size and merely bold, so no size-based rule
  can see them, while "Conclusion" reads as a named division and lands at a
  different heading level from its own siblings;
* a title wrapped over two lines, the first ending in a question mark;
* an abstract set inset, which reads as a block quote;
* notes in two columns, the labels of the right column indented as far as
  the continuation lines of the left; and
* a bibliography set below body size under a heading only half a point
  above it, stacked beneath the notes so that both sections share the
  columns in horizontal bands.
"""
import sys
import pymupdf

W, H = 595.0, 842.0
LEFT, RIGHT = 56.0, 539.0
BODY, SMALL = 10.0, 8.0
GUTTER_L1, GUTTER_R0 = 285.0, 300.0  # two-column bounds, a 15pt gutter

ROMAN, BOLD, ITALIC = "tiro", "tibo", "tiit"

# Word gap that leaves the text glued. MuPDF infers a space of its own above
# roughly 1.2pt at this size, so a wider gap would quietly fix the very fault
# the fixture exists to reproduce.
_GLUE_GAP = 1.0

ABSTRACT = (
    "The state is one of a series of concepts which pose a particular kind of "
    "ontological difficulty and provoke a particular kind of controversy, for "
    "it is far from self evident that the entity to which they refer is in any "
    "obvious sense real."
)


# Enough body text that body size is unambiguous -- a real paper carries far
# more of it than of notes and references, and the dominant-size statistic
# that everything else keys off needs the same proportions to be meaningful.
FILLER = [
    "The difficulty is not merely terminological, though it is often presented "
    "as if it were. What a political community calls itself shapes the futures "
    "it can imagine for itself, and the ones it cannot.",
    "Analysis of this kind has a long history, and the positions available "
    "within it have been rehearsed often enough that their outlines are "
    "familiar even to those who reject the terms of the argument entirely.",
    "What is proposed here is neither a defence of realism nor a rejection of "
    "it, but an attempt to describe what follows from treating the state as a "
    "conceptual abstraction whose analytical value is an open question.",
    "That this remains contested is not a weakness of the account but a "
    "consequence of the kind of object under discussion, which cannot be "
    "settled by appeal to evidence in the way an empirical claim can.",
]

SECTIONS = [
    ("Ontology, political ontology and the political ontology of the state", [
        "Placing the question of the state within a wider ontological frame makes "
        "the difficulty legible in a way no single account can manage on its own.",
        "The argument proceeds in three stages, moving from the administrative "
        "record to the theoretical one and back again.",
    ]),
    ("The (ontological) status of the state", [
        "What follows is an attempt to say precisely what kind of thing the state "
        "is, and what follows for the analysis of politics if that is granted.",
    ]),
    ("The character of the state and the question of its analytical purchase", [
        "A concept earns its place by what it lets us see. The state, on this "
        "reading, earns its place many times over despite its elusiveness.",
    ]),
    ("The paradoxical unity of the state", [
        "Unity is at best partial, the constantly evolving outcome of unifying "
        "tendencies and their dis-unifying counter-tendencies.",
    ]),
    ("Conclusion: towards a political ontology of the state", [
        "The state is neither real nor fictitious but as if real, a conceptual "
        "abstraction whose value is best seen as an open analytical question.",
    ]),
]

NOTES = [
    "1. I am indebted to the referees for a penetrating and deeply constructive "
    "set of comments on an early iteration of the argument presented here.",
    "2. An account of the state as an ideal collective actor is a good example "
    "of this kind of reasoning, and is discussed at length below.",
    "3. Before proceeding there is an obvious objection that needs considering, "
    "for it might be argued that to resolve such disputes on practical grounds "
    "is to privilege epistemology over ontology rather than the reverse.",
    "4. That said, the nature of such effects will depend on which institutions "
    "are seen to comprise the state and how they respond to lobbying.",
    "5. One could go further and suggest that such effects do not require a "
    "shared and articulated conception of the state at all.",
    "6. Similar observations, cast in slightly different terms, characterize a "
    "large part of the surrounding literature.",
]

REFERENCES = [
    "Abrams, Philip 1988 'Notes on the Difficulty of Studying the State', "
    "Journal of Historical Sociology 1(1): 58-89.",
    "Bartelson, Jens 1995 A Genealogy of Sovereignty, Cambridge: Cambridge "
    "University Press.",
    "Bhaskar, Roy 1979 A Realist Theory of Science, Brighton: Harvester.",
    "Eulau, Heinz 1953 The Behavioural Persuasion in Politics, New York: "
    "Random House.",
    "Jessop, Bob 1990 State Theory, Cambridge: Polity Press.",
    "Skinner, Quentin 1989 'The State', in Political Innovation and Conceptual "
    "Change, Cambridge: Cambridge University Press.",
]


def wrap(text, size, width, font=ROMAN):
    lines, cur = [], ""
    for word in text.split():
        trial = f"{cur} {word}".strip()
        if cur and pymupdf.get_text_length(trial, fontname=font, fontsize=size) > width:
            lines.append(cur)
            cur = word
        else:
            cur = trial
    if cur:
        lines.append(cur)
    return lines


def put(page, x, y, text, size=BODY, font=ROMAN):
    page.insert_text((x, y), text, fontsize=size, fontname=font)
    return y


def put_glued(page, x, y, text, size=BODY):
    """Draw a line word by word with no space glyphs between them.

    Each word is placed at its own x, separated by a gap a shade wider than
    the space between letters. Extracted, the line reads as one run of
    characters -- exactly what the offending typesetters produce.
    """
    cursor = x
    for word in text.split():
        page.insert_text((cursor, y), word, fontsize=size, fontname=ROMAN)
        cursor += pymupdf.get_text_length(word, fontname=ROMAN, fontsize=size) + _GLUE_GAP


def flow(page, y, lines, size=BODY, x=LEFT, lead=13.0, font=ROMAN):
    for line in lines:
        put(page, x, y, line, size, font)
        y += lead
    return y


def main(out="tests/paper.pdf"):
    doc = pymupdf.open()
    measure = RIGHT - LEFT

    # --- opening page: title, byline, abstract, keywords -------------------
    p = doc.new_page(width=W, height=H)
    put(p, LEFT, 70, "ARTICLE", 8, BOLD)
    put(p, LEFT, 100, "Neither real nor fictitious but 'as if real'?", 15, BOLD)
    put(p, LEFT, 120, "A political ontology of the state", 15, BOLD)
    put(p, LEFT, 145, "Colin Hay", 10)
    put(p, LEFT, 158, "Department of Politics, University of Somewhere", 8)
    put(p, LEFT, 170, "Email: colin.hay@example.edu", 8)
    put(p, LEFT, 196, "Abstract", 9, BOLD)
    # The abstract is inset, and set with no space glyphs at all.
    y = 212
    for line in wrap(ABSTRACT, SMALL, measure - 24):
        put_glued(p, LEFT + 12, y, line, SMALL)
        y += 11
    put(p, LEFT + 12, y + 6, "Keywords: state; ontology; realism", SMALL)
    y += 34
    y = flow(p, y, wrap(
        "No concept is more central to political discourse than that of the "
        "state, yet the concept remains elusive and, for some at least, "
        "illusory.1 The term is notoriously difficult to define.", BODY, measure))
    put(p, LEFT, 800, "(c) Example Learned Society 2014. Published by Example Press.", 6)

    # --- body sections, headings bold at body size -------------------------
    y_start = 70
    page = doc.new_page(width=W, height=H)
    y = y_start
    for title, paras in SECTIONS:
        if y > 620:
            page = doc.new_page(width=W, height=H)
            y = y_start
        for line in wrap(title, BODY, measure, BOLD):
            put(page, LEFT, y, line, BODY, BOLD)
            y += 14
        y += 4
        for para in list(paras) + FILLER:
            if y > 700:
                page = doc.new_page(width=W, height=H)
                y = y_start
            y = flow(page, y, wrap(para, BODY, measure)) + 6
        y += 10

    # --- notes and bibliography: two columns, stacked in bands -------------
    p = doc.new_page(width=W, height=H)
    put(p, LEFT, 70, "Notes", BODY, BOLD)
    col_w = GUTTER_L1 - LEFT

    def split_columns(items, first):
        """Deliberately lopsided: more in the left column than the right.

        An even split is rescued by guessing the columns at the half-way
        mark, so an even fixture would pass whether or not the gutter is
        actually measured -- and the test would prove nothing.
        """
        left, right = [], []
        for i, item in enumerate(items):
            (left if i < first else right).extend(wrap(item, SMALL, col_w))
        return left, right

    note_l, note_r = split_columns(NOTES, 4)
    flow(p, 88, note_l, SMALL, LEFT, 10.5)
    flow(p, 88, note_r, SMALL, GUTTER_R0, 10.5)

    # The bibliography heading sits only half a point above body size, and
    # opens a second band across the same two columns.
    band = 88 + max(len(note_l), len(note_r)) * 10.5 + 26
    put(p, LEFT, band, "Bibliography", BODY + 0.5, BOLD)
    ref_l, ref_r = split_columns(REFERENCES, 4)
    flow(p, band + 20, ref_l, SMALL, LEFT, 10.5)
    flow(p, band + 20, ref_r, SMALL, GUTTER_R0, 10.5)

    doc.set_metadata({
        "title": "Neither real nor fictitious but 'as if real'? "
                 "A political ontology of the state",
        "author": "",
    })
    doc.save(out)
    doc.close()
    return out


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "tests/paper.pdf")
