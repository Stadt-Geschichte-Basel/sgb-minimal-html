"""Unit tests for the OMP API client against a mock transport."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import httpx2

from sgb_html.omp import ApiChapter, ApiPublication, ApiSubmission, OmpClient

SUBMISSION: dict[str, Any] = {
    "id": 85,
    "currentPublicationId": 77,
    "publications": [
        {
            "id": 77,
            "fullTitle": {"de": "Basel 1856–1914"},
            "urlPublished": "https://emono.unibas.ch/stadtgeschichtebasel/catalog/book/85",
            "chapterLicenseUrl": "https://creativecommons.org/licenses/by-nc/4.0/",
            "chapters": [
                {
                    "id": 356,
                    "title": {"de": "Zur Einführung"},
                    "subtitle": {"de": ""},
                    "pages": "11-17",
                    "doiObject": {"doi": "10.21255/sgb-09.00-167141"},
                    "authors": {
                        "519": {
                            "givenName": {"de": "Lina"},
                            "familyName": {"de": "Gafner"},
                            "seq": 1,
                        },
                        "514": {
                            "givenName": {"de": "Esther"},
                            "familyName": {"de": "Baur"},
                            "seq": 0,
                        },
                    },
                }
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
                        }
                    ],
                },
                {"id": 96, "name": {"de": "HTML"}, "isAvailable": 0, "submissionFiles": []},
            ],
        }
    ],
}


def make_client(handler) -> OmpClient:
    transport = httpx2.MockTransport(handler)
    http = httpx2.Client(
        base_url="https://example.org/api/v1", params={"apiToken": "t"}, transport=transport
    )
    return OmpClient("https://example.org/api/v1", "t", client=http)


def test_submissions_fetches_details() -> None:
    def handler(request: httpx2.Request) -> httpx2.Response:
        assert request.url.params["apiToken"] == "t"
        if request.url.path.endswith("/submissions"):
            return httpx2.Response(200, json={"itemsMax": 1, "items": [{"id": 85}]})
        assert request.url.path.endswith("/submissions/85")
        return httpx2.Response(200, json=SUBMISSION)

    (submission,) = make_client(handler).submissions()
    publication = submission.current_publication
    assert publication.volume_title == "Basel 1856–1914"
    chapter = publication.chapters[0]
    assert chapter.doi == "10.21255/sgb-09.00-167141"
    assert chapter.author_names == ("Esther Baur", "Lina Gafner")
    html_format = publication.format_named("HTML")
    assert html_format is not None and html_format.id == 96
    assert publication.format_named("EPUB") is None


def test_current_publication_falls_back_to_first() -> None:
    data = dict(SUBMISSION, currentPublicationId=999)
    publication = ApiPublication.model_validate(SUBMISSION["publications"][0])
    submission = ApiSubmission.model_validate(data)
    assert submission.current_publication.id == publication.id


def test_chapter_author_list_form_and_empty() -> None:
    chapter = ApiChapter.model_validate(
        {"id": 1, "authors": [{"givenName": "A", "familyName": "B", "seq": 0}]}
    )
    assert chapter.author_names == ("A B",)
    assert ApiChapter.model_validate({"id": 2}).author_names == ()
    assert ApiChapter.model_validate({"id": 3}).doi == ""


def test_upload_and_attach(tmp_path: Path) -> None:
    html_file = tmp_path / "sgb-09.00-167141.html"
    html_file.write_text("<!DOCTYPE html>", encoding="utf-8")
    seen: list[httpx2.Request] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        seen.append(request)
        if request.method == "POST":
            assert b"text/html" in request.read()
            return httpx2.Response(200, json={"id": 4711})
        assert request.method == "PUT"
        payload = json.loads(request.read())
        assert payload["assocType"] == 521
        assert payload["assocId"] == 96
        assert payload["chapterId"] == 356
        assert payload["genreId"] == 58
        assert payload["viewable"] is True
        return httpx2.Response(200, json={"id": 4711, "assocId": 96})

    client = make_client(handler)
    uploaded = client.upload_proof_file(85, html_file, html_file.name)
    result = client.attach_to_format(85, uploaded["id"], 96, 356, 58)
    assert result["assocId"] == 96
    assert seen[0].url.path.endswith("/submissions/85/files")
    assert seen[1].url.path.endswith("/submissions/85/files/4711")


def test_attach_without_genre_omits_field(tmp_path: Path) -> None:
    def handler(request: httpx2.Request) -> httpx2.Response:
        payload = json.loads(request.read())
        assert "genreId" not in payload
        return httpx2.Response(200, json=payload)

    client = make_client(handler)
    result = client.attach_to_format(85, 4711, 96, 356, None)
    assert result["assocId"] == 96
