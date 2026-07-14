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
from sgb_html.omp import (
    ApiChapter,
    ApiPublication,
    ApiPublicationFormat,
    ApiSubmission,
    OmpClient,
    _de,
    redact_token,
)
from sgb_html.render import ChapterMeta, render_chapter
from sgb_html.settings import Settings

app = typer.Typer(add_completion=False, help=__doc__)
log = structlog.get_logger()

_PDF_NAME_RE = re.compile(r"^(sgb-\d{2}\.\d{2}-\d+)\.pdf$")
_VOLUME_PDF_NAME_RE = re.compile(r"^(sgb-\d{2}-\d+)\.pdf$")


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


def _volume_pdfs(pdf_dir: Path) -> dict[str, Path]:
    """Map volume DOI suffix (``sgb-09-486500``) to its full-volume PDF path.

    Volume PDFs live at the volume-directory root (``volume-*/sgb-0X-*.pdf``),
    alongside the ``chapters/`` subdirectory that holds the chapter PDFs.
    """
    mapping: dict[str, Path] = {}
    for path in sorted(pdf_dir.glob("volume-*/*.pdf")):
        match = _VOLUME_PDF_NAME_RE.match(path.name)
        if match:
            mapping[match.group(1)] = path
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


# The genre fingerprint every volume on emono follows: the full-volume PDF is the
# "volume" genre, the last (appendix) chapter is its own genre, and every other
# chapter galley is the ordinary chapter genre — in both the PDF and HTML formats.
GENRE_VOLUME = 57
GENRE_APPENDIX = 55
GENRE_CHAPTER = 58


def _appendix_chapter_id(publication: ApiPublication) -> int | None:
    """The chapter treated as the appendix: the one with the highest DOI suffix.

    Chapter DOIs are zero-padded (``sgb-03.00``…``sgb-03.10``), so the plain
    ``max`` of their DOIs is the last chapter of the volume.
    """
    chapters = [ch for ch in publication.chapters if ch.doi]
    if not chapters:
        return None
    return max(chapters, key=lambda ch: ch.doi).id


def _expected_genre(chapter_id: int | None, appendix_chapter_id: int | None) -> int:
    if chapter_id is None:
        return GENRE_VOLUME
    if chapter_id == appendix_chapter_id:
        return GENRE_APPENDIX
    return GENRE_CHAPTER


def _genre_anomalies(publication: ApiPublication) -> list[dict[str, object]]:
    """Galley files whose genre deviates from the expected fingerprint.

    Checking each file against the expected genre for its chapter also catches a
    null genre and a PDF/HTML mismatch, since both formats share one expectation.
    """
    appendix_id = _appendix_chapter_id(publication)
    anomalies: list[dict[str, object]] = []
    for fmt in publication.publicationFormats:
        if fmt.format_name not in ("PDF", "HTML"):
            continue
        for file in fmt.submissionFiles:
            expected = _expected_genre(file.chapterId, appendix_id)
            if file.genreId != expected:
                anomalies.append(
                    {
                        "format": fmt.format_name,
                        "file_id": file.id,
                        "chapter_id": file.chapterId,
                        "genre_id": file.genreId,
                        "expected_genre": expected,
                    }
                )
    return anomalies


@app.command(name="check-genres")
def check_genres() -> None:
    """Flag galley files whose genre deviates from the per-volume norm.

    A file with a null or wrong genre is the kind of mis-assignment that makes the
    OMP catalog page return HTTP 500. Exits non-zero if any volume deviates.
    """
    settings = Settings()  # ty: ignore[missing-argument]  # apikey comes from .env
    client = OmpClient(settings.base_url, settings.apikey)
    failures = 0
    for submission in client.submissions():
        publication = submission.current_publication
        anomalies = _genre_anomalies(publication)
        title = publication.volume_title[:40]
        if not anomalies:
            log.info("genre_ok", submission=submission.id, title=title)
            continue
        failures += len(anomalies)
        for anomaly in anomalies:
            log.error("genre_anomaly", submission=submission.id, title=title, **anomaly)
    if failures:
        log.error("check_genres_failed", anomalies=failures)
        raise typer.Exit(1)
    log.info("check_genres_passed")


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
        if already and not dry_run:
            for old in already:
                client.delete_file(job.submission.id, old.id)
                log.info("deleted_old", doi=job.doi_suffix, file_id=old.id)
        pdf_format = job.publication.format_named("PDF")
        genre_id = None
        if pdf_format:
            for file in pdf_format.submissionFiles:
                if file.chapterId == job.chapter.id:
                    genre_id = file.genreId
        if genre_id is None:
            # Refuse to upload a genre-less galley: OMP's catalog page can 500 on a
            # file with no (or the wrong) genre, and a null genre here means the PDF
            # sibling we inherit from is missing or itself mis-typed. Fix the source
            # genre first rather than silently propagating the gap.
            log.error("no_genre_for_chapter", doi=job.doi_suffix, chapter_id=job.chapter.id)
            raise typer.Exit(1)
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
        uploaded = client.upload_proof_file(
            job.submission.id, out, job.html_name, html_format.id, genre_id
        )
        file_id = uploaded["id"]
        client.attach_to_chapter(job.submission.id, file_id, job.chapter.id)
        log.info(
            "uploaded",
            doi=job.doi_suffix,
            file_id=file_id,
            format_id=html_format.id,
            chapter_id=job.chapter.id,
        )


@dataclass(frozen=True)
class PdfGalleyJob:
    """One PDF galley to replace: a local enhanced PDF plus its OMP target.

    ``chapter_id`` is the chapter to attach to, or ``None`` for a volume-level
    monograph galley (which has no chapter).
    """

    doi_suffix: str
    pdf_path: Path
    submission_id: int
    pdf_format: ApiPublicationFormat
    chapter_id: int | None


def _pdf_jobs(settings: Settings, client: OmpClient, doi: str | None) -> list[PdfGalleyJob]:
    """Match enhanced PDFs in ``pdf_dir`` to their OMP PDF galley targets."""
    chapter_pdfs = _chapter_pdfs(settings.pdf_dir)
    volume_pdfs = _volume_pdfs(settings.pdf_dir)
    jobs: list[PdfGalleyJob] = []
    for submission in client.submissions():
        publication = submission.current_publication
        pdf_format = publication.format_named("PDF")
        if pdf_format is None:
            log.warning("no_pdf_format", submission=submission.id)
            continue
        for chapter in publication.chapters:
            suffix = chapter.doi.removeprefix("10.21255/")
            if suffix and suffix in chapter_pdfs:
                path, _ = chapter_pdfs[suffix]
                jobs.append(PdfGalleyJob(suffix, path, submission.id, pdf_format, chapter.id))
        volume_suffix = publication.doi.removeprefix("10.21255/")
        if volume_suffix and volume_suffix in volume_pdfs:
            jobs.append(
                PdfGalleyJob(
                    volume_suffix, volume_pdfs[volume_suffix], submission.id, pdf_format, None
                )
            )
    if doi:
        wanted = doi.removeprefix("10.21255/")
        jobs = [job for job in jobs if job.doi_suffix == wanted]
        if not jobs:
            raise typer.BadParameter(f"no PDF galley found for DOI {doi}")
    return jobs


def _replace_pdf_galley(
    client: OmpClient, job: PdfGalleyJob, *, dry_run: bool, replace: bool
) -> None:
    """Replace the existing PDF galley file(s) for one job with the enhanced PDF."""
    existing = [f for f in job.pdf_format.submissionFiles if f.chapterId == job.chapter_id]
    genre_id = existing[0].genreId if existing else None
    if existing and not replace:
        log.info("skip_existing", doi=job.doi_suffix, files=[f.id for f in existing])
        return
    if genre_id is None:
        # No prior galley to inherit a genre from; refuse rather than upload a
        # genre-less file (see the note in ``upload``).
        log.error("no_genre_for_galley", doi=job.doi_suffix, chapter_id=job.chapter_id)
        raise typer.Exit(1)
    if dry_run:
        log.info(
            "dry_run",
            doi=job.doi_suffix,
            submission=job.submission_id,
            format_id=job.pdf_format.id,
            chapter_id=job.chapter_id,
            delete=[f.id for f in existing],
            file=job.pdf_path.name,
        )
        return
    for old in existing:
        client.delete_file(job.submission_id, old.id)
        log.info("deleted_old", doi=job.doi_suffix, file_id=old.id)
    uploaded = client.upload_proof_file(
        job.submission_id,
        job.pdf_path,
        job.pdf_path.name,
        job.pdf_format.id,
        genre_id,
        content_type="application/pdf",
    )
    file_id = uploaded["id"]
    client.publish_galley(job.submission_id, file_id, job.chapter_id)
    log.info(
        "uploaded",
        doi=job.doi_suffix,
        file_id=file_id,
        format_id=job.pdf_format.id,
        chapter_id=job.chapter_id,
    )


@app.command(name="upload-pdf")
def upload_pdf(
    doi: str | None = typer.Option(None, help="Upload a single PDF galley by DOI."),
    dry_run: bool = typer.Option(False, "--dry-run", help="Only print planned API calls."),
    replace: bool = typer.Option(
        False, "--replace", help="Replace the existing PDF galley (delete then re-upload)."
    ),
) -> None:
    """Replace OMP PDF galleys with metadata-enhanced PDFs from ``pdf_dir``.

    Chapter PDFs (``volume-*/chapters/sgb-0X.0Y-*.pdf``) replace their chapter
    galley; full-volume PDFs (``volume-*/sgb-0X-*.pdf``) replace the volume's
    monograph galley. Only replaces content-identical files: the enhanced PDFs
    differ from the originals in embedded metadata only. Run with ``--dry-run``
    first.
    """
    settings = Settings()  # ty: ignore[missing-argument]  # apikey comes from .env
    client = OmpClient(settings.base_url, settings.apikey)
    jobs = _pdf_jobs(settings, client, doi)
    for job in jobs:
        if not job.pdf_path.exists():
            log.error("missing_pdf", doi=job.doi_suffix, path=str(job.pdf_path))
            raise typer.Exit(1)
    failures: list[str] = []
    for job in jobs:
        try:
            _replace_pdf_galley(client, job, dry_run=dry_run, replace=replace)
        except Exception as exc:  # keep going so one bad upload can't abort the batch
            failures.append(job.doi_suffix)
            log.error("upload_pdf_failed", doi=job.doi_suffix, error=redact_token(str(exc)))
    if failures:
        log.error("upload_pdf_incomplete", failed=failures)
        raise typer.Exit(1)


if __name__ == "__main__":
    app()
