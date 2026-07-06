"""Render extracted chapter content as a minimal, self-contained HTML document."""

from __future__ import annotations

from dataclasses import dataclass, field
from html import escape

from sgb_html.extract import Aside, Chapter, Heading, Marker, Note, Paragraph, TextRun


@dataclass(frozen=True)
class ChapterMeta:
    """Chapter metadata as recorded in Open Monograph Press."""

    title: str
    subtitle: str = ""
    authors: tuple[str, ...] = ()
    doi: str = ""
    pages: str = ""
    license_url: str = ""
    volume_title: str = ""
    url_published: str = ""


@dataclass
class _NoteIds:
    """Stable ids even when sidebar notes reuse main-text numbers."""

    counts: dict[int, int] = field(default_factory=dict)

    def next_id(self, number: int) -> str:
        occurrence = self.counts.get(number, 0)
        self.counts[number] = occurrence + 1
        return f"fn{number}" if occurrence == 0 else f"fn{number}-{occurrence + 1}"


def _meta(name: str, content: str) -> str:
    return f'<meta name="{escape(name, quote=True)}" content="{escape(content, quote=True)}">'


def _inlines_html(paragraph: Paragraph, refs: _NoteIds) -> str:
    parts: list[str] = []
    for inline in paragraph.inlines:
        if isinstance(inline, TextRun):
            text = escape(inline.text)
            parts.append(f"<em>{text}</em>" if inline.italic else text)
        elif isinstance(inline, Marker):
            note_id = refs.next_id(inline.number)
            parts.append(f'<sup id="ref-{note_id}"><a href="#{note_id}">{inline.number}</a></sup>')
    return "".join(parts)


def _block_html(block: Paragraph | Heading | Aside, refs: _NoteIds) -> str:
    if isinstance(block, Heading):
        return f"<h{block.level}>{escape(block.text)}</h{block.level}>"
    if isinstance(block, Paragraph):
        tag = '<p class="lead">' if block.lead else "<p>"
        return f"{tag}{_inlines_html(block, refs)}</p>"
    inner = "\n".join(_block_html(child, refs) for child in block.blocks)
    return f"<aside>\n{inner}\n</aside>"


def _notes_html(notes: list[Note], ids: _NoteIds) -> str:
    items: list[str] = []
    for note in notes:
        note_id = ids.next_id(note.number)
        backlink = f' <a href="#ref-{note_id}" role="doc-backlink">↑</a>'
        items.append(f'<li id="{note_id}" value="{note.number}">{escape(note.text)}{backlink}</li>')
    body = "\n".join(items)
    return f'<section role="doc-endnotes">\n<h2>Anmerkungen</h2>\n<ol>\n{body}\n</ol>\n</section>'


def render_chapter(chapter: Chapter, meta: ChapterMeta) -> str:
    """Build the complete minimal HTML document for one chapter."""
    title = meta.title or chapter.title
    doc_title = f"{title} – {meta.volume_title}" if meta.volume_title else title
    head_lines = [
        '<meta charset="utf-8">',
        '<meta name="viewport" content="width=device-width, initial-scale=1">',
        f"<title>{escape(doc_title)}</title>",
        _meta("citation_title", title),
        _meta("citation_language", "de"),
    ]
    for author in meta.authors:
        head_lines.append(_meta("citation_author", author))
    if meta.volume_title:
        head_lines.append(_meta("citation_inbook_title", meta.volume_title))
    if meta.doi:
        head_lines.append(_meta("citation_doi", meta.doi))
    if meta.pages and "-" in meta.pages:
        first, _, last = meta.pages.partition("-")
        head_lines.append(_meta("citation_firstpage", first))
        head_lines.append(_meta("citation_lastpage", last))
    if meta.license_url:
        head_lines.append(_meta("DC.rights", meta.license_url))

    header_lines = [f"<h1>{escape(title)}</h1>"]
    if meta.subtitle:
        header_lines.append(f"<p>{escape(meta.subtitle)}</p>")
    if meta.authors:
        header_lines.append(f'<p class="authors">{escape(", ".join(meta.authors))}</p>')
    info: list[str] = []
    if meta.volume_title:
        volume = escape(meta.volume_title)
        if meta.url_published:
            volume = f'<a href="{escape(meta.url_published, quote=True)}">{volume}</a>'
        info.append(f"In: {volume}")
    if meta.pages:
        info.append(f"S. {escape(meta.pages)}")
    if meta.doi:
        doi_url = f"https://doi.org/{meta.doi}"
        info.append(f'DOI: <a href="{escape(doi_url, quote=True)}">{escape(meta.doi)}</a>')
    if meta.license_url:
        info.append(f'Lizenz: <a href="{escape(meta.license_url, quote=True)}">CC BY-NC 4.0</a>')
    if info:
        header_lines.append(f'<p class="meta">{" | ".join(info)}</p>')

    refs = _NoteIds()
    main = "\n".join(_block_html(block, refs) for block in chapter.blocks)
    notes = _notes_html(chapter.notes, _NoteIds()) if chapter.notes else ""

    parts = [
        "<!DOCTYPE html>",
        '<html lang="de">',
        "<head>",
        *head_lines,
        "</head>",
        "<body>",
        "<header>",
        *header_lines,
        "</header>",
        "<main>",
        main,
        "</main>",
    ]
    if notes:
        parts.append(notes)
    parts.extend(["</body>", "</html>", ""])
    return "\n".join(parts)
