"""Unit tests for review packs: ledger, lint, page images and findings files."""

from __future__ import annotations

import json
from pathlib import Path
from typing import ClassVar

import pymupdf
import pytest
from conftest import FakePage, body, line, span
from pydantic import ValidationError

from sgb_html.extract import Kind, RawLine, RawSpan, iter_raw_lines, plan_lines
from sgb_html.review import (
    CLASS_HINTS,
    LedgerRow,
    align,
    build_pack,
    ledger_rows,
    ledger_tsv,
    lint_html,
    lint_ledger,
    load_findings,
    parse_blocks,
    render_pages,
    word_count,
)

EUCLID = "EuclidCircularB-Regular"

PROBLEM_HTML = """<html><body>
<h1>Titel</h1>
<p>Autor</p>
<p class="lead">Erster Lead 1876</p>
<h2>Abschnitt</h2>
<p class="lead">Lead nach Überschrift.</p>
<p>Text  doppelt « offen « und « soft\xadhyphen \x01 https://x.org/a/ b/c.html Satz.
<sup id="ref-fn1"><a href="#fn1">1</a></sup><sup id="ref-fn5"><a href="#fn5">5</a></sup></p>
<p class="lead">Später Lead nach Text</p>
<p>Dieser Absatz ist lang genug, um auf Wiederholung geprüft zu werden.</p>
<p>Dieser Absatz ist lang genug, um auf Wiederholung geprüft zu werden.</p>
<aside><p>Kastentext mit Inhalt</p></aside>
<section><ol>
<li id="fn1" value="1">Note <a href="#ref-fn1" role="doc-backlink">↑</a></li>
<li id="fn2" value="2">Waise <a href="#ref-fn2" role="doc-backlink">↑</a></li>
<li id="fn9" value="9">Ohne Rücklink</li>
</ol></section>
</body></html>"""


def codes(html: str) -> list[str]:
    return [finding.code for finding in lint_html(html)]


def make_pdf(path: Path, pages: int = 2) -> None:
    doc = pymupdf.open()
    for number in range(pages):
        page = doc.new_page()
        page.insert_text((72, 72), f"Seite {number + 1} mit drei Wörtern")
    doc.save(path)
    doc.close()


class TestLedger:
    def test_rows_follow_final_decisions(self) -> None:
        page = FakePage(
            [
                line(span("Lead", EUCLID, 12.5), y0=60),
                body("Satz im Lauftext.", y0=100),
                line(span("1876", EUCLID, 12.5), y0=200),
                line(span("seltsam", "Practice-Regular", 7.0), y0=300),
            ]
        )
        plan = plan_lines(list(iter_raw_lines([page])))
        rows = ledger_rows(plan)
        assert [(row.kind, row.kept) for row in rows] == [
            (Kind.LEAD, True),
            (Kind.BODY, True),
            (Kind.DROP, False),
            (Kind.DROP, False),
        ]
        assert not plan.appendix

    def test_tsv_has_header_and_one_row_per_line(self) -> None:
        raw = RawLine(4, 57.0, 100.0, (RawSpan("Ein\tText", "Practice-Regular", 10.4),))
        text = ledger_tsv(ledger_rows(plan_lines([raw])))
        header, row = text.splitlines()
        assert header.split("\t") == ["page", "x", "y", "size", "font", "kind", "kept", "text"]
        assert row.split("\t") == [
            "4",
            "57",
            "100",
            "10.4",
            "Practice-Regular",
            "body",
            "kept",
            "Ein Text",
        ]

    def test_appendix_plan_uses_appendix_roles(self) -> None:
        raw = RawLine(1, 57.0, 100.0, (RawSpan("Eintrag", EUCLID, 6.5),))
        plan = plan_lines([raw])
        assert plan.appendix
        assert ledger_rows(plan)[0].kept


def lrow(page: int, y: float, kind: Kind, text: str) -> LedgerRow:
    return LedgerRow(page, 57.0, y, 10.4, "Practice-Regular", kind, text, text)


PARA_ONE = "Das erste Basler Stadtrecht regelte den Markt und die Zölle in der Stadt sehr genau."
PARA_TWO = "Die Zünfte erhielten im Laufe des Jahrhunderts mehr Rechte im Rat der Stadt Basel."
PARA_THREE = "Am Rhein entstanden neue Quartiere für Handwerker und Händler aus der ganzen Region."


def body_rows() -> list[LedgerRow]:
    return [
        lrow(1, 100, Kind.BODY, PARA_ONE),
        lrow(1, 200, Kind.BODY, PARA_TWO),
        lrow(2, 100, Kind.BODY, PARA_THREE),
        lrow(2, 300, Kind.ASIDE_BODY, "Ein Kasten mit genügend langem Text im Rand."),
        lrow(3, 100, Kind.NOTE, "12\t Franke 1989, S. 44."),
        lrow(3, 110, Kind.DROP, "Seite 3 Laufkopf mit genug Text"),
        lrow(3, 120, Kind.TITLE, "Titel der Zeile"),
        lrow(3, 130, Kind.BODY, "kurz"),
    ]


def doc(*blocks: str) -> str:
    return "<html><body>" + "".join(blocks) + "</body></html>"


CLEAN_DOC = doc(
    "<h1>Titel</h1><p>Autor</p><p>In: Reihe, Basel 2026.</p>",
    f"<p>{PARA_ONE}</p><p>{PARA_TWO}</p><p>{PARA_THREE}</p>",
    "<aside><p>Ein Kasten mit genügend langem Text im Rand.</p></aside>",
    '<ol><li id="fn12" value="12">Franke 1989, S. 44. '
    '<a href="#ref-fn12" role="doc-backlink">↑</a></li>'
    '<li id="fnx" value="x">Ohne Nummer im Namen</li></ol>',
)


def align_codes(html: str, *, appendix: bool = False) -> list[str]:
    return [f.code for f in align(body_rows(), html, appendix=appendix)]


class TestAlign:
    def test_matching_html_has_no_candidates(self) -> None:
        assert align(body_rows(), CLEAN_DOC) == []

    def test_missing_pdf_line_is_grouped_by_page(self) -> None:
        html = CLEAN_DOC.replace(f"<p>{PARA_TWO}</p>", "")
        (finding,) = [f for f in align(body_rows(), html) if f.code == "pdf-line-missing"]
        assert finding.page == 1 and "Zünfte erhielten" in finding.detail

    def test_text_that_the_pdf_does_not_hold_is_flagged(self) -> None:
        html = CLEAN_DOC.replace(
            PARA_TWO, PARA_TWO[:40] + " Bildlegende zu Abbildung zwölf. " + PARA_TWO[40:]
        )
        assert "html-not-in-pdf" in align_codes(html)

    def test_repeated_sentence_is_flagged(self) -> None:
        html = CLEAN_DOC.replace(PARA_TWO, PARA_TWO + " " + PARA_TWO)
        assert "html-not-in-pdf" in align_codes(html)

    def test_swapped_paragraphs_are_flagged_unless_appendix(self) -> None:
        html = CLEAN_DOC.replace(
            f"<p>{PARA_ONE}</p><p>{PARA_TWO}</p>", f"<p>{PARA_TWO}</p><p>{PARA_ONE}</p>"
        )
        assert "block-order" in align_codes(html)
        assert "block-order" not in align_codes(html, appendix=True)

    def test_space_inside_a_word_is_flagged(self) -> None:
        html = CLEAN_DOC.replace("Handwerker", "Hand werker")
        assert ("split-word", "Hand werker") in [
            (f.code, f.detail) for f in align(body_rows(), html)
        ]

    def test_wrong_note_text_is_flagged(self) -> None:
        html = CLEAN_DOC.replace("S. 44.", "S. 47.")
        (finding,) = [f for f in align(body_rows(), html) if f.code == "note-mismatch"]
        assert finding.locator == "fn12"

    def test_paragraph_without_a_pdf_match_does_not_break_the_order_check(self) -> None:
        html = CLEAN_DOC.replace(
            "<aside>", "<p>Ein ganz anderer Absatz ohne Entsprechung im PDF.</p><aside>"
        )
        assert "block-order" not in align_codes(html)

    def test_header_blocks_are_not_compared(self) -> None:
        assert "html-not-in-pdf" not in align_codes(CLEAN_DOC)

    def test_main_text_reads_top_to_bottom_on_each_page(self) -> None:
        rows = [lrow(1, 200, Kind.BODY, PARA_TWO), lrow(1, 100, Kind.BODY, PARA_ONE)]
        html = doc(f"<p>{PARA_ONE}</p><p>{PARA_TWO}</p>")
        assert align(rows, html) == []


class TestParseBlocks:
    def test_blocks_carry_css_aside_markers_and_backlinks(self) -> None:
        blocks = parse_blocks(PROBLEM_HTML)
        assert [block.tag for block in blocks[:3]] == ["h1", "p", "p"]
        assert blocks[2].css == "lead"
        aside = next(block for block in blocks if block.text.startswith("Kastentext"))
        assert aside.in_aside
        marked = next(block for block in blocks if block.marker_ids)
        assert marked.marker_ids == ["ref-fn1", "ref-fn5"]
        note = next(block for block in blocks if block.note_id == "fn1")
        assert note.backlink == "#ref-fn1"

    def test_markup_outside_body_is_ignored(self) -> None:
        assert parse_blocks("<head><title>x</title></head><p>nope</p>") == []


class TestLintHtml:
    def test_every_html_finding_is_reported(self) -> None:
        found = codes(PROBLEM_HTML)
        for code in (
            "marker-without-note",
            "note-without-marker",
            "short-paragraph",
            "repeated-paragraph",
            "lead-ends-in-number",
            "lead-without-heading",
            "double-space",
            "control-character",
            "url-space",
            "guillemets",
        ):
            assert code in found, code

    def test_clean_chapter_has_no_findings(self) -> None:
        html = (
            "<body><h1>T</h1><p class='lead'>Ein Lead.</p><h2>A</h2>"
            "<p>Ein Satz mit <sup id='ref-fn1'><a href='#fn1'>1</a></sup> Marker.</p>"
            "<h2>B</h2><p class='lead'>Später Lead.</p><p class='lead'>Noch ein Lead.</p>"
            "<ol><li id='fn1'>Note <a href='#ref-fn1' role='doc-backlink'>↑</a></li>"
            "<li id='fn7'>Ohne Rücklink</li></ol></body>"
        )
        assert codes(html) == []

    def test_lead_after_heading_is_not_flagged(self) -> None:
        html = (
            "<body><p class='lead'>Eins zwei.</p><p>Text hier.</p>"
            "<h2>H</h2><p class='lead'>Mitte hier.</p></body>"
        )
        assert "lead-without-heading" not in codes(html)


class TestLintLedger:
    def test_dropped_body_text_and_lead_without_letters(self) -> None:
        rows = ledger_rows(
            plan_lines(
                [
                    RawLine(
                        2, 57.0, 10.0, (RawSpan("Eine Zeile Text hier", "Practice-Regular", 10.4),)
                    ),
                    RawLine(
                        2, 57.0, 20.0, (RawSpan("Dropped drei Worte", "Practice-Extrabold", 10.4),)
                    ),
                ]
            )
        )
        from dataclasses import replace

        rows = [
            replace(rows[0], kind=Kind.DROP),
            rows[1],
            replace(rows[0], kind=Kind.LEAD, text="1876"),
        ]
        found = lint_ledger(rows)
        assert [(f.code, f.page) for f in found] == [
            ("dropped-body-text", 2),
            ("lead-without-letters", 2),
        ]


def test_word_count_skips_notes() -> None:
    assert word_count("<body><p>Eins zwei</p><ol><li id='fn1'>Drei vier fünf</li></ol></body>") == 2


class TestPack:
    def test_render_pages_writes_one_png_per_page(self, tmp_path: Path) -> None:
        pdf = tmp_path / "x.pdf"
        make_pdf(pdf, pages=3)
        assert render_pages(pdf, tmp_path / "pages", dpi=50) == 3
        assert sorted(p.name for p in (tmp_path / "pages").iterdir()) == [
            "p01.png",
            "p02.png",
            "p03.png",
        ]

    def test_build_pack_writes_all_files(self, tmp_path: Path) -> None:
        pdf = tmp_path / "x.pdf"
        make_pdf(pdf)
        html = tmp_path / "x.html"
        html.write_text(PROBLEM_HTML, encoding="utf-8")
        manifest = build_pack(pdf, html, tmp_path / "out", doi="sgb-00.00-1", git_sha="abc", dpi=50)
        out = tmp_path / "out"
        assert sorted(p.name for p in out.iterdir()) == [
            "blocks.tsv",
            "chapter.html",
            "diff.json",
            "ledger.tsv",
            "lint.json",
            "manifest.json",
            "pages",
        ]
        assert manifest["pages"] == 2 and manifest["git_sha"] == "abc"
        assert manifest["pdf_words"] == 10
        assert json.loads((out / "manifest.json").read_text(encoding="utf-8")) == manifest
        lint = json.loads((out / "lint.json").read_text(encoding="utf-8"))
        assert lint
        assert len(lint) == manifest["lint_findings"]
        assert (out / "chapter.html").read_text(encoding="utf-8") == PROBLEM_HTML

    def test_word_ratio_is_zero_for_a_pdf_without_text(self, tmp_path: Path) -> None:
        doc = pymupdf.open()
        doc.new_page()
        doc.save(tmp_path / "blank.pdf")
        html = tmp_path / "x.html"
        html.write_text("<body><p>Ein Wort.</p></body>", encoding="utf-8")
        manifest = build_pack(tmp_path / "blank.pdf", html, tmp_path / "o", doi="d", dpi=40)
        assert manifest["word_ratio"] == 0.0


class TestFindingsFile:
    GOOD: ClassVar[dict[str, object]] = {
        "doi": "sgb-00.00-1",
        "findings": [
            {
                "severity": "major",
                "html_locator": "p#12",
                "quote_html": "a",
                "quote_pdf": "b",
                "pdf_page": 3,
                "confidence": "high",
                "class_hint": "lost-text",
            }
        ],
        "pages_inspected": [1, 2, 3],
        "pages_skipped": [],
    }

    def test_valid_file_loads(self, tmp_path: Path) -> None:
        path = tmp_path / "findings.json"
        path.write_text(json.dumps(self.GOOD), encoding="utf-8")
        loaded = load_findings(path)
        assert loaded.findings[0].class_hint == "lost-text"
        assert "lost-text" in CLASS_HINTS

    def test_unknown_class_is_rejected(self, tmp_path: Path) -> None:
        bad = json.loads(json.dumps(self.GOOD))
        bad["findings"][0]["class_hint"] = "vibes"
        path = tmp_path / "findings.json"
        path.write_text(json.dumps(bad), encoding="utf-8")
        with pytest.raises(ValidationError):
            load_findings(path)
