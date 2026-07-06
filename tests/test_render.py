"""Unit tests for the minimal HTML renderer."""

from __future__ import annotations

from sgb_html.extract import Aside, Chapter, Heading, Marker, Note, Paragraph, TextRun
from sgb_html.render import ChapterMeta, render_chapter

FULL_META = ChapterMeta(
    title="Naturraum",
    subtitle="Fels & Fluss",
    authors=("David Brönnimann", "Philippe Rentzel"),
    doi="10.21255/sgb-01.01-439115",
    pages="21-36",
    license_url="https://creativecommons.org/licenses/by-nc/4.0/",
    volume_title="Vor der Stadt <Band 1>",
    url_published="https://emono.unibas.ch/stadtgeschichtebasel/catalog/book/22",
)


def full_chapter() -> Chapter:
    return Chapter(
        title="Naturraum (PDF)",
        blocks=[
            Paragraph([TextRun("Der Lead.")], lead=True),
            Heading("Geologie als Grundstein"),
            Paragraph(
                [
                    TextRun("Der Buntsandstein,"),
                    Marker(1),
                    TextRun(" der "),
                    TextRun("rote", italic=True),
                    TextRun(" Stein <hart>."),
                ]
            ),
            Aside([Heading("Kasten", level=3), Paragraph([TextRun("Kastentext."), Marker(2)])]),
        ],
        notes=[Note(1, "Franke 1989."), Note(2, "Rentzel 2015 <ebd.>.")],
    )


class TestRenderChapter:
    def test_document_structure_and_metadata(self) -> None:
        html = render_chapter(full_chapter(), FULL_META)
        assert html.startswith('<!DOCTYPE html>\n<html lang="de">')
        assert "<title>Naturraum – Vor der Stadt &lt;Band 1&gt;</title>" in html
        assert '<meta name="citation_author" content="David Brönnimann">' in html
        assert '<meta name="citation_doi" content="10.21255/sgb-01.01-439115">' in html
        assert '<meta name="citation_firstpage" content="21">' in html
        assert '<meta name="citation_lastpage" content="36">' in html
        assert '<meta name="DC.rights"' in html
        assert "<h1>Naturraum</h1>" in html
        assert "<p>Fels &amp; Fluss</p>" in html
        assert "David Brönnimann, Philippe Rentzel" in html
        assert "S. 21-36" in html
        assert '<a href="https://doi.org/10.21255/sgb-01.01-439115">' in html
        assert "CC BY-NC 4.0" in html

    def test_body_blocks(self) -> None:
        html = render_chapter(full_chapter(), FULL_META)
        assert '<p class="lead">Der Lead.</p>' in html
        assert "<h2>Geologie als Grundstein</h2>" in html
        assert "<em>rote</em>" in html
        assert "Stein &lt;hart&gt;." in html
        assert "<aside>\n<h3>Kasten</h3>" in html
        assert '<sup id="ref-fn1"><a href="#fn1">1</a></sup>' in html

    def test_endnotes_section(self) -> None:
        html = render_chapter(full_chapter(), FULL_META)
        assert "<h2>Anmerkungen</h2>" in html
        assert '<li id="fn1" value="1">Franke 1989. <a href="#ref-fn1"' in html
        assert "Rentzel 2015 &lt;ebd.&gt;." in html

    def test_duplicate_note_numbers_get_unique_ids(self) -> None:
        chapter = Chapter(
            blocks=[
                Paragraph([TextRun("Erst"), Marker(5)]),
                Paragraph([TextRun("Zweit"), Marker(5)]),
            ],
            notes=[Note(5, "Haupttextnote."), Note(5, "Kastennote.")],
        )
        html = render_chapter(chapter, ChapterMeta(title="T"))
        assert '<sup id="ref-fn5"><a href="#fn5">5</a></sup>' in html
        assert '<sup id="ref-fn5-2"><a href="#fn5-2">5</a></sup>' in html
        assert '<li id="fn5" value="5">' in html
        assert '<li id="fn5-2" value="5">' in html

    def test_minimal_meta_falls_back_to_pdf_title(self) -> None:
        chapter = Chapter(title="PDF-Titel", blocks=[Paragraph([TextRun("Nur Text.")])])
        html = render_chapter(chapter, ChapterMeta(title=""))
        assert "<title>PDF-Titel</title>" in html
        assert "<h1>PDF-Titel</h1>" in html
        assert "citation_author" not in html
        assert "doc-endnotes" not in html
        assert "citation_firstpage" not in html
        assert '<p class="meta">' not in html

    def test_pages_without_range_skip_first_last(self) -> None:
        html = render_chapter(
            Chapter(blocks=[Paragraph([TextRun("x")])]),
            ChapterMeta(title="T", pages="99"),
        )
        assert "citation_firstpage" not in html
        assert "S. 99" in html

    def test_volume_without_url_is_plain_text(self) -> None:
        html = render_chapter(
            Chapter(blocks=[Paragraph([TextRun("x")])]),
            ChapterMeta(title="T", volume_title="Band 2"),
        )
        assert "In: Band 2" in html
        assert "In: <a" not in html
