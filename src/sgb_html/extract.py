"""Extract structured chapter content from Stadt.Geschichte.Basel chapter PDFs.

The book series uses one consistent typographic system; every line of text can
be classified by the font family and size of its dominant span:

* EuclidCircularB-Semibold >= 18 pt      chapter title
* EuclidCircularB 13-16 pt               section headings
* EuclidCircularB-Regular ~12.5 pt       lead (intro) paragraph
* Practice ~10.4 pt                      body text (Italic -> ``<em>``)
* Practice-Extrabold ~11 pt              pull quotes (dropped, duplicated design text)
* EuclidCircularB-Semibold ~9.5 pt       sidebar/inset headings
* EuclidCircularB-Regular ~8.5 pt        sidebar/inset prose
* EuclidCircularB ~7-7.5 pt              captions, running heads, page numbers (dropped)
* EuclidCircularB ~6.5 pt                endnote text
* Practice-Bold < 6 pt, superscript      footnote markers in running text

Everything here is pure: PDF access is abstracted behind :class:`SupportsTextDict`
so the logic can be tested with plain dictionaries.
"""

from __future__ import annotations

import re
from collections.abc import Collection, Iterable, Iterator, Sequence
from dataclasses import dataclass, field
from enum import Enum
from itertools import pairwise
from typing import Protocol

SOFT_HYPHEN = "­"

_DOI_RE = re.compile(r"^\s*https?://doi\.org/10\.21255/sgb-\S*\s*$")  # the chapter's running foot
_NOTE_START_RE = re.compile(r"^\s*(\d+) ?\t\s*(.*)$")
_COLUMN_GAP = 50.0
_PARAGRAPH_INDENT = 4.0
_MAX_PARAGRAPH_INDENT = 40.0
_COLUMN_TOLERANCE = 4.0  # x-distance in points that still counts as the same column


class Kind(Enum):
    """Semantic role of a PDF text line."""

    TITLE = "title"
    LEAD = "lead"
    HEADING = "heading"
    BODY = "body"
    ASIDE_HEAD = "aside_head"
    SUBHEAD = "subhead"
    ASIDE_BODY = "aside_body"
    NOTE = "note"
    ENTRY = "entry"
    DROP = "drop"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class RawSpan:
    """One styled run of characters inside a PDF line."""

    text: str
    font: str
    size: float
    superscript: bool = False


@dataclass(frozen=True)
class RawLine:
    """One PDF text line with its page number and position."""

    page: int
    x0: float
    y0: float
    spans: tuple[RawSpan, ...]
    x1: float = 0.0

    @property
    def text(self) -> str:
        return "".join(s.text for s in self.spans)


@dataclass(frozen=True)
class TextRun:
    """Inline text with minimal styling."""

    text: str
    italic: bool = False


@dataclass(frozen=True)
class Marker:
    """Inline endnote reference."""

    number: int


Inline = TextRun | Marker


@dataclass
class Paragraph:
    inlines: list[Inline]
    lead: bool = False
    hyphenated: bool = False
    """True when the last PDF line ended on a soft hyphen (word split across a break)."""


@dataclass
class Heading:
    text: str
    level: int = 2


@dataclass
class Aside:
    """Sidebar/inset story; real prose set apart from the main text."""

    blocks: list[Paragraph | Heading]


Block = Paragraph | Heading | Aside


@dataclass
class Note:
    number: int
    text: str
    linked: bool = True
    """False when the marker sits in text the extractor drops (a figure or table caption)."""


@dataclass
class Chapter:
    """Structured chapter content, ready for rendering."""

    title: str = ""
    blocks: list[Block] = field(default_factory=list)
    notes: list[Note] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    author_line: str = ""


class SupportsTextDict(Protocol):
    """The one PyMuPDF page method the extractor needs."""

    def get_text(self, option: str) -> dict: ...


def iter_raw_lines(pages: Iterable[SupportsTextDict]) -> Iterator[RawLine]:
    """Yield non-empty text lines of all pages in PDF order."""
    for page_number, page in enumerate(pages, start=1):
        for block in page.get_text("dict")["blocks"]:
            if block.get("type", 0) != 0:  # image blocks
                continue
            for line in block["lines"]:
                spans = tuple(
                    RawSpan(
                        text=span["text"],
                        font=span["font"],
                        size=float(span["size"]),
                        superscript=bool(span.get("flags", 0) & 1),
                    )
                    for span in line["spans"]
                )
                if not "".join(s.text for s in spans).strip():
                    continue
                x0, y0, x1 = line["bbox"][0], line["bbox"][1], line["bbox"][2]
                yield RawLine(page=page_number, x0=x0, y0=y0, spans=spans, x1=x1)


def dominant_span(line: RawLine) -> RawSpan:
    """The span that visually defines a line: the longest non-superscript one.

    >>> body = RawSpan("Text im Lauftext ", "Practice-Regular", 10.4)
    >>> mark = RawSpan("12", "Practice-Bold", 5.2, superscript=True)
    >>> dominant_span(RawLine(1, 0, 0, (mark, body))).font
    'Practice-Regular'
    """
    candidates = [s for s in line.spans if not s.superscript and s.text.strip()]
    if not candidates:
        candidates = list(line.spans)
    text_spans = [s for s in candidates if not _is_figure_anchor(s)]
    return max(text_spans or candidates, key=lambda s: len(s.text.strip()))


def _is_figure_anchor(span: RawSpan) -> bool:
    """A small figure reference such as ``[7 | 8]`` inside running text.

    >>> _is_figure_anchor(RawSpan("[7 | 8]", "EuclidCircularB-Semibold", 7.0))
    True
    >>> _is_figure_anchor(RawSpan("[7] Kasten", "EuclidCircularB-Regular", 7.0))
    False
    """
    text = span.text.strip()
    return (
        span.font.startswith("EuclidCircularB")
        and span.size < 8
        and text.startswith("[")
        and text.endswith("]")
        and "|" in text
    )


def _is_marker_span(span: RawSpan) -> bool:
    """A bold superscript number: an endnote marker (7.3 pt in a lead, 5.2 pt in body)."""
    return (
        span.superscript and span.size < 8 and "Bold" in span.font and span.text.strip().isdigit()
    )


def classify(line: RawLine) -> Kind:
    """Map a line to its semantic role via the series' typographic system."""
    if _DOI_RE.search(line.text):
        return Kind.DROP
    span = dominant_span(line)
    font, size = span.font, span.size
    if font.startswith("Practice"):
        if "Extrabold" in font:
            return Kind.DROP  # pull quotes
        if 9.5 <= size <= 11.5:
            return Kind.BODY
        if size < 6.2:
            # Endnote first lines start with a bold number span; a lone
            # superscript digit is a stray marker without body context.
            return Kind.DROP if line.text.strip().isdigit() else Kind.NOTE
        return Kind.UNKNOWN
    if font.startswith("EuclidCircularB"):
        heavy = "Semibold" in font or "Bold" in font
        aside_span = next(
            (
                span
                for span in line.spans
                if span.font.startswith("EuclidCircularB")
                and 8.0 <= span.size < 8.8
                and span.text.strip()
            ),
            None,
        )
        if aside_span is not None:
            # A whole line in Semibold is the sidebar title; Regular is sidebar text.
            if heavy and 8.0 <= size < 8.8:
                return Kind.ASIDE_HEAD
            return Kind.ASIDE_BODY
        if size >= 18:
            return Kind.TITLE
        if 13 <= size < 16:
            # Semibold ~14 is a section heading; Regular ~14 is the author
            # line on the chapter opener (metadata comes from the OMP API).
            return Kind.HEADING if heavy else Kind.DROP
        if 11.5 <= size < 13:
            return Kind.LEAD
        if 8.8 <= size < 11.5:
            # Semibold ~9.5 heads sidebars; Regular ~9.5 is the author line
            # on the chapter opener (metadata comes from the OMP API instead).
            return Kind.ASIDE_HEAD if heavy else Kind.DROP
        if 6.9 <= size < 8.0:
            return Kind.DROP  # captions, running heads, page numbers
        if 6.2 <= size < 6.9:
            return Kind.NOTE
        return Kind.DROP  # tiny ornaments
    return Kind.UNKNOWN


def _clean(text: str) -> str:
    """Normalise whitespace inside a line; soft hyphens at line ends survive.

    >>> _clean("Kalt- und ­Warmzeiten\t des")
    'Kalt- und Warmzeiten des'
    """
    keep_trailing = text.rstrip().endswith(SOFT_HYPHEN)
    cleaned = re.sub(r"[ \t]+", " ", text.replace(SOFT_HYPHEN, "")).strip()
    return cleaned + SOFT_HYPHEN if keep_trailing else cleaned


_URL_START_RE = re.compile(r"(?:https?://|www\.)")
_URL_BREAK_CHARS = "/_=&?:-."


def _is_wrapped_url(previous: str, upcoming: str) -> bool:
    """True when a URL breaks at a line end and continues on the next line.

    >>> _is_wrapped_url("siehe https://www.srf.ch/play/", "tv/video")
    True
    >>> _is_wrapped_url("https://www.srf.ch/play/tv", "abgerufen")
    False
    >>> _is_wrapped_url("https://www.srf.ch.", "Danach")
    False
    >>> _is_wrapped_url("https://doi.org/10.1016/S0277-3791(03)", "00083-0")
    True
    >>> _is_wrapped_url("", "x")
    False
    """
    words = previous.split()
    if not words or not upcoming:
        return False
    tail = words[-1]
    if not _URL_START_RE.match(tail):
        return False
    if tail.endswith(")") and upcoming[0].isdigit():
        return True  # a DOI such as ...3791(03) 00083-0
    if tail[-1] not in _URL_BREAK_CHARS:
        return False
    return not (tail.endswith(".") and upcoming[0].isupper())


def _glue(previous: str, upcoming: str = "") -> str:
    """Separator when continuing a paragraph on the next PDF line.

    Soft hyphens mark hyphenation breaks, a hard hyphen at a line end is a
    broken compound, and an en dash before a digit is a broken number range.

    >>> _glue("Brönni" + SOFT_HYPHEN), _glue("rot-"), _glue("und")
    ('', '', ' ')
    >>> _glue("S. 123–", "127."), _glue("Basel –", "und")
    ('', ' ')
    """
    if previous.endswith((SOFT_HYPHEN, "-")):
        return ""
    if previous.endswith("–") and upcoming[:1].isdigit():
        return ""
    if _is_wrapped_url(previous, upcoming):
        return ""
    return " "


def _line_inlines(line: RawLine, *, drop_anchors: bool = True) -> list[Inline]:
    """Split a line into text runs and endnote markers, keeping span spacing.

    Endnote markers are bold superscript digits. Other small superscripts
    (isotope numbers such as ¹⁴C) stay plain text; small EuclidCircularB
    spans inside running text are figure anchors like ``[7 | 8]`` and are
    dropped together with the images they point to — except in appendix
    chapters, where small EuclidCircularB *is* the running text.
    """
    inlines: list[Inline] = []
    for span in line.spans:
        text = span.text.replace("\t", " ")
        if not text.strip():
            # A blank span (often inside a dropped figure anchor) still separates words.
            if inlines and isinstance(inlines[-1], TextRun) and not inlines[-1].text.endswith(" "):
                inlines[-1] = TextRun(inlines[-1].text + " ", inlines[-1].italic)
            continue
        digits = text.strip()
        if _is_marker_span(span):
            if inlines and isinstance(inlines[-1], TextRun):
                # Footnote markers hug the preceding word; drop any span padding.
                inlines[-1] = TextRun(inlines[-1].text.rstrip(), inlines[-1].italic)
            inlines.append(Marker(int(digits)))
            continue
        if (
            drop_anchors
            and not span.superscript
            and span.size < 8
            and span.font.startswith("EuclidCircularB")
        ):
            continue
        italic = "Italic" in span.font or span.font.endswith("I")
        previous = inlines[-1] if inlines else None
        if isinstance(previous, TextRun) and previous.italic == italic:
            inlines[-1] = TextRun(previous.text + text, italic)
        else:
            if isinstance(previous, Marker) and text[:1].isalnum():
                text = " " + text
            inlines.append(TextRun(text, italic))
    return _trim_line(inlines)


def _trim_line(inlines: list[Inline]) -> list[Inline]:
    """Collapse whitespace and trim the line ends; keep a trailing soft hyphen."""
    trimmed: list[Inline] = []
    for index, item in enumerate(inlines):
        if isinstance(item, TextRun):
            text = re.sub(r"\s+", " ", item.text)
            if index == 0:
                text = text.lstrip()
            if index == len(inlines) - 1:
                text = text.rstrip()
            trimmed.append(TextRun(text, item.italic))
        else:
            trimmed.append(item)
    return trimmed


_KEEP_HYPHEN_BEFORE = {"und", "oder", "bis", "sowie", "resp.", "bzw."}


def _drop_break_hyphen(previous: str, upcoming: str) -> str:
    """Remove a hyphen that only marks a line break in small print.

    Sidebars and notes print the break hyphen as a plain hyphen. A hyphen after
    a lower-case letter before a lower-case word is a break, except in a
    suspended compound such as "Kalt- und Warmzeiten".

    >>> _drop_break_hyphen("um 640 ge-", "gründet")
    'um 640 ge'
    >>> _drop_break_hyphen("Fami" + SOFT_HYPHEN + "-", "lie")
    'Fami\xad'
    >>> _drop_break_hyphen("Moutier-", "Grandval"), _drop_break_hyphen("Kalt-", "und Warm")
    ('Moutier-', 'Kalt-')
    >>> _drop_break_hyphen("Ende", "x")
    'Ende'
    """
    if previous.endswith(SOFT_HYPHEN + "-"):
        return previous[:-1]
    if (
        len(previous) > 1
        and previous.endswith("-")
        and previous[-2].isalpha()
        and previous[-2].islower()
        and upcoming[:1].islower()
        and upcoming.split()[0] not in _KEEP_HYPHEN_BEFORE
    ):
        return previous[:-1]
    return previous


def _append_line(
    inlines: list[Inline], line: RawLine, *, drop_anchors: bool = True, small: bool = False
) -> None:
    """Append a line's inlines, joining text across the line break.

    Soft hyphens are kept while accumulating (they decide the glue of the
    next line) and are stripped later by :func:`_finish`.
    """
    new_runs = _line_inlines(line, drop_anchors=drop_anchors)
    if not new_runs:
        return
    previous = inlines[-1] if inlines else None
    first = new_runs[0]
    if isinstance(previous, TextRun):
        upcoming = first.text if isinstance(first, TextRun) else ""
        glue = _glue(previous.text, upcoming)
        if small:
            kept = _drop_break_hyphen(previous.text, upcoming)
            if kept != previous.text:
                glue = ""
                previous = TextRun(kept, previous.italic)
                inlines[-1] = previous
        if isinstance(first, TextRun) and previous.italic == first.italic:
            inlines[-1] = TextRun(previous.text + glue + first.text, previous.italic)
            new_runs = new_runs[1:]
        elif glue and isinstance(first, TextRun):
            inlines[-1] = TextRun(previous.text + glue, previous.italic)
    elif isinstance(previous, Marker) and isinstance(first, TextRun):
        new_runs[0] = TextRun(" " + first.text, first.italic)
    inlines.extend(new_runs)


def _polish(text: str) -> str:
    """Final text cleanup: soft hyphens out, spacing before punctuation fixed.

    A compound head whose hyphen is directly followed by a conjunction is a
    German suspended (ellipsis) compound: the space after the hyphen is restored
    ("Trocken-oder" -> "Trocken- oder", "In-und" -> "In- und", "1950er-und" ->
    "1950er- und"). The head must carry an upper-case letter or be a decade such
    as ``1950er``; this leaves all-lower-case URL slugs (``fakultaeten-und``) and
    numeric URL path segments (``1876-bis-1988``, ``im-19-und-20``) glued.

    >>> _polish(f"exis{SOFT_HYPHEN}tierte  . Und")
    'existierte. Und'
    >>> _polish("Trocken-oder Kälteperioden, In-und Ausland")
    'Trocken- oder Kälteperioden, In- und Ausland'
    >>> _polish("1950er-und 1960er-Jahren")
    '1950er- und 1960er-Jahren'
    >>> _polish("chronik/1876-bis-1988, obdachlosigkeit-im-19-und-20-jahrhundert")
    'chronik/1876-bis-1988, obdachlosigkeit-im-19-und-20-jahrhundert'
    """
    text = text.replace(SOFT_HYPHEN, "")
    text = re.sub(
        r"(\b(?:[A-Za-zÄÖÜäöüß0-9]*[A-ZÄÖÜ][A-Za-zÄÖÜäöüß0-9]*|[0-9]+er)-)(und|oder|bis)\b",
        r"\1 \2",
        text,
    )
    text = re.sub(r" {2,}", " ", text)
    return re.sub(r" ([.,;:])", r"\1", text)


def _paragraph(inlines: list[Inline], *, lead: bool) -> Paragraph:
    """Build a finished paragraph; remember a soft hyphen at its very end."""
    last = inlines[-1] if inlines else None
    hyphenated = isinstance(last, TextRun) and last.text.endswith(SOFT_HYPHEN)
    return Paragraph(_finish(inlines), lead=lead, hyphenated=hyphenated)


def _finish(inlines: list[Inline]) -> list[Inline]:
    """Apply the final text cleanup to every run of a paragraph."""
    return [TextRun(_polish(i.text), i.italic) if isinstance(i, TextRun) else i for i in inlines]


_LEAD_SHORT_LINE = 5.0  # font sizes; a lead line this short before a break ends a paragraph


def _ends_lead_paragraph(line: RawLine, widest: float) -> bool:
    """True when a lead line ends a sentence and stops far short of the right edge.

    Lead paragraphs have no first-line indent and no extra spacing. A short last
    line is the only cue. Lines that stop less than five font sizes short stay
    joined, because the ragged right edge varies by about that much.

    >>> text = RawSpan("Ende.", "EuclidCircularB-Regular", 12.5)
    >>> _ends_lead_paragraph(RawLine(1, 57.0, 0.0, (text,), x1=239.0), 416.0)
    True
    >>> _ends_lead_paragraph(RawLine(1, 57.0, 0.0, (text,), x1=358.0), 382.0)
    False
    """
    text = "".join(s.text for s in line.spans if not _is_marker_span(s))
    stripped = text.rstrip()
    if not stripped or stripped[-1] not in ".!?»›":
        return False
    return widest - line.x1 > _LEAD_SHORT_LINE * dominant_span(line).size


def _starts_paragraph(line: RawLine, base: float, previous: RawLine | None) -> bool:
    """First-line indents (~25-30 pt) start paragraphs; larger x-shifts are
    text flowing around images or margin boxes and continue the paragraph."""
    deltas = [line.x0 - base]
    if previous is not None:
        deltas.append(line.x0 - previous.x0)
    return any(_PARAGRAPH_INDENT < delta <= _MAX_PARAGRAPH_INDENT for delta in deltas)


def _build_paragraphs(
    lines: Sequence[RawLine], *, lead: bool = False, small: bool = False
) -> list[Paragraph]:
    """Group consecutive lines into paragraphs using first-line indents."""
    paragraphs: list[Paragraph] = []
    base_x: dict[int, float] = {}
    widest: dict[int, float] = {}
    for line in lines:
        base_x[line.page] = min(base_x.get(line.page, line.x0), line.x0)
        widest[line.page] = max(widest.get(line.page, line.x1), line.x1)
    current: list[Inline] = []
    previous: RawLine | None = None
    for line in lines:
        if current and (
            _starts_paragraph(line, base_x[line.page], previous)
            or (
                lead
                and previous is not None
                and _ends_lead_paragraph(previous, widest[previous.page])
            )
        ):
            paragraphs.append(_paragraph(current, lead=lead))
            current = []
        _append_line(current, line, small=small)
        previous = line
    if current:
        paragraphs.append(_paragraph(current, lead=lead))
    return paragraphs


def _note_columns(lines: Sequence[RawLine]) -> list[list[RawLine]]:
    """Group endnote lines into columns per page, left to right."""
    columns: list[list[RawLine]] = []
    pages = sorted({line.page for line in lines})
    for page in pages:
        page_lines = sorted((li for li in lines if li.page == page), key=lambda li: li.x0)
        page_columns: list[list[RawLine]] = []
        for line in page_lines:
            if page_columns and line.x0 - page_columns[-1][0].x0 < _COLUMN_GAP:
                page_columns[-1].append(line)
            else:
                page_columns.append([line])
        columns.extend(sorted(column, key=lambda li: li.y0) for column in page_columns)
    return columns


def _plain_text(line: RawLine) -> str:
    return _clean("".join(span.text for span in line.spans))


def _is_note_start(line: RawLine) -> bool:
    first = line.spans[0]
    return "Bold" in first.font and first.size < 6.2 and bool(_NOTE_START_RE.match(line.text))


def _build_notes(lines: Sequence[RawLine]) -> tuple[list[Note], list[str]]:
    """Parse the ``Anmerkungen`` block: bold number spans start a new note.

    Diagram and table labels share the endnote font; a column that contains
    no numbered note start is such a label cluster and is skipped entirely.
    """
    notes: list[Note] = []
    warnings: list[str] = []
    for column in _note_columns(lines):
        if not any(_is_note_start(line) for line in column):
            sample = column[0]
            warnings.append(
                f"page {sample.page}: skipped {len(column)} small-print lines "
                f"without note numbers: {sample.text[:60]!r}"
            )
            continue
        for line in column:
            match = _NOTE_START_RE.match(line.text)
            if _is_note_start(line) and match:
                notes.append(Note(int(match.group(1)), _clean(match.group(2))))
            elif notes:
                continuation = _plain_text(line)
                kept = _drop_break_hyphen(notes[-1].text, continuation)
                glue = "" if kept != notes[-1].text else _glue(kept, continuation)
                joined = kept + glue + continuation
                notes[-1] = Note(notes[-1].number, joined)
            else:
                warnings.append(
                    f"page {line.page}: endnote continuation without start: {line.text[:60]!r}"
                )
    return [Note(n.number, _polish(n.text)) for n in notes], warnings


def _build_aside(lines: list[tuple[Kind, RawLine]]) -> Aside:
    """Assemble one page's sidebar lines into an aside block."""
    lines = _aside_reading_order(lines)
    aside_blocks: list[Paragraph | Heading] = []
    prose: list[RawLine] = []
    for kind, line in lines:
        if kind is Kind.ASIDE_HEAD:
            aside_blocks.extend(_build_paragraphs(prose, small=True))
            prose = []
            head = _plain_text(line)
            if aside_blocks and isinstance(aside_blocks[-1], Heading):
                aside_blocks[-1].text += _glue(aside_blocks[-1].text, head) + head
            else:
                aside_blocks.append(Heading(head, level=3))
        else:
            prose.append(line)
    aside_blocks.extend(_build_paragraphs(prose, small=True))
    last = aside_blocks[-1] if len(aside_blocks) > 1 else None
    if isinstance(last, Heading):
        # A title with no text after it is the author line at the end of the sidebar.
        aside_blocks[-1] = Paragraph([TextRun(last.text, False)])
    return Aside(aside_blocks)


def _aside_reading_order(lines: Sequence[tuple[Kind, RawLine]]) -> list[tuple[Kind, RawLine]]:
    """Read sidebar text column by column within heading-delimited bands."""
    headings = sorted(
        (item for item in lines if item[0] is Kind.ASIDE_HEAD),
        key=lambda item: item[1].y0,
    )
    prose = [item for item in lines if item[0] is not Kind.ASIDE_HEAD]
    ordered: list[tuple[Kind, RawLine]] = []
    lower_y = float("-inf")
    for heading in headings:
        heading_y = heading[1].y0
        band = [item for item in prose if lower_y <= item[1].y0 < heading_y]
        ordered.extend(_column_reading_order(band))
        ordered.append(heading)
        lower_y = heading_y
    ordered.extend(_column_reading_order([item for item in prose if item[1].y0 >= lower_y]))
    return ordered


def _column_reading_order(lines: Sequence[tuple[Kind, RawLine]]) -> list[tuple[Kind, RawLine]]:
    """Read positioned text lines column by column, left to right."""
    columns: list[list[tuple[Kind, RawLine]]] = []
    for item in sorted(lines, key=lambda item: item[1].x0):
        line = item[1]
        if columns and line.x0 - columns[-1][0][1].x0 < _COLUMN_GAP:
            columns[-1].append(item)
        else:
            columns.append([item])
    ordered: list[tuple[Kind, RawLine]] = []
    for column in columns:
        ordered.extend(sorted(column, key=lambda item: item[1].y0))
    return ordered


def _paragraph_text(paragraph: Paragraph) -> str:
    return "".join(inline.text for inline in paragraph.inlines if isinstance(inline, TextRun))


def _is_unfinished(paragraph: Paragraph) -> bool:
    text = _paragraph_text(paragraph).rstrip()
    return bool(text) and text[-1] not in ".!?;:»›)]}"


def _append_inlines(target: list[Inline], source: list[Inline]) -> None:
    """Append finished inline runs while preserving paragraph glue rules."""
    if not source:
        return
    incoming = list(source)
    previous = target[-1] if target else None
    first = incoming[0]
    if isinstance(previous, TextRun):
        upcoming = first.text if isinstance(first, TextRun) else ""
        glue = _glue(previous.text, upcoming)
        if isinstance(first, TextRun) and previous.italic == first.italic:
            target[-1] = TextRun(previous.text + glue + first.text, previous.italic)
            incoming = incoming[1:]
        elif glue and isinstance(first, TextRun):
            target[-1] = TextRun(previous.text + glue, previous.italic)
    elif isinstance(previous, Marker) and isinstance(first, TextRun):
        incoming[0] = TextRun(" " + first.text, first.italic)
    target.extend(incoming)


def _merge_continuation_asides(blocks: list[Block]) -> list[Block]:
    """Join adjacent page asides when prose continues across the page break."""
    merged: list[Block] = []
    for block in blocks:
        if (
            isinstance(block, Aside)
            and merged
            and isinstance(merged[-1], Aside)
            and block.blocks
            and isinstance(block.blocks[0], Paragraph)
            and merged[-1].blocks
            and isinstance(merged[-1].blocks[-1], Paragraph)
            and _is_unfinished(merged[-1].blocks[-1])
        ):
            previous = merged[-1].blocks[-1]
            if previous.hyphenated:
                previous.inlines.append(TextRun(SOFT_HYPHEN, False))
            _append_inlines(previous.inlines, block.blocks[0].inlines)
            previous.inlines[:] = _finish(previous.inlines)
            previous.hyphenated = block.blocks[0].hyphenated
            merged[-1].blocks.extend(block.blocks[1:])
        else:
            merged.append(block)
    return merged


def _main_flow(classified: list[tuple[Kind, RawLine]]) -> list[Block]:
    """Assemble headings, body paragraphs, and asides in reading order.

    Paragraphs continue across page breaks; a page's asides are queued and
    inserted once the running paragraph closes, so they never cut a sentence.
    """
    body_lines = [line for kind, line in classified if kind is Kind.BODY]
    base_x: dict[int, float] = {}
    for line in body_lines:
        base_x[line.page] = min(base_x.get(line.page, line.x0), line.x0)

    blocks: list[Block] = []
    current: list[Inline] = []
    current_is_lead = False
    pending_asides: list[Block] = []
    previous: RawLine | None = None
    previous_lead: RawLine | None = None
    lead_widest: dict[int, float] = {}
    for _kind, line in classified:
        if _kind is Kind.LEAD:
            lead_widest[line.page] = max(lead_widest.get(line.page, line.x1), line.x1)

    def close_paragraph() -> None:
        nonlocal current
        if current:
            blocks.append(Paragraph(_finish(current), lead=current_is_lead))
            current = []
        blocks.extend(pending_asides)
        pending_asides.clear()

    for page in sorted({line.page for _, line in classified}):
        main = [
            (kind, line)
            for kind, line in classified
            if line.page == page and kind in (Kind.HEADING, Kind.SUBHEAD, Kind.BODY, Kind.LEAD)
        ]
        main.sort(key=lambda item: item[1].y0)
        for kind, line in main:
            if kind is Kind.LEAD:
                # A mid-chapter intro (lead-style text after the first body
                # line) is a lead paragraph of its own, at its own position.
                if not current_is_lead or (
                    previous_lead is not None
                    and _ends_lead_paragraph(previous_lead, lead_widest[previous_lead.page])
                ):
                    close_paragraph()
                current_is_lead = True
                _append_line(current, line)
                previous_lead = line
                continue
            if current_is_lead:
                close_paragraph()
                current_is_lead = False
            if kind in (Kind.HEADING, Kind.SUBHEAD):
                close_paragraph()
                level = 2 if kind is Kind.HEADING else 3
                heading_text = _plain_text(line)
                if blocks and isinstance(blocks[-1], Heading) and blocks[-1].level == level:
                    blocks[-1].text += _glue(blocks[-1].text, heading_text) + heading_text
                else:
                    blocks.append(Heading(heading_text, level=level))
            else:
                if current and _starts_paragraph(line, base_x[line.page], previous):
                    close_paragraph()
                _append_line(current, line)
                previous = line

        aside_lines = [
            (kind, line)
            for kind, line in classified
            if line.page == page and kind in (Kind.ASIDE_HEAD, Kind.ASIDE_BODY)
        ]
        if aside_lines:
            pending_asides.append(_build_aside(aside_lines))
        if not current:
            blocks.extend(pending_asides)
            pending_asides.clear()

    close_paragraph()
    return _merge_continuation_asides(blocks)


def classify_appendix(line: RawLine) -> Kind:
    """Semantic role of a line in an appendix chapter (Anhang).

    Appendices (bibliography, image credits, registers, author notes) are
    set almost entirely in small EuclidCircularB: ~9.5 pt Semibold section
    headings, ~9 pt Regular intro prose, ~6.5 pt entries with hanging
    indents, and ~6 pt Semibold numbered image credits.
    """
    if _DOI_RE.search(line.text):
        return Kind.DROP
    span = dominant_span(line)
    font, size = span.font, span.size
    if not font.startswith("EuclidCircularB"):
        return Kind.UNKNOWN
    heavy = "Semibold" in font or "Bold" in font
    if size >= 18:
        return Kind.DROP  # chapter title, extracted separately
    if 8.8 <= size < 11.5:
        return Kind.HEADING if heavy else Kind.BODY
    if 6.9 <= size < 8.8:
        return Kind.DROP  # running heads and page numbers
    if 5.8 <= size < 6.9:
        if heavy:
            starts_numbered = bool(_NOTE_START_RE.match(line.text))
            return Kind.ENTRY if starts_numbered else Kind.ASIDE_HEAD
        return Kind.ENTRY
    return Kind.DROP


def _column_runs(lines: Sequence[RawLine]) -> Iterator[list[RawLine]]:
    """Yield lines page by page, column by column, top to bottom."""
    for page in sorted({line.page for line in lines}):
        page_lines = sorted((li for li in lines if li.page == page), key=lambda li: li.x0)
        columns: list[list[RawLine]] = []
        for line in page_lines:
            if columns and line.x0 - columns[-1][0].x0 < _COLUMN_GAP:
                columns[-1].append(line)
            else:
                columns.append([line])
        for column in columns:
            yield sorted(column, key=lambda li: li.y0)


def _appendix_flow(classified: list[tuple[Kind, RawLine]]) -> list[Block]:
    """Assemble an appendix: sections, intro prose, and list entries.

    Entries use hanging indents, so an entry starts on lines at the column
    base and continues on indented ones. Intro prose has no indents; there
    a vertical gap larger than the regular leading starts a new paragraph.
    """
    blocks: list[Block] = []
    current: list[Inline] = []
    kept = [(kind, line) for kind, line in classified if kind is not Kind.DROP]
    by_id = {id(line): kind for kind, line in kept}

    def close() -> None:
        nonlocal current
        if current:
            blocks.append(Paragraph(_finish(current)))
            current = []

    previous: RawLine | None = None
    for column in _column_runs([line for _, line in kept]):
        entry_base = min((li.x0 for li in column if by_id[id(li)] is Kind.ENTRY), default=0.0)
        for line in column:
            kind = by_id[id(line)]
            if kind is Kind.ASIDE_HEAD and current and line.x0 > entry_base + _PARAGRAPH_INDENT:
                # Indented bold small print continues an entry (image
                # credits share the subsection-heading style).
                kind = Kind.ENTRY
            if kind in (Kind.HEADING, Kind.ASIDE_HEAD):
                close()
                level = 2 if kind is Kind.HEADING else 3
                text = _plain_text(line)
                if blocks and isinstance(blocks[-1], Heading) and blocks[-1].level == level:
                    blocks[-1].text += _glue(blocks[-1].text, text) + text
                else:
                    blocks.append(Heading(text, level=level))
            elif kind is Kind.BODY:
                leading = 2.8 * dominant_span(line).size
                same_column = previous is not None and by_id.get(id(previous)) is Kind.BODY
                if current and same_column and line.y0 - previous.y0 > leading:
                    close()
                _append_line(current, line, drop_anchors=False)
            else:
                if current and line.x0 <= entry_base + _PARAGRAPH_INDENT:
                    close()
                _append_line(current, line, drop_anchors=False)
            previous = line
    close()
    for block in blocks:
        _strip_heading_hyphens(block)
    return blocks


@dataclass
class LinePlan:
    """The extractor's final decision for every PDF line of a chapter."""

    classified: list[tuple[Kind, RawLine]]
    """The kept lines with their body-chapter role (the appendix title comes from here)."""
    decisions: list[tuple[RawLine, Kind]]
    """Every raw line with its final role; ``Kind.DROP`` marks a dropped line."""
    appendix: bool
    """True for an appendix chapter, which has its own line roles."""
    warnings: list[str]
    dropped_markers: set[int]
    """Endnote numbers printed in dropped lines (figure and table captions)."""


def plan_lines(raw_lines: Sequence[RawLine]) -> LinePlan:
    """Decide the role of every line: classify, apply fallbacks, drop late labels.

    ``extract_chapter`` and the review ledger both call this, so they cannot
    disagree about what the extractor kept.
    """
    classified: list[tuple[Kind, RawLine]] = []
    warnings: list[str] = []
    dropped_markers: set[int] = set()
    for line in raw_lines:
        kind = classify(line)
        if kind is Kind.UNKNOWN:
            span = dominant_span(line)
            series_font = span.font.startswith(("Practice", "EuclidCircularB"))
            warnings.append(
                f"page {line.page}: unknown style {span.font} {span.size:.1f}: {line.text[:60]!r}"
            )
            # Odd sizes of the series fonts are still book text; foreign
            # fonts are OCR layers inside reproduced images and are dropped.
            kind = Kind.BODY if series_font and span.size >= 8 else Kind.DROP
        if kind is Kind.DROP:
            dropped_markers.update(int(s.text) for s in line.spans if _is_marker_span(s))
        else:
            classified.append((kind, line))

    classified = _drop_opener_byline(classified)
    classified = _demote_late_leads(classified)
    classified = _promote_subheadings(classified)
    if any(kind in (Kind.BODY, Kind.LEAD) for kind, _ in classified):
        kept = {id(line): kind for kind, line in classified}
        decisions = [(line, kept.get(id(line), Kind.DROP)) for line in raw_lines]
        return LinePlan(classified, decisions, False, warnings, dropped_markers)
    # No running text in the series body font: an appendix chapter.
    decisions = []
    for line in raw_lines:
        kind = classify_appendix(line)
        if kind is Kind.UNKNOWN:
            span = dominant_span(line)
            warnings.append(
                f"page {line.page}: unknown appendix style {span.font} "
                f"{span.size:.1f}: {line.text[:60]!r}"
            )
            kind = Kind.DROP
        decisions.append((line, kind))
    return LinePlan(classified, decisions, True, warnings, dropped_markers)


def extract_chapter(pages: Iterable[SupportsTextDict]) -> Chapter:
    """Turn a chapter PDF into structured, text-only content."""
    raw_lines = list(iter_raw_lines(pages))
    plan = plan_lines(raw_lines)
    warnings = plan.warnings
    classified = plan.classified

    title_lines = [line for kind, line in classified if kind is Kind.TITLE]
    title = ""
    for line in title_lines:
        part = _plain_text(line)
        title = title + _glue(title, part) + part if title else part
    title = _polish(title)

    notes: list[Note] = []
    if not plan.appendix:
        first_body = next(
            (index for index, (kind, _) in enumerate(classified) if kind is Kind.BODY),
            len(classified),
        )
        lead_lines = [line for kind, line in classified[:first_body] if kind is Kind.LEAD]
        blocks: list[Block] = list(_build_paragraphs(lead_lines, lead=True))
        opener = {id(line) for line in lead_lines}
        blocks.extend(_main_flow([item for item in classified if id(item[1]) not in opener]))
        note_lines = [line for kind, line in classified if kind is Kind.NOTE]
        notes, note_warnings = _build_notes(note_lines)
        warnings.extend(note_warnings)
        notes = _prune_orphan_notes(notes, blocks, warnings, plan.dropped_markers)
    else:
        blocks = _appendix_flow([(kind, line) for line, kind in plan.decisions])

    for block in blocks:
        _strip_heading_hyphens(block)
    return Chapter(
        title=title,
        blocks=blocks,
        notes=notes,
        warnings=warnings,
        author_line=_author_line(raw_lines),
    )


def _author_line(raw_lines: Sequence[RawLine]) -> str:
    """Return the Regular ~14 pt text on the first page (the printed author line)."""
    if not raw_lines:
        return ""
    first_page = raw_lines[0].page
    parts: list[str] = []
    for line in raw_lines:
        span = dominant_span(line)
        if (
            line.page == first_page
            and span.font.startswith("EuclidCircularB")
            and not ("Semibold" in span.font or "Bold" in span.font)
            and 13 <= span.size < 16
        ):
            parts.append(line.text)
    return _clean(" ".join(parts))


_AUTHOR_WINDOW = 40  # characters allowed per author between the first and last name


def _printed_order(names: Sequence[str], source: str) -> tuple[str, ...] | None:
    """Order ``names`` by their closest joint appearance in ``source``, if any."""
    text = source.replace(SOFT_HYPHEN, "").replace("\xa0", " ")
    occurrences: dict[str, list[int]] = {}
    for name in names:
        family = name.split()[-1]
        found = [m.start() for m in re.finditer(re.escape(family), text)]
        if not found:
            return None
        occurrences[name] = found
    best: tuple[str, ...] | None = None
    best_span = _AUTHOR_WINDOW * len(names)
    for anchor in occurrences[names[0]]:
        picks = {
            name: min(positions, key=lambda position: abs(position - anchor))
            for name, positions in occurrences.items()
        }
        span = max(picks.values()) - min(picks.values())
        ordered = sorted(names, key=lambda name: picks[name])
        # A line break between two names means a list, such as an alphabetical
        # title page, and not an author line.
        on_one_line = not any("|" in text[picks[a] : picks[b]] for a, b in pairwise(ordered))
        if on_one_line and span <= best_span:
            best, best_span = tuple(ordered), span
    return best


def order_authors(names: Sequence[str], *sources: str) -> tuple[str, ...]:
    """Order authors as printed in the book.

    Each source is printed text, for example the chapter's author line or the
    volume front matter. A ``|`` marks a line break. The first source that
    names every author close together on one line sets the order. Without
    such a source the order is alphabetical by family name, so the result
    never depends on the API response order.

    >>> order_authors(["Anina Zahn", "Silas Gusset"], "Silas Gusset, Anina Zahn")
    ('Silas Gusset', 'Anina Zahn')
    >>> order_authors(["Anina Zahn", "Silas Gusset"], "", "Einleitung | Silas Gusset, Anina Zahn")
    ('Silas Gusset', 'Anina Zahn')
    >>> order_authors(["Anina Zahn", "Silas Gusset"], "Silas Gusset | Anina Zahn")
    ('Silas Gusset', 'Anina Zahn')
    >>> order_authors(["Zoe Zahn", "Anna Bauer"], "Einleitung")
    ('Anna Bauer', 'Zoe Zahn')
    """
    if len(names) > 1:
        for source in sources:
            ordered = _printed_order(names, source)
            if ordered is not None:
                return ordered
    return tuple(sorted(names, key=lambda name: (name.split()[-1].casefold(), name)))


_LATE_LEAD_MIN_LINES = 3
_LATE_LEAD_MAX_GAP = 1.8  # in font sizes; lead leading is about 1.55


def _demote_late_leads(
    classified: list[tuple[Kind, RawLine]],
) -> list[tuple[Kind, RawLine]]:
    """Drop lead-styled lines after the first body line that are not prose.

    The lead sits between the title and the running text. Lead-sized lines
    after body text that hold no letters are infographic labels (for example
    years in a chart). Lead-sized lines that stand alone or in a pair (map
    labels) are labels too. A run of at least three consecutive lines is a
    mid-chapter intro; it stays in the stream as lead lines and
    :func:`_main_flow` gives it its own block.

    >>> def lead(text: str, y: float, page: int = 2) -> tuple[Kind, RawLine]:
    ...     span = RawSpan(text, "EuclidCircularB-Regular", 12.5)
    ...     return Kind.LEAD, RawLine(page, 57.0, y, (span,))
    >>> body = (Kind.BODY, RawLine(1, 57.0, 10.0, (RawSpan("T", "Practice-Regular", 10.4),)))
    >>> kept = _demote_late_leads([body, lead("A", 108), lead("B", 127), lead("C", 147)])
    >>> [line.text for _, line in kept[1:]]
    ['A', 'B', 'C']
    >>> len(_demote_late_leads([body, lead("A", 108), lead("B", 300)]))
    1
    """
    seen_body = False
    result: list[tuple[Kind, RawLine]] = []
    group: list[tuple[Kind, RawLine]] = []

    def flush() -> None:
        if len(group) >= _LATE_LEAD_MIN_LINES:
            result.extend(group)
        group.clear()

    for kind, line in classified:
        late_prose = kind is Kind.LEAD and seen_body and any(c.isalpha() for c in line.text)
        if kind is Kind.BODY:
            seen_body = True
        if kind is Kind.LEAD and seen_body:
            if not late_prose:
                continue
            if group:
                last = group[-1][1]
                size = dominant_span(last).size
                if line.page != last.page or line.y0 - last.y0 > _LATE_LEAD_MAX_GAP * size:
                    flush()
            group.append((kind, line))
            continue
        flush()
        result.append((kind, line))
    flush()
    return result


_BYLINE_MAX_CHARS = 100
_BYLINE_MAX_Y = 90.0  # the byline sits at the top of the first page


def _drop_opener_byline(
    classified: list[tuple[Kind, RawLine]],
) -> list[tuple[Kind, RawLine]]:
    """Drop the short author line of the chapter's first page.

    The chapter header already shows the authors, so a short sidebar line on the
    first page only repeats them.

    >>> font = RawSpan("Guido Lassau", "EuclidCircularB-Regular", 8.5)
    >>> byline = RawLine(1, 113.0, 62.0, (font,))
    >>> _drop_opener_byline([(Kind.ASIDE_BODY, byline)])
    []
    >>> long = RawLine(1, 113.0, 62.0, (RawSpan("x" * 120, "EuclidCircularB-Regular", 8.5),))
    >>> len(_drop_opener_byline([(Kind.ASIDE_BODY, long)]))
    1
    """
    if not classified:
        return classified
    first_page = classified[0][1].page
    opener = [
        item
        for item in classified
        if item[0] is Kind.ASIDE_BODY and item[1].page == first_page and item[1].y0 < _BYLINE_MAX_Y
    ]
    if sum(len(line.text.strip()) for _, line in opener) >= _BYLINE_MAX_CHARS:
        return classified
    return [item for item in classified if item not in opener]


def _promote_subheadings(
    classified: list[tuple[Kind, RawLine]],
) -> list[tuple[Kind, RawLine]]:
    """Turn in-text subheadings, wrongly read as sidebar titles, into SUBHEAD.

    A sidebar title is followed by sidebar text. An in-text subheading is
    followed by body text in the same column.

    >>> font = "EuclidCircularB-Semibold"
    >>> head = RawLine(1, 114.0, 40.0, (RawSpan("Zünfte", font, 9.5),))
    >>> span = RawSpan("Im Verlauf", "Practice-Regular", 10.4)
    >>> text = RawLine(1, 113.0, 63.0, (span,))
    >>> result = _promote_subheadings([(Kind.ASIDE_HEAD, head), (Kind.BODY, text)])
    >>> [kind.value for kind, _ in result]
    ['subhead', 'body']
    >>> _promote_subheadings([(Kind.ASIDE_HEAD, head)])[0][0].value
    'aside_head'
    """
    result: list[tuple[Kind, RawLine]] = []
    for kind, line in classified:
        if kind is Kind.ASIDE_HEAD:
            below = [
                (other_kind, other)
                for other_kind, other in classified
                if other.page == line.page
                and other.y0 > line.y0
                and abs(other.x0 - line.x0) < _COLUMN_TOLERANCE
                and other_kind is not Kind.ASIDE_HEAD
            ]
            if below and min(below, key=lambda item: item[1].y0)[0] is Kind.BODY:
                kind = Kind.SUBHEAD
        result.append((kind, line))
    return result


def _marker_counts(blocks: Sequence[Block]) -> dict[int, int]:
    """Count the footnote markers per number, including markers inside asides."""
    counts: dict[int, int] = {}
    for block in blocks:
        if isinstance(block, Paragraph):
            for inline in block.inlines:
                if isinstance(inline, Marker):
                    counts[inline.number] = counts.get(inline.number, 0) + 1
        elif isinstance(block, Aside):
            for number, count in _marker_counts(block.blocks).items():
                counts[number] = counts.get(number, 0) + count
    return counts


def _prune_orphan_notes(
    notes: list[Note],
    blocks: Sequence[Block],
    warnings: list[str],
    dropped_markers: Collection[int] = (),
) -> list[Note]:
    """Remove endnotes that repeat another note and have no marker of their own.

    The k-th note with number N needs a k-th marker with number N. A note
    without one is an orphan. It is dropped only when its text repeats an
    earlier kept note (a typesetting leftover in the PDF). Other orphans stay
    in the output and raise a warning, so no real endnote disappears silently.
    A note whose marker the book prints in dropped text (a figure or table
    caption) stays without a backlink and raises no warning.
    """
    markers = _marker_counts(blocks)
    seen: dict[int, int] = {}
    kept: list[Note] = []
    for note in notes:
        occurrence = seen.get(note.number, 0) + 1
        seen[note.number] = occurrence
        if occurrence <= markers.get(note.number, 0):
            kept.append(note)
        elif any(note.text == earlier.text for earlier in kept):
            warnings.append(
                f"orphan endnote {note.number} dropped (duplicate text): {note.text[:60]!r}"
            )
        elif note.number in dropped_markers:
            kept.append(Note(note.number, note.text, linked=False))
        else:
            warnings.append(f"endnote {note.number} has no marker: {note.text[:60]!r}")
            kept.append(note)
    return kept


def _strip_heading_hyphens(block: Block) -> None:
    """Remove soft hyphens left in headings after multi-line merging."""
    if isinstance(block, Heading):
        block.text = _polish(block.text)
    elif isinstance(block, Aside):
        for child in block.blocks:
            _strip_heading_hyphens(child)
