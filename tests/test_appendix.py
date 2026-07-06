"""Unit tests for appendix (Anhang) chapter extraction."""

from __future__ import annotations

from conftest import FakePage, line, span

from sgb_html.extract import (
    Heading,
    Kind,
    Paragraph,
    RawLine,
    RawSpan,
    TextRun,
    classify_appendix,
    extract_chapter,
)


def raw(text: str, font: str, size: float) -> RawLine:
    return RawLine(1, 30.0, 100.0, (RawSpan(text, font, size),))


def entry(text: str, *, x0: float = 28.0, y0: float = 100.0) -> dict:
    return line(span(text, "EuclidCircularB-Regular", 6.5), x0=x0, y0=y0)


def texts(chapter_blocks: list) -> list[str]:
    result = []
    for block in chapter_blocks:
        if isinstance(block, Heading):
            result.append(f"h{block.level}:{block.text}")
        else:
            assert isinstance(block, Paragraph)
            result.append("".join(i.text for i in block.inlines if isinstance(i, TextRun)))
    return result


class TestClassifyAppendix:
    def test_styles(self) -> None:
        euclid = "EuclidCircularB-Regular"
        semibold = "EuclidCircularB-Semibold"
        assert classify_appendix(raw("Anhang", semibold, 32.0)) is Kind.DROP
        assert classify_appendix(raw("Bildnachweis", semibold, 9.5)) is Kind.HEADING
        assert classify_appendix(raw("Band 1 ist das Ergebnis", euclid, 9.0)) is Kind.BODY
        assert classify_appendix(raw("280", semibold, 7.0)) is Kind.DROP
        assert classify_appendix(raw("Quellen", semibold, 6.5)) is Kind.ASIDE_HEAD
        assert classify_appendix(raw("25\t Quelle: ABBS", semibold, 6.0)) is Kind.ENTRY
        assert classify_appendix(raw("Ammianus: Römische Geschichte", euclid, 6.5)) is Kind.ENTRY
        assert classify_appendix(raw("winzig", euclid, 3.8)) is Kind.DROP
        assert classify_appendix(raw("fremd", "Helvetica", 9.0)) is Kind.UNKNOWN
        assert (
            classify_appendix(raw("https://doi.org/10.21255/sgb-01.07", euclid, 6.5)) is Kind.DROP
        )


class TestAppendixFlow:
    def test_sections_entries_and_hanging_indents(self) -> None:
        page = FakePage(
            [
                line(span("Quellen- und", "EuclidCircularB-Semibold", 9.5), x0=28, y0=26),
                line(span("Literaturverzeichnis", "EuclidCircularB-Semibold", 9.5), x0=28, y0=40),
                line(span("Quellen", "EuclidCircularB-Semibold", 6.5), x0=28, y0=54),
                entry("Ammianus, Marcellinus: Römische", x0=28.3, y0=64),
                entry("Geschichte, Berlin 1968.", x0=37.2, y0=73),
                entry("Caesar: Der gallische Krieg,", x0=28.3, y0=82),
                entry("Zürich 1999.", x0=37.2, y0=91),
            ]
        )
        chapter = extract_chapter([page])
        assert texts(chapter.blocks) == [
            "h2:Quellen- und Literaturverzeichnis",
            "h3:Quellen",
            "Ammianus, Marcellinus: Römische Geschichte, Berlin 1968.",
            "Caesar: Der gallische Krieg, Zürich 1999.",
        ]
        assert chapter.notes == []

    def test_columns_read_in_order_and_entries_flow_across(self) -> None:
        page = FakePage(
            [
                entry("Rechte Spalte mit eigenem Eintrag.", x0=327.4, y0=64),
                entry("Linke Spalte beginnt einen Ein-", x0=28.3, y0=64),
                entry("trag, der fortgesetzt wird", x0=37.2, y0=73),
            ]
        )
        chapter = extract_chapter([page])
        first, second = chapter.blocks
        assert texts([first]) == ["Linke Spalte beginnt einen Ein-trag, der fortgesetzt wird"]
        assert texts([second]) == ["Rechte Spalte mit eigenem Eintrag."]

    def test_intro_prose_splits_on_vertical_gap(self) -> None:
        intro = "EuclidCircularB-Regular"
        page = FakePage(
            [
                line(span("Band 1 ist das Ergebnis einer", intro, 9.0), x0=56.8, y0=64),
                line(span("mehrjährigen Forschungsarbeit.", intro, 9.0), x0=56.8, y0=80),
                line(span("Ein neuer Absatz nach Lücke.", intro, 9.0), x0=56.8, y0=140),
            ]
        )
        chapter = extract_chapter([page])
        assert texts(chapter.blocks) == [
            "Band 1 ist das Ergebnis einer mehrjährigen Forschungsarbeit.",
            "Ein neuer Absatz nach Lücke.",
        ]

    def test_numbered_image_credits_are_entries(self) -> None:
        page = FakePage(
            [
                line(span("Bildnachweis", "EuclidCircularB-Semibold", 9.5), x0=28, y0=26),
                line(
                    span("\t 25\t Quelle: Digitale", "EuclidCircularB-Semibold", 6.0), x0=28, y0=64
                ),
                line(span("Archäologie, Freiburg", "EuclidCircularB-Semibold", 6.0), x0=37, y0=73),
                line(span("\t 26\t StABS, PA 88a", "EuclidCircularB-Semibold", 6.0), x0=28, y0=82),
            ]
        )
        chapter = extract_chapter([page])
        assert texts(chapter.blocks) == [
            "h2:Bildnachweis",
            "25 Quelle: Digitale Archäologie, Freiburg",
            "26 StABS, PA 88a",
        ]

    def test_unknown_appendix_style_warns_and_drops(self) -> None:
        page = FakePage(
            [
                entry("Echter Eintrag.", y0=100),
                line(span("OCR Fremdtext", "Helvetica", 9.0), y0=200),
            ]
        )
        chapter = extract_chapter([page])
        assert texts(chapter.blocks) == ["Echter Eintrag."]
        assert any("unknown appendix style Helvetica" in w for w in chapter.warnings)

    def test_title_is_extracted_but_not_repeated(self) -> None:
        page = FakePage(
            [
                line(span("Anhang", "EuclidCircularB-Semibold", 32.0), y0=26),
                entry("Ein Eintrag.", y0=100),
            ]
        )
        chapter = extract_chapter([page])
        assert chapter.title == "Anhang"
        assert texts(chapter.blocks) == ["Ein Eintrag."]

    def test_multiline_section_heading_merges_with_h3(self) -> None:
        page = FakePage(
            [
                line(span("Antike", "EuclidCircularB-Semibold", 6.5), x0=28, y0=54),
                line(span("Autoren", "EuclidCircularB-Semibold", 6.5), x0=28, y0=64),
                entry("Aristoteles 12, 47", x0=28.3, y0=80),
            ]
        )
        chapter = extract_chapter([page])
        assert texts(chapter.blocks) == ["h3:Antike Autoren", "Aristoteles 12, 47"]


def test_aside_only_page_flushes_before_next_page() -> None:
    """Covers the main flow: an aside page with no open paragraph."""
    from conftest import body

    first = FakePage(
        [line(span("Kastentext ohne Haupttext.", "EuclidCircularB-Regular", 8.5), y0=100)]
    )
    second = FakePage([body("Haupttext auf Folgeseite.", y0=100)])
    chapter = extract_chapter([first, second])
    kinds = [type(block).__name__ for block in chapter.blocks]
    assert kinds == ["Aside", "Paragraph"]
