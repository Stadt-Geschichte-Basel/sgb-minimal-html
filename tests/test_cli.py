"""Tests for the PDF-galley upload helpers in the CLI."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

from sgb_html.cli import _pdf_jobs, _replace_pdf_galley, _volume_pdfs
from sgb_html.omp import ApiSubmission, OmpClient
from sgb_html.settings import Settings

SUBMISSION: dict[str, Any] = {
    "id": 85,
    "currentPublicationId": 77,
    "publications": [
        {
            "id": 77,
            "fullTitle": {"de": "Basel 1856–1914"},
            "doiObject": {"doi": "10.21255/sgb-09-486500"},
            "chapters": [
                {"id": 356, "doiObject": {"doi": "10.21255/sgb-09.00-167141"}},
            ],
            "publicationFormats": [
                {
                    "id": 86,
                    "name": {"de": "PDF"},
                    "isAvailable": 1,
                    "submissionFiles": [
                        {
                            "id": 978,
                            "name": {"de": "sgb-09.00-167141.pdf"},
                            "chapterId": 356,
                            "genreId": 58,
                        },
                        {
                            "id": 900,
                            "name": {"de": "sgb-09-486500.pdf"},
                            "chapterId": None,
                            "genreId": 12,
                        },
                    ],
                },
            ],
        }
    ],
}


class FakeClient:
    """Records the galley-management calls made by the helpers."""

    def __init__(self, submission: ApiSubmission) -> None:
        self._submission = submission
        self.deleted: list[tuple[int, int]] = []
        self.uploaded: list[dict[str, Any]] = []
        self.published: list[tuple[int, int, int | None]] = []

    def submissions(self) -> list[ApiSubmission]:
        return [self._submission]

    def delete_file(self, submission_id: int, file_id: int) -> None:
        self.deleted.append((submission_id, file_id))

    def upload_proof_file(
        self,
        submission_id: int,
        path: Path,
        name: str,
        format_id: int,
        genre_id: int | None,
        *,
        content_type: str = "text/html",
    ) -> dict[str, Any]:
        self.uploaded.append(
            {
                "name": name,
                "format_id": format_id,
                "genre_id": genre_id,
                "content_type": content_type,
            }
        )
        return {"id": 5000 + len(self.uploaded)}

    def publish_galley(
        self, submission_id: int, file_id: int, chapter_id: int | None = None
    ) -> dict[str, Any]:
        self.published.append((submission_id, file_id, chapter_id))
        return {"id": file_id}


def _make_pdfs(tmp_path: Path) -> Path:
    chapters = tmp_path / "volume-09" / "chapters"
    chapters.mkdir(parents=True)
    (chapters / "sgb-09.00-167141.pdf").write_bytes(b"%PDF chapter")
    (tmp_path / "volume-09" / "sgb-09-486500.pdf").write_bytes(b"%PDF volume")
    return tmp_path


def _submission() -> ApiSubmission:
    return ApiSubmission.model_validate(SUBMISSION)


def test_volume_pdfs_ignores_chapter_pdfs(tmp_path: Path) -> None:
    _make_pdfs(tmp_path)
    volumes = _volume_pdfs(tmp_path)
    assert set(volumes) == {"sgb-09-486500"}  # chapter PDF (has a dot) is excluded


def test_pdf_jobs_matches_chapters_and_volumes(tmp_path: Path) -> None:
    settings = cast(Settings, SimpleNamespace(pdf_dir=_make_pdfs(tmp_path)))
    jobs = _pdf_jobs(settings, cast(OmpClient, FakeClient(_submission())), None)
    by_suffix = {job.doi_suffix: job for job in jobs}
    assert by_suffix["sgb-09.00-167141"].chapter_id == 356
    assert by_suffix["sgb-09-486500"].chapter_id is None


def test_replace_chapter_galley(tmp_path: Path) -> None:
    settings = cast(Settings, SimpleNamespace(pdf_dir=_make_pdfs(tmp_path)))
    client = FakeClient(_submission())
    job = next(j for j in _pdf_jobs(settings, cast(OmpClient, client), None) if j.chapter_id == 356)
    _replace_pdf_galley(cast(OmpClient, client), job, dry_run=False, replace=True)
    assert client.deleted == [(85, 978)]  # old chapter galley removed
    assert client.uploaded[0]["content_type"] == "application/pdf"
    assert client.uploaded[0]["genre_id"] == 58  # inherited from the old file
    assert client.published[0][2] == 356  # attached to the chapter


def test_replace_volume_monograph_galley(tmp_path: Path) -> None:
    settings = cast(Settings, SimpleNamespace(pdf_dir=_make_pdfs(tmp_path)))
    client = FakeClient(_submission())
    job = next(
        j for j in _pdf_jobs(settings, cast(OmpClient, client), None) if j.chapter_id is None
    )
    _replace_pdf_galley(cast(OmpClient, client), job, dry_run=False, replace=True)
    assert client.deleted == [(85, 900)]  # old monograph galley removed
    assert client.published[0][2] is None  # no chapter for a volume galley


def test_replace_skips_without_replace_flag(tmp_path: Path) -> None:
    settings = cast(Settings, SimpleNamespace(pdf_dir=_make_pdfs(tmp_path)))
    client = FakeClient(_submission())
    job = next(j for j in _pdf_jobs(settings, cast(OmpClient, client), None) if j.chapter_id == 356)
    _replace_pdf_galley(cast(OmpClient, client), job, dry_run=False, replace=False)
    assert client.deleted == []
    assert client.uploaded == []


def test_dry_run_makes_no_calls(tmp_path: Path) -> None:
    settings = cast(Settings, SimpleNamespace(pdf_dir=_make_pdfs(tmp_path)))
    client = FakeClient(_submission())
    job = next(j for j in _pdf_jobs(settings, cast(OmpClient, client), None) if j.chapter_id == 356)
    _replace_pdf_galley(cast(OmpClient, client), job, dry_run=True, replace=True)
    assert client.deleted == []
    assert client.uploaded == []
    assert client.published == []
