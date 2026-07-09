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
            content = request.read()
            assert b"text/html" in content
            # Association is set at upload time; a name field would 500.
            assert b'name="assocType"' in content
            assert b'name="assocId"' in content
            assert b'name="genreId"' in content
            assert b'name="name"' not in content
            return httpx2.Response(200, json={"id": 4711, "assocId": 96})
        assert request.method == "PUT"
        assert request.url.params["stageId"] == "5"
        payload = json.loads(request.read())
        assert payload["chapterId"] == 356
        assert payload["viewable"] is True
        assert payload["salesType"] == "openAccess"
        return httpx2.Response(200, json={"id": 4711, "assocId": 96, "chapterId": 356})

    client = make_client(handler)
    uploaded = client.upload_proof_file(85, html_file, html_file.name, 96, 58)
    assert uploaded["assocId"] == 96
    result = client.attach_to_chapter(85, uploaded["id"], 356)
    assert result["chapterId"] == 356
    assert seen[0].url.path.endswith("/submissions/85/files")
    assert seen[1].url.path.endswith("/submissions/85/files/4711")


def test_delete_file_uses_production_stage() -> None:
    def handler(request: httpx2.Request) -> httpx2.Response:
        assert request.method == "DELETE"
        assert request.url.path.endswith("/submissions/85/files/1570")
        assert request.url.params["stageId"] == "5"
        return httpx2.Response(200, json={})

    make_client(handler).delete_file(85, 1570)


def test_upload_without_genre_omits_field(tmp_path: Path) -> None:
    html_file = tmp_path / "chapter.html"
    html_file.write_text("<!DOCTYPE html>", encoding="utf-8")

    def handler(request: httpx2.Request) -> httpx2.Response:
        assert b'name="genreId"' not in request.read()
        return httpx2.Response(200, json={"id": 4711})

    client = make_client(handler)
    uploaded = client.upload_proof_file(85, html_file, html_file.name, 96, None)
    assert uploaded["id"] == 4711


def test_upload_pdf_content_type(tmp_path: Path) -> None:
    pdf_file = tmp_path / "sgb-09.00-167141.pdf"
    pdf_file.write_bytes(b"%PDF-1.7 enhanced")

    def handler(request: httpx2.Request) -> httpx2.Response:
        content = request.read()
        assert b"application/pdf" in content
        assert b"text/html" not in content
        return httpx2.Response(200, json={"id": 4712})

    client = make_client(handler)
    uploaded = client.upload_proof_file(
        85, pdf_file, pdf_file.name, 86, 58, content_type="application/pdf"
    )
    assert uploaded["id"] == 4712


def test_publish_galley_with_and_without_chapter() -> None:
    seen: list[dict[str, Any]] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        assert request.method == "PUT"
        assert request.url.params["stageId"] == "5"
        seen.append(json.loads(request.read()))
        return httpx2.Response(200, json={"id": 4711})

    client = make_client(handler)
    client.publish_galley(85, 4711, 356)  # chapter galley
    client.publish_galley(85, 4712)  # volume monograph galley
    assert seen[0]["chapterId"] == 356
    assert seen[0]["viewable"] is True
    assert seen[0]["salesType"] == "openAccess"
    assert "chapterId" not in seen[1]
    assert seen[1]["viewable"] is True
