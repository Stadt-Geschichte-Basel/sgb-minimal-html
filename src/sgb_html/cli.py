"""Command line interface: convert chapter PDFs to HTML, QA them, upload to OMP."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

import pymupdf
import structlog
import typer

from sgb_html.extract import (
    Aside,
    Chapter,
    Heading,
    Marker,
    Paragraph,
    TextRun,
    extract_chapter,
)
from sgb_html.omp import ApiChapter, ApiPublication, ApiSubmission, OmpClient, _de
from sgb_html.render import ChapterMeta, render_chapter
from sgb_html.settings import Settings

app = typer.Typer(add_completion=False, help=__doc__)
log = structlog.get_logger()

_PDF_NAME_RE = re.compile(r"^(sgb-\d{2}\.\d{2}-\d+)\.pdf$")


@dataclass(frozen=True)
class ChapterJob:
    """One chapter: local PDF plus its OMP context."""

    doi_suffix: str
    pdf_path: Path
    volume_dir: str
    submission: ApiSubmission
    publication: ApiPublication
    chapter: ApiChapter

    @property
    def html_name(self) -> str:
        return f"{self.doi_suffix}.html"


def _chapter_pdfs(pdf_dir: Path) -> dict[str, tuple[Path, str]]:
    """Map DOI suffix (``sgb-01.01-439115``) to its chapter PDF path."""
    mapping: dict[str, tuple[Path, str]] = {}
    for path in sorted(pdf_dir.glob("volume-*/chapters/*.pdf")):
        match = _PDF_NAME_RE.match(path.name)
        if match:
            mapping[match.group(1)] = (path, path.parent.parent.name)
    return mapping


def _jobs(settings: Settings, client: OmpClient, doi: str | None) -> list[ChapterJob]:
    pdfs = _chapter_pdfs(settings.pdf_dir)
    jobs: list[ChapterJob] = []
    for submission in client.submissions():
        publication = submission.current_publication
        for chapter in publication.chapters:
            suffix = chapter.doi.removeprefix("10.21255/")
            if not suffix or suffix not in pdfs:
                if suffix:
                    log.warning("no_pdf_for_chapter", doi=chapter.doi)
                continue
            path, volume_dir = pdfs[suffix]
            jobs.append(ChapterJob(suffix, path, volume_dir, submission, publication, chapter))
    if doi:
        wanted = doi.removeprefix("10.21255/")
        jobs = [job for job in jobs if job.doi_suffix == wanted]
        if not jobs:
            raise typer.BadParameter(f"no chapter found for DOI {doi}")
    return jobs


def _meta_for(job: ChapterJob) -> ChapterMeta:
    return ChapterMeta(
        title=_de(job.chapter.title),
        subtitle=_de(job.chapter.subtitle),
        authors=job.chapter.author_names,
        doi=job.chapter.doi,
        pages=job.chapter.pages or "",
        license_url=job.publication.chapterLicenseUrl,
        volume_title=job.publication.volume_title,
        url_published=job.publication.urlPublished,
    )


def _extract(job: ChapterJob) -> Chapter:
    with pymupdf.open(job.pdf_path) as doc:
        return extract_chapter(doc)


def _output_path(settings: Settings, job: ChapterJob) -> Path:
    return settings.html_dir / job.volume_dir / job.html_name


@app.command()
def convert(doi: str | None = typer.Option(None, help="Convert a single chapter by DOI.")) -> None:
    """Convert chapter PDFs to minimal HTML files under ``html/``."""
    settings = Settings()  # ty: ignore[missing-argument]  # apikey comes from .env
    client = OmpClient(settings.base_url, settings.apikey)
    for job in _jobs(settings, client, doi):
        chapter = _extract(job)
        html = render_chapter(chapter, _meta_for(job))
        out = _output_path(settings, job)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(html, encoding="utf-8")
        log.info(
            "converted",
            doi=job.doi_suffix,
            blocks=len(chapter.blocks),
            notes=len(chapter.notes),
            warnings=len(chapter.warnings),
        )
        for warning in chapter.warnings:
            log.warning("extract_warning", doi=job.doi_suffix, detail=warning)


@app.command()
def check() -> None:
    """QA report: extraction coverage and footnote consistency per chapter."""
    settings = Settings()  # ty: ignore[missing-argument]  # apikey comes from .env
    client = OmpClient(settings.base_url, settings.apikey)
    failures = 0
    for job in _jobs(settings, client, None):
        out = _output_path(settings, job)
        if not out.exists():
            log.error("missing_html", doi=job.doi_suffix)
            failures += 1
            continue
        chapter = _extract(job)
        with pymupdf.open(job.pdf_path) as doc:
            raw_words = sum(len(page.get_text().split()) for page in doc)
        kept_words = _word_count(chapter)
        ratio = kept_words / raw_words if raw_words else 0.0
        marker_numbers = _marker_numbers(chapter)
        note_numbers = [note.number for note in chapter.notes]
        unmatched = sorted(set(marker_numbers) - set(note_numbers))
        status = "ok"
        if ratio < 0.6 or unmatched:
            status = "review"
            failures += 1
        log.info(
            "checked",
            doi=job.doi_suffix,
            status=status,
            word_ratio=round(ratio, 2),
            markers=len(marker_numbers),
            notes=len(note_numbers),
            unmatched_markers=unmatched[:10],
            warnings=len(chapter.warnings),
        )
    if failures:
        log.error("check_failed", chapters=failures)
        raise typer.Exit(1)
    log.info("check_passed")


def _word_count(chapter: Chapter) -> int:
    def block_words(block: Paragraph | Heading | Aside) -> int:
        if isinstance(block, Heading):
            return len(block.text.split())
        if isinstance(block, Paragraph):
            return sum(len(i.text.split()) for i in block.inlines if isinstance(i, TextRun))
        return sum(block_words(child) for child in block.blocks)

    words = sum(block_words(block) for block in chapter.blocks)
    words += len(chapter.title.split())
    words += sum(len(note.text.split()) + 1 for note in chapter.notes)
    return words


def _marker_numbers(chapter: Chapter) -> list[int]:
    numbers: list[int] = []

    def visit(block: object) -> None:
        if isinstance(block, Paragraph):
            numbers.extend(i.number for i in block.inlines if isinstance(i, Marker))
        elif isinstance(block, Aside):
            for child in block.blocks:
                visit(child)

    for block in chapter.blocks:
        visit(block)
    return numbers


@app.command()
def upload(
    doi: str | None = typer.Option(None, help="Upload a single chapter by DOI."),
    dry_run: bool = typer.Option(False, "--dry-run", help="Only print planned API calls."),
    replace: bool = typer.Option(False, "--replace", help="Upload even if already attached."),
) -> None:
    """Attach the generated HTML files to each volume's HTML publication format."""
    settings = Settings()  # ty: ignore[missing-argument]  # apikey comes from .env
    client = OmpClient(settings.base_url, settings.apikey)
    for job in _jobs(settings, client, doi):
        out = _output_path(settings, job)
        if not out.exists():
            log.error("missing_html", doi=job.doi_suffix, path=str(out))
            raise typer.Exit(1)
        html_format = job.publication.format_named("HTML")
        if html_format is None:
            log.error("no_html_format", submission=job.submission.id)
            raise typer.Exit(1)
        already = [f for f in html_format.submissionFiles if f.file_name == job.html_name]
        if already and not replace:
            log.info("skip_existing", doi=job.doi_suffix, file_id=already[0].id)
            continue
        pdf_format = job.publication.format_named("PDF")
        genre_id = None
        if pdf_format:
            for file in pdf_format.submissionFiles:
                if file.chapterId == job.chapter.id:
                    genre_id = file.genreId
        if dry_run:
            log.info(
                "dry_run",
                doi=job.doi_suffix,
                submission=job.submission.id,
                format_id=html_format.id,
                chapter_id=job.chapter.id,
                genre_id=genre_id,
                file=job.html_name,
            )
            continue
        uploaded = client.upload_proof_file(job.submission.id, out, job.html_name)
        file_id = uploaded["id"]
        client.attach_to_format(
            job.submission.id, file_id, html_format.id, job.chapter.id, genre_id
        )
        log.info(
            "uploaded",
            doi=job.doi_suffix,
            file_id=file_id,
            format_id=html_format.id,
            chapter_id=job.chapter.id,
        )


if __name__ == "__main__":
    app()
