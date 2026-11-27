"""Build review packs: ledger, lint findings and page images for one chapter.

A pack gives a reviewer (human or agent) everything needed to compare the
extracted HTML with its PDF. The functions here are pure, except
:func:`build_pack`, which reads a PDF and writes files.
"""

from __future__ import annotations

import csv
import hashlib
import io
import json
import re
import unicodedata
from collections import Counter
from collections.abc import Sequence
from dataclasses import asdict, dataclass, field
from html.parser import HTMLParser
from pathlib import Path
from typing import Literal, get_args

import pymupdf
from pydantic import BaseModel, Field

from sgb_html.extract import (
    Kind,
    LinePlan,
    dominant_span,
    iter_raw_lines,
    plan_lines,
)

DEFAULT_DPI = 110
ClassHint = Literal[
    "lost-text",
    "duplicated-text",
    "caption-in-prose",
    "reading-order",
    "broken-word",
    "spacing",
    "heading-or-lead",
    "endnote-alignment",
    "special-character",
    "other",
]
"""The fixed vocabulary for the class of a finding."""
CLASS_HINTS: tuple[str, ...] = get_args(ClassHint)


@dataclass(frozen=True)
class LedgerRow:
    """One PDF line and the extractor's decision about it."""

    page: int
    x0: float
    y0: float
    size: float
    font: str
    kind: Kind
    text: str
    clean: str = ""
    """The text the extractor can reach: figure anchors such as [7] are left out."""

    @property
    def kept(self) -> bool:
        return self.kind is not Kind.DROP


def ledger_rows(plan: LinePlan) -> list[LedgerRow]:
    """One row per PDF line, in PDF order, with the final role of the line.

    >>> from sgb_html.extract import RawLine, RawSpan
    >>> line = RawLine(3, 57.0, 100.0, (RawSpan("Text", "Practice-Regular", 10.4),))
    >>> plan = plan_lines([line])
    >>> [(row.page, row.kind.value, row.kept) for row in ledger_rows(plan)]
    [(3, 'body', True)]
    """
    rows: list[LedgerRow] = []
    for line, kind in plan.decisions:
        span = dominant_span(line)
        # Small series-font spans in running text are figure anchors; the extractor drops them.
        drops_anchors = kind is not Kind.NOTE and not plan.appendix
        clean = "".join(
            s.text
            for s in line.spans
            if not (
                drops_anchors
                and s.size < 8
                and not s.superscript
                and s.font.startswith("EuclidCircularB")
            )
        )
        rows.append(
            LedgerRow(line.page, line.x0, line.y0, span.size, span.font, kind, line.text, clean)
        )
    return rows


def ledger_tsv(rows: Sequence[LedgerRow]) -> str:
    """Render the ledger as tab-separated text, one row per PDF line."""
    out = io.StringIO()
    writer = csv.writer(out, delimiter="\t", lineterminator="\n")
    writer.writerow(["page", "x", "y", "size", "font", "kind", "kept", "text"])
    for row in rows:
        text = row.text.replace("\t", " ").replace("\n", " ")
        writer.writerow(
            [
                row.page,
                f"{row.x0:.0f}",
                f"{row.y0:.0f}",
                f"{row.size:.1f}",
                row.font,
                row.kind.value,
                "kept" if row.kept else "dropped",
                text,
            ]
        )
    return out.getvalue()


@dataclass
class HtmlBlock:
    """One block element of the chapter HTML, in document order."""

    index: int
    tag: str
    css: str
    text: str
    in_aside: bool
    marker_ids: list[str] = field(default_factory=list)
    note_id: str = ""
    backlink: str = ""
    value: str = ""


class _BlockParser(HTMLParser):
    """Collect p, h1-h4 and li blocks with their markers and note ids."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.blocks: list[HtmlBlock] = []
        self._open: HtmlBlock | None = None
        self._aside = 0
        self._in_body = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = {key: value or "" for key, value in attrs}
        if tag == "body":
            self._in_body = True
        elif tag == "aside":
            self._aside += 1
        elif self._in_body and tag in {"p", "h1", "h2", "h3", "h4", "li"}:
            self._open = HtmlBlock(
                len(self.blocks), tag, values.get("class", ""), "", self._aside > 0
            )
            self._open.note_id = values.get("id", "") if tag == "li" else ""
            self._open.value = values.get("value", "")
        elif tag == "sup" and self._open is not None and values.get("id"):
            self._open.marker_ids.append(values["id"])
        elif tag == "a" and self._open is not None and values.get("role") == "doc-backlink":
            self._open.backlink = values.get("href", "")

    def handle_endtag(self, tag: str) -> None:
        if tag == "aside":
            self._aside -= 1
        elif self._open is not None and tag == self._open.tag:
            self._open.text = self._open.text.strip()
            self.blocks.append(self._open)
            self._open = None

    def handle_data(self, data: str) -> None:
        if self._open is not None:
            self._open.text += data


def parse_blocks(html: str) -> list[HtmlBlock]:
    """Parse chapter HTML into ordered blocks.

    >>> doc = '<body><h2>Titel</h2><p class="lead">Ein Satz.</p></body>'
    >>> [(b.tag, b.css, b.text) for b in parse_blocks(doc)]
    [('h2', '', 'Titel'), ('p', 'lead', 'Ein Satz.')]
    """
    parser = _BlockParser()
    parser.feed(html)
    return parser.blocks


@dataclass(frozen=True)
class LintFinding:
    """A static finding with a locator into the HTML or the ledger."""

    code: str
    locator: str
    detail: str
    page: int | None = None


_URL_BREAK = re.compile(r"https?://\S*[/=_-] [a-z0-9.-]+[/.]\w\S*")
_CONTROL = re.compile("[\xad\x00-\x08\x0b\x0c\x0e-\x1f]")


def _locator(block: HtmlBlock) -> str:
    return f"{block.tag}#{block.index}"


def lint_html(html: str) -> list[LintFinding]:
    """Static checks on the chapter HTML that need no PDF.

    A note without a backlink is intentional (the marker sits in a dropped
    caption), and a sidebar may reuse a note number, so neither is a finding.
    """
    blocks = parse_blocks(html)
    findings: list[LintFinding] = []
    markers = Counter(m for block in blocks for m in block.marker_ids)
    note_ids = {block.note_id for block in blocks if block.note_id}
    for marker in sorted(markers):
        if marker.removeprefix("ref-") not in note_ids:
            findings.append(LintFinding("marker-without-note", marker, "no endnote has this id"))
    for block in blocks:
        if block.backlink and block.backlink.removeprefix("#") not in markers:
            findings.append(
                LintFinding("note-without-marker", block.note_id, "backlink target is missing")
            )
    previous: HtmlBlock | None = None
    seen_text: dict[str, int] = {}
    seen_lead = False
    for block in blocks:
        text = block.text
        where = _locator(block)
        if block.tag == "p":
            findings.extend(_lint_paragraph(block, previous, seen_text, seen_lead))
            seen_lead = seen_lead or block.css == "lead"
        if "  " in text:
            findings.append(LintFinding("double-space", where, repr(text[:60])))
        if _CONTROL.search(text):
            findings.append(LintFinding("control-character", where, repr(text[:60])))
        url = _URL_BREAK.search(text)
        if url:
            findings.append(LintFinding("url-space", where, url.group(0)))
        previous = block
    body = " ".join(block.text for block in blocks)
    if abs(body.count("«") - body.count("»")) > 1:
        findings.append(
            LintFinding("guillemets", "chapter", f"{body.count('«')} open, {body.count('»')} close")
        )
    return findings


def _lint_paragraph(
    block: HtmlBlock, previous: HtmlBlock | None, seen_text: dict[str, int], seen_lead: bool
) -> list[LintFinding]:
    """Findings for one paragraph: size, repeats and lead placement."""
    text = block.text
    where = _locator(block)
    found: list[LintFinding] = []
    if len(text.split()) <= 1:
        found.append(LintFinding("short-paragraph", where, repr(text[:40])))
    elif len(text) > 40:
        if text in seen_text:
            found.append(
                LintFinding("repeated-paragraph", where, f"same as block {seen_text[text]}")
            )
        seen_text.setdefault(text, block.index)
    if block.css == "lead":
        if not any(c.isalpha() for c in text) or re.search(r"\d\s*$", text):
            found.append(LintFinding("lead-ends-in-number", where, text[-40:]))
        # A mid-chapter lead follows its section heading; a second opener paragraph follows a lead.
        late = seen_lead and previous is not None and previous.css != "lead"
        if late and previous is not None and previous.tag not in {"h2", "h3"}:
            found.append(
                LintFinding("lead-without-heading", where, f"follows {_locator(previous)}")
            )
    return found


def lint_ledger(rows: Sequence[LedgerRow]) -> list[LintFinding]:
    """Checks on the extractor's decisions: dropped lines that look like body text."""
    findings: list[LintFinding] = []
    for row in rows:
        where = f"p{row.page} y{row.y0:.0f}"
        looks_like_body = (
            row.font.startswith("Practice")
            and "Extrabold" not in row.font  # pull quotes repeat body text on purpose
            and 9.5 <= row.size <= 11.5
        )
        if not row.kept and looks_like_body and len(row.text.split()) >= 3:
            findings.append(LintFinding("dropped-body-text", where, row.text[:60], row.page))
        if row.kind is Kind.LEAD and not any(c.isalpha() for c in row.text):
            findings.append(LintFinding("lead-without-letters", where, row.text[:40], row.page))
    return findings


def blocks_tsv(html: str) -> str:
    """Index of the HTML blocks, so a reviewer can give a locator such as ``p#42``.

    >>> blocks_tsv("<body><h2>Titel</h2></body>").splitlines()[1].split()
    ['h2#0', 'h2', 'Titel']
    """
    out = io.StringIO()
    writer = csv.writer(out, delimiter="\t", lineterminator="\n")
    writer.writerow(["block", "tag", "class", "aside", "text"])
    for block in parse_blocks(html):
        text = " ".join(block.text.split())
        writer.writerow(
            [_locator(block), block.tag, block.css, "aside" if block.in_aside else "", text[:120]]
        )
    return out.getvalue()


def word_count(html: str) -> int:
    """Number of words in the chapter body, endnotes excluded.

    >>> word_count("<body><p>Eins zwei</p><h2>drei</h2></body>")
    3
    """
    return sum(len(block.text.split()) for block in parse_blocks(html) if block.tag != "li")


_WINDOW = 18
_REPEAT_WINDOW = 30


def _norm(text: str) -> str:
    """Letters and digits only, lower case: a form that survives line breaks and spacing."""
    return "".join(c for c in unicodedata.normalize("NFKC", text).lower() if c.isalnum())


def _words(text: str) -> list[str]:
    return re.findall(r"[^\W\d_]+", unicodedata.normalize("NFKC", text).lower())


def _content_blocks(blocks: Sequence[HtmlBlock]) -> list[HtmlBlock]:
    """Drop the header blocks (title, authors, citation) that the PDF text layer does not hold."""
    skip = 0
    for index, block in enumerate(blocks[:5]):
        if block.tag == "p" and block.text.startswith("In: "):
            skip = index + 1
    return [b for b in blocks[skip:] if b.tag != "h1"]


def align(rows: Sequence[LedgerRow], html: str, *, appendix: bool = False) -> list[LintFinding]:
    """Compare the HTML with the PDF text layer, without the page images.

    The checks find candidates. A reviewer confirms each one against the page image.

    - ``pdf-line-missing``: a line that the extractor kept is not in the HTML.
    - ``html-not-in-pdf``: HTML text that no kept PDF line holds, for example spliced
      caption text or a repeated sentence.
    - ``block-order``: a paragraph that stands before text that comes earlier in the PDF.
    - ``split-word``: a space inside a word that the PDF writes as one word.
    - ``note-mismatch``: an endnote whose number and text are not in the PDF.
    """
    blocks = _content_blocks(parse_blocks(html))
    html_all = "".join(_norm(b.text) for b in blocks)
    kept = [r for r in rows if r.kept and r.kind is not Kind.TITLE]
    aside_kinds = {Kind.ASIDE_HEAD, Kind.ASIDE_BODY}
    notes = [r for r in kept if r.kind is Kind.NOTE]
    asides = [r for r in kept if r.kind in aside_kinds]
    main = [r for r in kept if r.kind is not Kind.NOTE and r.kind not in aside_kinds]
    if not appendix:  # the extractor reads the running text top to bottom on each page
        main.sort(key=lambda r: (r.page, r.y0))
    pdf_main = "".join(_norm(r.clean) for r in main)
    pdf_aside = "".join(_norm(r.clean) for r in asides)
    pdf_notes = "".join(_norm(r.clean) for r in notes)
    findings: list[LintFinding] = []

    missing: dict[int, list[str]] = {}
    for row in kept:
        text = re.sub(r"^\s*\d+\s+", "", row.clean) if row.kind is Kind.NOTE else row.clean
        norm = _norm(text)
        if len(norm) >= 12 and norm not in html_all:
            missing.setdefault(row.page, []).append(text.strip())
    for page, lines in missing.items():
        findings.append(
            LintFinding(
                "pdf-line-missing",
                f"p{page}",
                f"{len(lines)} lines: {' / '.join(lines)[:160]}",
                page,
            )
        )

    for block in blocks:
        if block.tag == "li":
            continue
        norm = _norm(block.text)
        windows = [norm[i : i + _WINDOW] for i in range(0, max(1, len(norm) - _WINDOW + 1), 6)]
        bad = [
            w
            for w in windows
            if len(w) == _WINDOW and not any(w in s for s in (pdf_main, pdf_aside, pdf_notes))
        ]
        if bad:
            findings.append(
                LintFinding(
                    "html-not-in-pdf", _locator(block), f"{len(bad)} windows: {block.text[:80]!r}"
                )
            )

    previous = -1
    for block in blocks:
        norm = _norm(block.text)
        if appendix or block.tag != "p" or block.in_aside or len(norm) < 25:
            continue
        position = pdf_main.find(norm[:25])
        if position < 0:
            continue
        if position < previous:
            findings.append(LintFinding("block-order", _locator(block), block.text[:60]))
        previous = position

    stream = "".join(
        r.clean.replace("\xad", "") if r.clean.rstrip().endswith("\xad") else r.clean + " "
        for r in kept
    )
    vocabulary = set(_words(stream))
    for block in blocks:
        tokens = re.findall(r"[^\W\d_]+|\W+", block.text)
        for first, space, second in zip(tokens, tokens[1:], tokens[2:], strict=False):
            joined = (first + second).lower()
            if (
                space == " "
                and first.isalpha()
                and second.isalpha()
                and len(joined) >= 8
                and joined in vocabulary
                and (first.lower() not in vocabulary or second.lower() not in vocabulary)
            ):
                findings.append(LintFinding("split-word", _locator(block), f"{first} {second}"))

    for block in blocks:
        match = re.fullmatch(r"fn(\d+)", block.note_id) if block.tag == "li" else None
        if match and _norm(match.group(1) + block.text) not in pdf_notes:
            findings.append(LintFinding("note-mismatch", block.note_id, block.text[:80]))
    return findings


def render_pages(pdf_path: Path, out_dir: Path, dpi: int = DEFAULT_DPI) -> int:
    """Write one PNG per PDF page (``p01.png``, ``p02.png``, ...); return the page count."""
    out_dir.mkdir(parents=True, exist_ok=True)
    with pymupdf.open(pdf_path) as doc:
        for number, page in enumerate(doc, start=1):
            page.get_pixmap(dpi=dpi).save(out_dir / f"p{number:02d}.png")
        return len(doc)


def build_pack(
    pdf_path: Path,
    html_path: Path,
    out_dir: Path,
    *,
    doi: str,
    git_sha: str = "",
    dpi: int = DEFAULT_DPI,
) -> dict[str, object]:
    """Write the review pack for one chapter into ``out_dir`` and return its manifest."""
    out_dir.mkdir(parents=True, exist_ok=True)
    html = html_path.read_text(encoding="utf-8")
    with pymupdf.open(pdf_path) as doc:
        raw_lines = list(iter_raw_lines(doc))
        pdf_words = sum(len(page.get_text().split()) for page in doc)
    plan = plan_lines(raw_lines)
    rows = ledger_rows(plan)
    findings = lint_html(html) + lint_ledger(rows)
    candidates = align(rows, html, appendix=plan.appendix)
    pages = render_pages(pdf_path, out_dir / "pages", dpi)
    (out_dir / "chapter.html").write_text(html, encoding="utf-8")
    (out_dir / "ledger.tsv").write_text(ledger_tsv(rows), encoding="utf-8")
    (out_dir / "blocks.tsv").write_text(blocks_tsv(html), encoding="utf-8")
    (out_dir / "lint.json").write_text(
        json.dumps([asdict(f) for f in findings], ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (out_dir / "diff.json").write_text(
        json.dumps([asdict(f) for f in candidates], ensure_ascii=False, indent=2), encoding="utf-8"
    )
    html_words = word_count(html)
    manifest: dict[str, object] = {
        "doi": doi,
        "pdf": str(pdf_path),
        "pages": pages,
        "dpi": dpi,
        "git_sha": git_sha,
        "html_sha256": hashlib.sha256(html.encode("utf-8")).hexdigest(),
        "html_words": html_words,
        "pdf_words": pdf_words,
        "word_ratio": round(html_words / pdf_words, 3) if pdf_words else 0.0,
        "extractor_warnings": plan.warnings,
        "lint_findings": len(findings),
        "diff_candidates": len(candidates),
        "flagged_pages": sorted({f.page for f in findings + candidates if f.page is not None}),
        "appendix": plan.appendix,
    }
    (out_dir / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return manifest


class Finding(BaseModel):
    """One reviewer finding. The reviewer quotes both sides."""

    severity: Literal["blocker", "major", "minor"]
    html_locator: str
    quote_html: str
    quote_pdf: str
    pdf_page: int = Field(ge=1)
    confidence: Literal["high", "medium", "low"]
    class_hint: ClassHint


class FindingsFile(BaseModel):
    """The file a reviewer writes: findings plus the pages it did and did not inspect."""

    doi: str
    findings: list[Finding]
    pages_inspected: list[int]
    pages_skipped: list[int]


def load_findings(path: Path) -> FindingsFile:
    """Read and validate a reviewer's ``findings.json``; raise on a bad file."""
    return FindingsFile.model_validate_json(path.read_text(encoding="utf-8"))
