"""Thin client for the Open Monograph Press REST API of emono.unibas.ch."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import httpx2
from pydantic import BaseModel, ConfigDict

LOCALE = "de"
FILE_STAGE_PROOF = 10
ASSOC_TYPE_REPRESENTATION = 521
WORKFLOW_STAGE_PRODUCTION = 5

_TOKEN_RE = re.compile(r"(apiToken=)[^&\s]+")


def redact_token(text: str) -> str:
    """Mask the ``apiToken`` query param so the secret never reaches logs.

    httpx renders request URLs (including the auth query param) into its
    exception strings, so raw ``str(exc)`` values must be scrubbed before
    logging.
    """
    return _TOKEN_RE.sub(r"\1[REDACTED]", text)


def _de(value: dict[str, str] | str | None) -> str:
    if isinstance(value, dict):
        return value.get(LOCALE, "") or ""
    return value or ""


class ApiAuthor(BaseModel):
    model_config = ConfigDict(extra="ignore")

    givenName: dict[str, str] | str | None = None
    familyName: dict[str, str] | str | None = None
    seq: int = 0

    @property
    def display_name(self) -> str:
        return f"{_de(self.givenName)} {_de(self.familyName)}".strip()


class ApiDoi(BaseModel):
    model_config = ConfigDict(extra="ignore")

    doi: str = ""


class ApiChapter(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id: int
    title: dict[str, str] | str | None = None
    subtitle: dict[str, str] | str | None = None
    pages: str | None = None
    doiObject: ApiDoi | None = None
    authors: dict[str, ApiAuthor] | list[ApiAuthor] | None = None

    @property
    def doi(self) -> str:
        return self.doiObject.doi if self.doiObject else ""

    @property
    def author_names(self) -> tuple[str, ...]:
        authors = self.authors or {}
        values = list(authors.values()) if isinstance(authors, dict) else list(authors)
        values.sort(key=lambda a: a.seq)
        return tuple(a.display_name for a in values if a.display_name)


class ApiSubmissionFile(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id: int
    name: dict[str, str] | str | None = None
    chapterId: int | None = None
    genreId: int | None = None

    @property
    def file_name(self) -> str:
        return _de(self.name)


class ApiPublicationFormat(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id: int
    name: dict[str, str] | str | None = None
    isAvailable: int | bool = 0
    submissionFiles: list[ApiSubmissionFile] = []

    @property
    def format_name(self) -> str:
        return _de(self.name)


class ApiPublication(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id: int
    fullTitle: dict[str, str] | str | None = None
    urlPublished: str = ""
    chapterLicenseUrl: str = ""
    doiObject: ApiDoi | None = None
    chapters: list[ApiChapter] = []
    publicationFormats: list[ApiPublicationFormat] = []

    @property
    def volume_title(self) -> str:
        return _de(self.fullTitle)

    @property
    def doi(self) -> str:
        """The volume-level (monograph) DOI, empty when unset."""
        return self.doiObject.doi if self.doiObject else ""

    def format_named(self, name: str) -> ApiPublicationFormat | None:
        for fmt in self.publicationFormats:
            if fmt.format_name == name:
                return fmt
        return None


class ApiSubmission(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id: int
    currentPublicationId: int
    publications: list[ApiPublication] = []

    @property
    def current_publication(self) -> ApiPublication:
        for publication in self.publications:
            if publication.id == self.currentPublicationId:
                return publication
        return self.publications[0]


class OmpClient:
    """Minimal OMP REST client authenticated via ``apiToken``."""

    def __init__(self, base_url: str, api_token: str, client: httpx2.Client | None = None) -> None:
        # Generous read/write timeouts: PDF galleys run to ~120 MB and easily
        # exceed a 60 s write timeout on the upload.
        self._client = client or httpx2.Client(
            base_url=base_url,
            params={"apiToken": api_token},
            timeout=httpx2.Timeout(60.0, read=300.0, write=600.0),
        )

    def submissions(self) -> list[ApiSubmission]:
        response = self._client.get("/submissions", params={"count": 50})
        response.raise_for_status()
        items = response.json()["items"]
        detailed = []
        for item in items:
            detailed.append(self.submission(item["id"]))
        return detailed

    def submission(self, submission_id: int) -> ApiSubmission:
        response = self._client.get(f"/submissions/{submission_id}")
        response.raise_for_status()
        return ApiSubmission.model_validate(response.json())

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
        """Upload a galley file into the proof stage of a publication format.

        Defaults to an HTML galley; pass ``content_type="application/pdf"`` to
        upload a PDF galley. The association with the publication format must be
        set at upload time; the edit endpoint rejects ``assocType`` changes. A
        ``name`` form field triggers a server error, so the display name comes
        from the multipart file name.
        """
        data = {
            "fileStage": str(FILE_STAGE_PROOF),
            "assocType": str(ASSOC_TYPE_REPRESENTATION),
            "assocId": str(format_id),
        }
        if genre_id is not None:
            data["genreId"] = str(genre_id)
        with path.open("rb") as handle:
            response = self._client.post(
                f"/submissions/{submission_id}/files",
                data=data,
                files={"file": (name, handle, content_type)},
            )
        response.raise_for_status()
        return response.json()

    def delete_file(self, submission_id: int, file_id: int) -> None:
        response = self._client.delete(
            f"/submissions/{submission_id}/files/{file_id}",
            params={"stageId": WORKFLOW_STAGE_PRODUCTION},
        )
        response.raise_for_status()

    def edit_file(self, submission_id: int, file_id: int, fields: dict[str, Any]) -> dict[str, Any]:
        response = self._client.put(
            f"/submissions/{submission_id}/files/{file_id}",
            params={"stageId": WORKFLOW_STAGE_PRODUCTION},
            json=fields,
        )
        response.raise_for_status()
        return response.json()

    def publish_galley(
        self, submission_id: int, file_id: int, chapter_id: int | None = None
    ) -> dict[str, Any]:
        """Make an uploaded galley file viewable and open access.

        Pass ``chapter_id`` to link the file to a chapter (chapter galleys);
        omit it for a volume-level monograph galley, which has no chapter.
        """
        fields: dict[str, Any] = {
            "viewable": True,
            "salesType": "openAccess",
            "directSalesPrice": "0",
        }
        if chapter_id is not None:
            fields["chapterId"] = chapter_id
        return self.edit_file(submission_id, file_id, fields)

    def attach_to_chapter(
        self, submission_id: int, file_id: int, chapter_id: int
    ) -> dict[str, Any]:
        """Link an uploaded galley file to its chapter and make it viewable."""
        return self.publish_galley(submission_id, file_id, chapter_id)
