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
from collections.abc import Iterable, Iterator, Sequence
from dataclasses import dataclass, field
from enum import Enum
from typing import Protocol

SOFT_HYPHEN = "­"

_DOI_RE = re.compile(r"https?://doi\.org/")
_NOTE_START_RE = re.compile(r"^\s*(\d+) ?\t\s*(.*)$")
_NOTE_COLUMN_GAP = 50.0
_PARAGRAPH_INDENT = 4.0
_MAX_PARAGRAPH_INDENT = 40.0


class Kind(Enum):
    """Semantic role of a PDF text line."""

    TITLE = "title"
    LEAD = "lead"
    HEADING = "heading"
    BODY = "body"
    ASIDE_HEAD = "aside_head"
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


@dataclass
class Chapter:
    """Structured chapter content, ready for rendering."""

    title: str = ""
    blocks: list[Block] = field(default_factory=list)
    notes: list[Note] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


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
                x0, y0 = line["bbox"][0], line["bbox"][1]
                yield RawLine(page=page_number, x0=x0, y0=y0, spans=spans)


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
    return max(candidates, key=lambda s: len(s.text.strip()))


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
        if 8.0 <= size < 8.8:
            return Kind.ASIDE_BODY
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
            continue
        digits = text.strip()
        if span.superscript and span.size < 7 and "Bold" in span.font and digits.isdigit():
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


def _append_line(inlines: list[Inline], line: RawLine, *, drop_anchors: bool = True) -> None:
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
        if isinstance(first, TextRun) and previous.italic == first.italic:
            inlines[-1] = TextRun(previous.text + glue + first.text, previous.italic)
            new_runs = new_runs[1:]
        elif glue:
            inlines[-1] = TextRun(previous.text + glue, previous.italic)
    elif isinstance(previous, Marker) and isinstance(first, TextRun):
        new_runs[0] = TextRun(" " + first.text, first.italic)
    inlines.extend(new_runs)


def _polish(text: str) -> str:
    """Final text cleanup: soft hyphens out, spacing before punctuation fixed.

    >>> _polish(f"exis{SOFT_HYPHEN}tierte  . Und")
    'existierte. Und'
    """
    text = text.replace(SOFT_HYPHEN, "")
    text = re.sub(r" {2,}", " ", text)
    return re.sub(r" ([.,;:])", r"\1", text)


def _finish(inlines: list[Inline]) -> list[Inline]:
    """Apply the final text cleanup to every run of a paragraph."""
    return [TextRun(_polish(i.text), i.italic) if isinstance(i, TextRun) else i for i in inlines]


def _starts_paragraph(line: RawLine, base: float, previous: RawLine | None) -> bool:
    """First-line indents (~25-30 pt) start paragraphs; larger x-shifts are
    text flowing around images or margin boxes and continue the paragraph."""
    deltas = [line.x0 - base]
    if previous is not None:
        deltas.append(line.x0 - previous.x0)
    return any(_PARAGRAPH_INDENT < delta <= _MAX_PARAGRAPH_INDENT for delta in deltas)


def _build_paragraphs(lines: Sequence[RawLine], *, lead: bool = False) -> list[Paragraph]:
    """Group consecutive lines into paragraphs using first-line indents."""
    paragraphs: list[Paragraph] = []
    base_x: dict[int, float] = {}
    for line in lines:
        base_x[line.page] = min(base_x.get(line.page, line.x0), line.x0)
    current: list[Inline] = []
    previous: RawLine | None = None
    for line in lines:
        if current and _starts_paragraph(line, base_x[line.page], previous):
            paragraphs.append(Paragraph(_finish(current), lead=lead))
            current = []
        _append_line(current, line)
        previous = line
    if current:
        paragraphs.append(Paragraph(_finish(current), lead=lead))
    return paragraphs


def _note_columns(lines: Sequence[RawLine]) -> list[list[RawLine]]:
    """Group endnote lines into columns per page, left to right."""
    columns: list[list[RawLine]] = []
    pages = sorted({line.page for line in lines})
    for page in pages:
        page_lines = sorted((li for li in lines if li.page == page), key=lambda li: li.x0)
        page_columns: list[list[RawLine]] = []
        for line in page_lines:
            if page_columns and line.x0 - page_columns[-1][0].x0 < _NOTE_COLUMN_GAP:
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
                joined = notes[-1].text + _glue(notes[-1].text, continuation) + continuation
                notes[-1] = Note(notes[-1].number, joined)
            else:
                warnings.append(
                    f"page {line.page}: endnote continuation without start: {line.text[:60]!r}"
                )
    return [Note(n.number, _polish(n.text)) for n in notes], warnings


def _build_aside(lines: list[tuple[Kind, RawLine]]) -> Aside:
    """Assemble one page's sidebar lines into an aside block."""
    lines.sort(key=lambda item: item[1].y0)
    aside_blocks: list[Paragraph | Heading] = []
    prose: list[RawLine] = []
    for kind, line in lines:
        if kind is Kind.ASIDE_HEAD:
            aside_blocks.extend(_build_paragraphs(prose))
            prose = []
            head = _plain_text(line)
            if aside_blocks and isinstance(aside_blocks[-1], Heading):
                aside_blocks[-1].text += _glue(aside_blocks[-1].text, head) + head
            else:
                aside_blocks.append(Heading(head, level=3))
        else:
            prose.append(line)
    aside_blocks.extend(_build_paragraphs(prose))
    return Aside(aside_blocks)


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
    pending_asides: list[Block] = []
    previous: RawLine | None = None

    def close_paragraph() -> None:
        nonlocal current
        if current:
            blocks.append(Paragraph(_finish(current)))
            current = []
        blocks.extend(pending_asides)
        pending_asides.clear()

    for page in sorted({line.page for _, line in classified}):
        main = [
            (kind, line)
            for kind, line in classified
            if line.page == page and kind in (Kind.HEADING, Kind.BODY)
        ]
        main.sort(key=lambda item: item[1].y0)
        for kind, line in main:
            if kind is Kind.HEADING:
                close_paragraph()
                heading_text = _plain_text(line)
                if blocks and isinstance(blocks[-1], Heading) and blocks[-1].level == 2:
                    blocks[-1].text += _glue(blocks[-1].text, heading_text) + heading_text
                else:
                    blocks.append(Heading(heading_text))
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
    return blocks


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
            if columns and line.x0 - columns[-1][0].x0 < _NOTE_COLUMN_GAP:
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


def extract_chapter(pages: Iterable[SupportsTextDict]) -> Chapter:
    """Turn a chapter PDF into structured, text-only content."""
    classified: list[tuple[Kind, RawLine]] = []
    warnings: list[str] = []
    raw_lines = list(iter_raw_lines(pages))
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
        if kind is not Kind.DROP:
            classified.append((kind, line))

    title_lines = [line for kind, line in classified if kind is Kind.TITLE]
    title = ""
    for line in title_lines:
        part = _plain_text(line)
        title = title + _glue(title, part) + part if title else part
    title = _polish(title)

    notes: list[Note] = []
    if any(kind in (Kind.BODY, Kind.LEAD) for kind, _ in classified):
        lead_lines = [line for kind, line in classified if kind is Kind.LEAD]
        blocks: list[Block] = list(_build_paragraphs(lead_lines, lead=True))
        blocks.extend(_main_flow(classified))
        note_lines = [line for kind, line in classified if kind is Kind.NOTE]
        notes, note_warnings = _build_notes(note_lines)
        warnings.extend(note_warnings)
    else:
        # No running text in the series body font: an appendix chapter.
        appendix: list[tuple[Kind, RawLine]] = []
        for line in raw_lines:
            kind = classify_appendix(line)
            if kind is Kind.UNKNOWN:
                span = dominant_span(line)
                warnings.append(
                    f"page {line.page}: unknown appendix style {span.font} "
                    f"{span.size:.1f}: {line.text[:60]!r}"
                )
                kind = Kind.DROP
            appendix.append((kind, line))
        blocks = _appendix_flow(appendix)

    for block in blocks:
        _strip_heading_hyphens(block)
    return Chapter(title=title, blocks=blocks, notes=notes, warnings=warnings)


def _strip_heading_hyphens(block: Block) -> None:
    """Remove soft hyphens left in headings after multi-line merging."""
    if isinstance(block, Heading):
        block.text = _polish(block.text)
    elif isinstance(block, Aside):
        for child in block.blocks:
            _strip_heading_hyphens(child)
