"""Unit tests for the typography-driven chapter extractor."""

from __future__ import annotations

from conftest import FakePage, body, line, note_cont, note_start, span

from sgb_html.extract import (
    SOFT_HYPHEN,
    Aside,
    Heading,
    Kind,
    Marker,
    Paragraph,
    RawLine,
    RawSpan,
    TextRun,
    classify,
    extract_chapter,
    iter_raw_lines,
)


def raw(text: str, font: str, size: float, *, superscript: bool = False) -> RawLine:
    return RawLine(1, 55.0, 100.0, (RawSpan(text, font, size, superscript),))


def paragraph_text(block: object) -> str:
    assert isinstance(block, Paragraph)
    return "".join(i.text if isinstance(i, TextRun) else f"[{i.number}]" for i in block.inlines)


class TestIterRawLines:
    def test_skips_image_blocks_and_blank_lines(self) -> None:
        page = FakePage([body("Text"), line(span("   ", "Practice-Regular", 10.4))], image_blocks=2)
        lines = list(iter_raw_lines([page]))
        assert [entry.text for entry in lines] == ["Text"]
        assert lines[0].page == 1

    def test_missing_flags_default_to_regular(self) -> None:
        entry = {"bbox": (0, 0, 10, 10), "spans": [{"text": "x", "font": "F", "size": 10.0}]}
        (result,) = iter_raw_lines([FakePage([entry])])
        assert not result.spans[0].superscript


class TestClassify:
    def test_doi_line_dropped(self) -> None:
        assert (
            classify(raw("https://doi.org/10.21255/sgb-01.01", "EuclidCircularB-Regular", 9.5))
            is Kind.DROP
        )

    def test_practice_styles(self) -> None:
        assert classify(raw("Pull quote", "Practice-Extrabold", 11.0)) is Kind.DROP
        assert classify(raw("Lauftext", "Practice-Regular", 10.4)) is Kind.BODY
        assert classify(raw("Kursiv", "Practice-Italic", 10.4)) is Kind.BODY
        assert classify(raw("1\t Franke 1989.", "Practice-Bold", 5.5)) is Kind.NOTE
        assert classify(raw("7", "Practice-Bold", 5.2)) is Kind.DROP
        assert classify(raw("seltsam", "Practice-Regular", 7.0)) is Kind.UNKNOWN

    def test_euclid_styles(self) -> None:
        euclid = "EuclidCircularB-Regular"
        semibold = "EuclidCircularB-Semibold"
        assert classify(raw("Naturraum", semibold, 32.0)) is Kind.TITLE
        assert classify(raw("Geologie", semibold, 14.0)) is Kind.HEADING
        assert classify(raw("Lead", euclid, 12.5)) is Kind.LEAD
        assert classify(raw("Kastentitel", semibold, 9.5)) is Kind.ASIDE_HEAD
        assert classify(raw("Autorenzeile", euclid, 9.5)) is Kind.DROP
        assert classify(raw("Kastentext", euclid, 8.5)) is Kind.ASIDE_BODY
        assert classify(raw("Bildlegende", euclid, 7.5)) is Kind.DROP
        assert classify(raw("Anmerkungstext", euclid, 6.5)) is Kind.NOTE
        assert classify(raw("winzig", euclid, 3.8)) is Kind.DROP

    def test_unknown_font(self) -> None:
        assert classify(raw("Fremd", "Helvetica", 10.0)) is Kind.UNKNOWN

    def test_dominant_span_ignores_markers(self) -> None:
        entry = RawLine(
            1,
            55.0,
            100.0,
            (
                RawSpan("12", "Practice-Bold", 5.2, superscript=True),
                RawSpan("Der Lauftext dieser Zeile", "Practice-Regular", 10.4),
            ),
        )
        assert classify(entry) is Kind.BODY

    def test_all_superscript_line_falls_back(self) -> None:
        entry = RawLine(1, 55.0, 100.0, (RawSpan("12", "Practice-Bold", 5.2, superscript=True),))
        assert classify(entry) is Kind.DROP


class TestParagraphs:
    def test_lines_join_with_dehyphenation(self) -> None:
        page = FakePage(
            [
                body(f"Brönni{SOFT_HYPHEN}", y0=100),
                body("mann prägte rot-", y0=112),
                body("blaue Etiketten und", y0=124),
                body(f"Kalt- und {SOFT_HYPHEN}Warmzeiten.", y0=136),
            ]
        )
        chapter = extract_chapter([page])
        (block,) = chapter.blocks
        assert (
            paragraph_text(block)
            == "Brönnimann prägte rot-blaue Etiketten und Kalt- und Warmzeiten."
        )

    def test_indent_starts_new_paragraph(self) -> None:
        page = FakePage(
            [
                body("Erster Absatz endet hier.", y0=100),
                body("Zweiter Absatz beginnt eingerückt", x0=85.0, y0=112),
                body("und läuft weiter.", y0=124),
            ]
        )
        chapter = extract_chapter([page])
        first, second = chapter.blocks
        assert paragraph_text(first) == "Erster Absatz endet hier."
        assert paragraph_text(second) == "Zweiter Absatz beginnt eingerückt und läuft weiter."

    def test_markers_become_inline_references(self) -> None:
        page = FakePage(
            [
                line(
                    span("Der Buntsandstein,", "Practice-Regular", 10.4),
                    span("2", "Practice-Bold", 5.2, superscript=True),
                    span(" der rot ist.", "Practice-Regular", 10.4),
                )
            ]
        )
        chapter = extract_chapter([page])
        (block,) = chapter.blocks
        assert isinstance(block, Paragraph)
        assert block.inlines == [
            TextRun("Der Buntsandstein,"),
            Marker(2),
            TextRun(" der rot ist."),
        ]

    def test_marker_at_line_end_then_new_line(self) -> None:
        page = FakePage(
            [
                line(
                    span("Satzende.", "Practice-Regular", 10.4),
                    span("3", "Practice-Bold", 5.2, superscript=True),
                    y0=100,
                ),
                body("Neuer Satz.", y0=112),
            ]
        )
        chapter = extract_chapter([page])
        (block,) = chapter.blocks
        assert isinstance(block, Paragraph)
        assert block.inlines == [TextRun("Satzende."), Marker(3), TextRun(" Neuer Satz.")]

    def test_italic_runs_are_separated(self) -> None:
        page = FakePage(
            [
                line(
                    span("Die ", "Practice-Regular", 10.4),
                    span("Rotliegend Brekzie", "Practice-Italic", 10.4),
                    span(" ist hart.", "Practice-Regular", 10.4),
                )
            ]
        )
        chapter = extract_chapter([page])
        (block,) = chapter.blocks
        assert isinstance(block, Paragraph)
        assert block.inlines == [
            TextRun("Die "),
            TextRun("Rotliegend Brekzie", italic=True),
            TextRun(" ist hart."),
        ]

    def test_figure_anchor_spans_are_dropped(self) -> None:
        page = FakePage(
            [
                line(
                    span("nicht existierte ", "Practice-Regular", 10.4),
                    span("[7 | 8]", "EuclidCircularB-Semibold", 7.0),
                    span(". Die Eichenstämme", "Practice-Regular", 10.4),
                )
            ]
        )
        chapter = extract_chapter([page])
        (block,) = chapter.blocks
        assert paragraph_text(block) == "nicht existierte. Die Eichenstämme"

    def test_line_of_only_anchors_is_skipped(self) -> None:
        page = FakePage(
            [
                body("Text davor.", y0=100),
                line(span("[4]", "EuclidCircularB-Semibold", 7.0), y0=112),
                body("Text danach.", y0=124),
            ]
        )
        chapter = extract_chapter([page])
        (block,) = chapter.blocks
        assert paragraph_text(block) == "Text davor. Text danach."

    def test_isotope_superscript_stays_plain_text(self) -> None:
        page = FakePage(
            [
                line(
                    span("datieren gemäss ", "Practice-Regular", 10.4),
                    span("14", "Practice-Regular", 6.06, superscript=True),
                    span("C-Analysen", "Practice-Regular", 10.4),
                )
            ]
        )
        chapter = extract_chapter([page])
        (block,) = chapter.blocks
        assert isinstance(block, Paragraph)
        assert block.inlines == [TextRun("datieren gemäss 14C-Analysen")]

    def test_italic_switch_across_lines_keeps_space(self) -> None:
        page = FakePage(
            [
                line(span("kursiv am Ende", "Practice-Italic", 10.4), y0=100),
                body("regulär weiter.", y0=112),
            ]
        )
        chapter = extract_chapter([page])
        (block,) = chapter.blocks
        assert isinstance(block, Paragraph)
        assert block.inlines == [
            TextRun("kursiv am Ende ", italic=True),
            TextRun("regulär weiter."),
        ]

    def test_en_dash_number_range_joins_without_space(self) -> None:
        page = FakePage(
            [
                body("Seiten 123–", y0=100),
                body("127 zeigen dies.", y0=112),
            ]
        )
        chapter = extract_chapter([page])
        (block,) = chapter.blocks
        assert paragraph_text(block) == "Seiten 123–127 zeigen dies."

    def test_append_line_with_only_dropped_spans_is_noop(self) -> None:
        from sgb_html.extract import _append_line

        inlines: list = [TextRun("bestehender Text")]
        anchor_only = RawLine(1, 55.0, 100.0, (RawSpan("[4]", "EuclidCircularB-Semibold", 7.0),))
        _append_line(inlines, anchor_only)
        assert inlines == [TextRun("bestehender Text")]

    def test_empty_span_is_ignored(self) -> None:
        page = FakePage(
            [
                line(
                    span("Text", "Practice-Regular", 10.4),
                    span("", "Practice-Regular", 10.4),
                    span("   ", "Practice-Regular", 10.4),
                )
            ]
        )
        chapter = extract_chapter([page])
        (block,) = chapter.blocks
        assert paragraph_text(block) == "Text"


class TestStructure:
    def test_title_lead_heading_order(self) -> None:
        page = FakePage(
            [
                line(span("Natur", "EuclidCircularB-Semibold", 32.0), y0=60),
                line(span("raum", "EuclidCircularB-Semibold", 32.0), y0=95),
                line(
                    span("Der Lead-Text der Einleitung.", "EuclidCircularB-Regular", 12.5), y0=120
                ),
                line(span("Geologie als Grundstein", "EuclidCircularB-Semibold", 14.0), y0=150),
                body("Der erste Satz.", y0=170),
            ]
        )
        chapter = extract_chapter([page])
        assert chapter.title == "Natur raum"
        lead, heading, paragraph = chapter.blocks
        assert isinstance(lead, Paragraph) and lead.lead
        assert isinstance(heading, Heading) and heading.text == "Geologie als Grundstein"
        assert paragraph_text(paragraph) == "Der erste Satz."

    def test_multiline_heading_merges(self) -> None:
        page = FakePage(
            [
                line(span("Ein Auf und Ab:", "EuclidCircularB-Semibold", 14.0), y0=100),
                line(span(f"Kalt{SOFT_HYPHEN}", "EuclidCircularB-Semibold", 14.0), y0=118),
                line(span("zeiten", "EuclidCircularB-Semibold", 14.0), y0=136),
                body("Text danach.", y0=160),
            ]
        )
        chapter = extract_chapter([page])
        heading, paragraph = chapter.blocks
        assert isinstance(heading, Heading)
        assert heading.text == "Ein Auf und Ab: Kaltzeiten"
        assert paragraph_text(paragraph) == "Text danach."

    def test_two_headings_with_body_between(self) -> None:
        page = FakePage(
            [
                line(span("Erstens", "EuclidCircularB-Semibold", 14.0), y0=100),
                body("Absatz eins.", y0=120),
                line(span("Zweitens", "EuclidCircularB-Semibold", 14.0), y0=140),
                body("Absatz zwei.", y0=160),
            ]
        )
        chapter = extract_chapter([page])
        kinds = [type(block).__name__ for block in chapter.blocks]
        assert kinds == ["Heading", "Paragraph", "Heading", "Paragraph"]

    def test_aside_with_heading_and_prose(self) -> None:
        page = FakePage(
            [
                body("Haupttext.", y0=100),
                line(span("Warteck", "EuclidCircularB-Semibold", 9.5), y0=300),
                line(span("bleibt", "EuclidCircularB-Semibold", 9.5), y0=312),
                line(span("Für viele Menschen war die", "EuclidCircularB-Regular", 8.5), y0=330),
                line(span("Brauerei fest verbunden.", "EuclidCircularB-Regular", 8.5), y0=342),
            ]
        )
        chapter = extract_chapter([page])
        _paragraph, aside = chapter.blocks
        assert isinstance(aside, Aside)
        head, prose = aside.blocks
        assert head == Heading("Warteck bleibt", level=3)
        assert paragraph_text(prose) == "Für viele Menschen war die Brauerei fest verbunden."

    def test_aside_prose_splits_paragraphs_on_indent(self) -> None:
        page = FakePage(
            [
                line(span("Erster Kastenabsatz.", "EuclidCircularB-Regular", 8.5), y0=100),
                line(
                    span("Zweiter Kastenabsatz.", "EuclidCircularB-Regular", 8.5),
                    x0=75.0,
                    y0=112,
                ),
            ]
        )
        chapter = extract_chapter([page])
        (aside,) = chapter.blocks
        assert isinstance(aside, Aside)
        assert [paragraph_text(block) for block in aside.blocks] == [
            "Erster Kastenabsatz.",
            "Zweiter Kastenabsatz.",
        ]

    def test_aside_waits_for_paragraph_to_close(self) -> None:
        first = FakePage(
            [
                body("Ein Absatz, der über die", y0=100),
                line(span("Kastentext auf Seite eins.", "EuclidCircularB-Regular", 8.5), y0=300),
            ]
        )
        second = FakePage(
            [
                body("Seite läuft und hier endet.", y0=100),
                body("Neuer Absatz.", x0=85.0, y0=112),
            ]
        )
        chapter = extract_chapter([first, second])
        kinds = [type(block).__name__ for block in chapter.blocks]
        assert kinds == ["Paragraph", "Aside", "Paragraph"]
        assert (
            paragraph_text(chapter.blocks[0])
            == "Ein Absatz, der über die Seite läuft und hier endet."
        )

    def test_aside_prose_before_heading(self) -> None:
        page = FakePage(
            [
                line(span("Einleitung des Kastens.", "EuclidCircularB-Regular", 8.5), y0=100),
                line(span("Kastentitel", "EuclidCircularB-Semibold", 9.5), y0=120),
                line(span("Haupttextkasten.", "EuclidCircularB-Regular", 8.5), y0=140),
            ]
        )
        chapter = extract_chapter([page])
        (aside,) = chapter.blocks
        assert isinstance(aside, Aside)
        assert [type(block).__name__ for block in aside.blocks] == [
            "Paragraph",
            "Heading",
            "Paragraph",
        ]

    def test_unknown_styles_warn_and_fail_open(self) -> None:
        page = FakePage(
            [
                line(span("Grosser Fremdtext", "Helvetica", 10.0), y0=100),
                line(span("kleiner fremdtext", "Helvetica", 6.0), y0=120),
            ]
        )
        chapter = extract_chapter([page])
        (block,) = chapter.blocks
        assert paragraph_text(block) == "Grosser Fremdtext"
        assert len(chapter.warnings) == 2
        assert "unknown style Helvetica" in chapter.warnings[0]


class TestNotes:
    def test_notes_parse_and_continue(self) -> None:
        page = FakePage(
            [
                note_start(1, "Franke 1989.", y0=100),
                note_start(2, f"Rentzel; Pümpin; Brönni{SOFT_HYPHEN}", y0=120),
                note_cont("mann 2015.", y0=130),
            ]
        )
        chapter = extract_chapter([page])
        assert [(n.number, n.text) for n in chapter.notes] == [
            (1, "Franke 1989."),
            (2, "Rentzel; Pümpin; Brönnimann 2015."),
        ]

    def test_note_columns_read_left_column_first(self) -> None:
        page = FakePage(
            [
                note_start(3, "Rechte Spalte.", x0=191.8, y0=100),
                note_start(1, "Linke Spalte oben.", x0=48.0, y0=100),
                note_start(2, "Linke Spalte unten.", x0=48.0, y0=120),
            ]
        )
        chapter = extract_chapter([page])
        assert [note.number for note in chapter.notes] == [1, 2, 3]

    def test_notes_span_pages(self) -> None:
        first = FakePage([note_start(1, "Erste Seite,", y0=100)])
        second = FakePage(
            [
                note_cont("zweite Seite.", y0=100),
                note_start(2, "Nächste Note.", y0=120),
            ]
        )
        chapter = extract_chapter([first, second])
        assert [(n.number, n.text) for n in chapter.notes] == [
            (1, "Erste Seite, zweite Seite."),
            (2, "Nächste Note."),
        ]

    def test_column_without_starts_is_skipped(self) -> None:
        page = FakePage(
            [
                note_start(1, "Echte Note.", x0=48.0, y0=100),
                note_cont("Diagrammbeschriftung", x0=300.0, y0=200),
                note_cont("noch eine Beschriftung", x0=300.0, y0=210),
            ]
        )
        chapter = extract_chapter([page])
        assert [(n.number, n.text) for n in chapter.notes] == [(1, "Echte Note.")]
        assert any("skipped 2 small-print lines" in w for w in chapter.warnings)

    def test_orphan_continuation_warns(self) -> None:
        page = FakePage(
            [
                note_cont("verwaiste Fortsetzung", y0=100),
                note_start(1, "Erste Note.", y0=120),
            ]
        )
        chapter = extract_chapter([page])
        assert [(n.number, n.text) for n in chapter.notes] == [(1, "Erste Note.")]
        assert any("continuation without start" in w for w in chapter.warnings)

    def test_bold_start_without_number_treated_as_continuation(self) -> None:
        page = FakePage(
            [
                note_start(1, "Anfang.", y0=100),
                line(span("Fortsetzung fett", "Practice-Bold", 5.5), x0=48.0, y0=120),
            ]
        )
        chapter = extract_chapter([page])
        (note,) = chapter.notes
        assert note.text == "Anfang. Fortsetzung fett"
