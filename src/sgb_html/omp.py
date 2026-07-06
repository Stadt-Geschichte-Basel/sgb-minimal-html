"""Thin client for the Open Monograph Press REST API of emono.unibas.ch."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import httpx2
from pydantic import BaseModel, ConfigDict

LOCALE = "de"
FILE_STAGE_PROOF = 10
ASSOC_TYPE_REPRESENTATION = 521


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
    chapters: list[ApiChapter] = []
    publicationFormats: list[ApiPublicationFormat] = []

    @property
    def volume_title(self) -> str:
        return _de(self.fullTitle)

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
        self._client = client or httpx2.Client(
            base_url=base_url, params={"apiToken": api_token}, timeout=60.0
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

    def upload_proof_file(self, submission_id: int, path: Path, name: str) -> dict[str, Any]:
        """Upload an HTML galley file into the proof file stage."""
        with path.open("rb") as handle:
            response = self._client.post(
                f"/submissions/{submission_id}/files",
                data={"fileStage": str(FILE_STAGE_PROOF), "name": name},
                files={"file": (name, handle, "text/html")},
            )
        response.raise_for_status()
        return response.json()

    def edit_file(self, submission_id: int, file_id: int, fields: dict[str, Any]) -> dict[str, Any]:
        response = self._client.put(f"/submissions/{submission_id}/files/{file_id}", json=fields)
        response.raise_for_status()
        return response.json()

    def attach_to_format(
        self,
        submission_id: int,
        file_id: int,
        format_id: int,
        chapter_id: int,
        genre_id: int | None,
    ) -> dict[str, Any]:
        """Link an uploaded file to a publication format and chapter."""
        fields: dict[str, Any] = {
            "assocType": ASSOC_TYPE_REPRESENTATION,
            "assocId": format_id,
            "fileStage": FILE_STAGE_PROOF,
            "chapterId": chapter_id,
            "viewable": True,
            "salesType": "openAccess",
            "directSalesPrice": "0",
        }
        if genre_id is not None:
            fields["genreId"] = genre_id
        return self.edit_file(submission_id, file_id, fields)
