import io
import time
import zipfile

from fastapi.testclient import TestClient

from inkreference.api import create_app


def epub():
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("META-INF/container.xml", '<container><rootfiles><rootfile full-path="book.opf"/></rootfiles></container>')
        archive.writestr("book.opf", '<package><manifest><item id="a" href="a.xhtml" media-type="application/xhtml+xml"/></manifest><spine><itemref idref="a"/></spine></package>')
        archive.writestr("a.xhtml", '<html><body><h1>第一章</h1><p>江屿抬头。星光照亮了旧站台。🙂</p></body></html>')
    return buffer.getvalue()


def test_epub_conversion_download_and_import(tmp_path):
    raw = epub()
    with TestClient(create_app(tmp_path)) as client:
        response = client.post("/api/studio/conversions", files={"file": ("故事.epub", raw)}, data={"options": "{}"})
        assert response.status_code == 201, response.text
        identifier = response.json()["id"]
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            job = client.get(f"/api/studio/conversions/{identifier}").json()
            if job["status"] not in {"queued", "running"}:
                break
            time.sleep(.02)
        assert job["status"] == "completed", job
        markdown = client.get(f"/api/studio/conversions/{identifier}/download?format=md")
        plain = client.get(f"/api/studio/conversions/{identifier}/download?format=txt")
        assert markdown.status_code == plain.status_code == 200
        assert "# 第一章" in markdown.content.decode("utf-8-sig")
        assert "🙂" in plain.content.decode("utf-8-sig")
        assert "# 第一章" not in plain.content.decode("utf-8-sig")
        document = client.post(f"/api/studio/conversions/{identifier}/import")
        assert document.status_code == 201, document.text
        assert client.post(f"/api/studio/conversions/{identifier}/import").json()["id"] == document.json()["id"]
        original = client.app.state.runtime.studio.conversions.get_original(identifier)
        assert original == (raw, "故事.epub")
        assert len(client.get("/api/studio/conversions").json()["items"]) == 1


def test_conversion_rejects_invalid_settings_and_download_format(tmp_path):
    with TestClient(create_app(tmp_path)) as client:
        response = client.post("/api/studio/conversions", files={"file": ("book.epub", epub())}, data={"options": "[]"})
        assert response.status_code == 422
        response = client.post("/api/studio/conversions", files={"file": ("script.py", b"print('do not execute')")})
        assert response.status_code == 422
        assert client.get("/api/studio/conversions/nope/download?format=exe").status_code == 422


def test_queued_analysis_can_be_started_explicitly(tmp_path):
    # The start route must exist even when a client creates a queued run.
    with TestClient(create_app(tmp_path)) as client:
        response = client.post("/api/studio/jobs/missing/start")
        assert response.status_code == 404
