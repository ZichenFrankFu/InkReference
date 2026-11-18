"""Real HTTP, source/metadata and portable restore integration, no model calls."""
from __future__ import annotations

import io
import json
import threading
import time
import zipfile
from pathlib import Path

from fastapi.testclient import TestClient
import pytest

from inkreference.api import create_app
from inkreference.projects import ProjectStore
from inkreference.skills import BUILTIN_SKILL
from inkreference.text_processing import split_chapters

SOURCE = "书名：星舟\n作者：林青\n第一章发布时间：2020-03\n第1章 起程\n他收好钥匙。\n第2章 风雨\n她关上窗。\n"


def wait_for_ingestion(client, identifier, *, timeout=30):
    """Wait for this HTTP job's durable outcome, independent of worker timing."""
    deadline = time.monotonic() + timeout
    while True:
        response = client.get("/api/studio/ingestions/" + identifier)
        assert response.status_code == 200, response.text
        state = response.json()
        if state["status"] == "completed":
            return state
        diagnostic = json.dumps({key: state.get(key) for key in (
            "id", "status", "stage", "progress", "completed", "total", "attempt", "error",
            "document_id", "version_id", "diagnostic_id")}, ensure_ascii=False)
        if state["status"] in {"failed", "cancelled", "interrupted"}:
            raise AssertionError("Ingestion reached an unsuccessful terminal state: " + diagnostic)
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise AssertionError(f"Ingestion exceeded its {timeout}s deadline: " + diagnostic)
        # Poll persisted progress; a bounded thread join does not guarantee that
        # a worker has finished on a busy Windows filesystem.
        time.sleep(min(0.05, remaining))


def test_frontmatter_chapter_publication_label_is_not_a_false_chapter():
    parsed = split_chapters(SOURCE)
    assert not any("发布时间" in chapter.get("raw_marker", "") for chapter in parsed["chapters"])
    assert [chapter["number"] for chapter in parsed["chapters"] if chapter.get("number")] == [1, 2]
    assert "".join(SOURCE[chapter["start"]:chapter["end"]] for chapter in parsed["chapters"]) == SOURCE


def test_upload_metadata_files_revision_and_both_exports_over_http(tmp_path):
    with TestClient(create_app(tmp_path / "workspace", tmp_path / "dist"), base_url="http://127.0.0.1") as client:
        response = client.post("/api/studio/ingestions", files={"file": ("星舟.txt", SOURCE.encode(), "text/plain")},
                               data={"author": "作者甲", "earliest_update_at": "2020", "book_type": "中文小说"})
        assert response.status_code == 201, response.text
        ingestion = response.json()
        completed = wait_for_ingestion(client, ingestion["id"])
        identity, version = completed["document_id"], completed["version_id"]
        base = f"/api/studio/documents/{identity}"
        metadata = client.get(base + "/book-metadata").json()
        assert (metadata["book_title"], metadata["author"], metadata["earliest_update_at"], metadata["book_type"]) == ("星舟", "作者甲", "2020", "中文小说")
        status = client.get(base + "/chapter-files?verify=true").json()
        assert status["state"] == "ready" and status["chapter_count"] >= 2
        archive = client.get(base + "/export/chapters")
        assert archive.status_code == 200
        with zipfile.ZipFile(io.BytesIO(archive.content)) as bundle:
            manifests = [name for name in bundle.namelist() if name.endswith("metadata.json")]
            assert len(manifests) == 1
            exported = json.loads(bundle.read(manifests[0]))
            assert exported["author"] == "作者甲" and exported["version_id"] == version
            assert len([name for name in bundle.namelist() if name.endswith(".txt")]) >= 2
        merged = client.get(base + f"/versions/{version}/download?format=clean")
        assert merged.status_code == 200 and "他收好钥匙。" in merged.text and "她关上窗。" in merged.text
        saved = client.put(base + "/book-metadata", json={"values": {"book_title": "星舟续篇", "author": "作者乙"},
            "expected_version_id": version, "expected_revision": metadata["metadata_revision"]})
        assert saved.status_code == 200, saved.text
        assert client.get(base).json()["title"] == "星舟续篇"
        stale = client.put(base + "/book-metadata", json={"values": {"author": "旧窗口"},
            "expected_version_id": version, "expected_revision": metadata["metadata_revision"]})
        assert stale.status_code == 409
        invalid = client.put(base + "/book-metadata", json={"values": {"earliest_update_at": "2020-99"},
            "expected_version_id": version, "expected_revision": saved.json()["metadata_revision"]})
        assert invalid.status_code == 422
        assert client.get(base + "/book-metadata").json()["author"] == "作者乙"


def test_ingestion_wait_observes_slow_worker_and_reports_deadline_state(tmp_path, monkeypatch):
    entered, release = threading.Event(), threading.Event()
    with TestClient(create_app(tmp_path / "workspace", tmp_path / "dist"), base_url="http://127.0.0.1") as client:
        documents = client.app.state.runtime.studio.documents
        upload = documents.upload
        def blocked_upload(*args, **kwargs):
            entered.set()
            assert release.wait(30), "Controlled ingestion worker was not released"
            return upload(*args, **kwargs)
        monkeypatch.setattr(documents, "upload", blocked_upload)
        response = client.post("/api/studio/ingestions", files={"file": ("星舟.txt", SOURCE.encode(), "text/plain")})
        assert response.status_code == 201, response.text
        identity = response.json()["id"]
        try:
            assert entered.wait(30), "Controlled ingestion worker never started"
            with pytest.raises(AssertionError, match="deadline") as timed_out:
                wait_for_ingestion(client, identity, timeout=0)
            assert identity in str(timed_out.value) and '"status": "running"' in str(timed_out.value)
            assert '"stage": "decoding"' in str(timed_out.value)
            get, observed = client.get, []
            def observe_and_release(path, **kwargs):
                result = get(path, **kwargs)
                if path == "/api/studio/ingestions/" + identity:
                    observed.append(result.json()["status"])
                    if len(observed) == 2:
                        release.set()
                return result
            monkeypatch.setattr(client, "get", observe_and_release)
            completed = wait_for_ingestion(client, identity)
            assert observed[:2] == ["running", "running"]
            assert observed[-1] == "completed" and completed["document_id"]
        finally:
            release.set()


def test_ingestion_wait_fails_immediately_with_terminal_error_details(tmp_path, monkeypatch):
    with TestClient(create_app(tmp_path / "workspace", tmp_path / "dist"), base_url="http://127.0.0.1") as client:
        def failed_upload(*args, **kwargs):
            raise RuntimeError("synthetic ingestion failure")
        monkeypatch.setattr(client.app.state.runtime.studio.documents, "upload", failed_upload)
        response = client.post("/api/studio/ingestions", files={"file": ("星舟.txt", SOURCE.encode(), "text/plain")})
        assert response.status_code == 201, response.text
        identity = response.json()["id"]
        with pytest.raises(AssertionError, match="unsuccessful terminal state") as failed:
            wait_for_ingestion(client, identity)
        assert "synthetic ingestion failure" in str(failed.value)
        assert identity in str(failed.value) and '"status": "failed"' in str(failed.value)
        assert '"stage": "failed"' in str(failed.value)


def test_document_list_catalog_labels_are_batched_and_do_not_read_full_text(tmp_path, monkeypatch):
    with TestClient(create_app(tmp_path / "workspace", tmp_path / "dist"), base_url="http://127.0.0.1") as client:
        runtime = client.app.state.runtime
        work = runtime.store.create_record("works", {"作品ID": "work-a", "作品": "关联作品", "类别": "电影", "作者": "作者乙"})
        item = runtime.studio.documents.upload("电影.txt", "第1章\n正文。".encode(), work_id=work["id"])
        monkeypatch.setattr(runtime.studio.documents, "get_version", lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("list loaded source")))
        rows = client.get("/api/studio/documents").json()["items"]
        selected = next(row for row in rows if row["id"] == item["id"])
        assert selected["work_category"] == "电影" and selected["catalog_author"] == "作者乙"
        assert "text" not in selected


def test_new_ui_rejects_legacy_skill_upload_before_persistence(tmp_path):
    with TestClient(create_app(tmp_path / "workspace", tmp_path / "dist"), base_url="http://127.0.0.1") as client:
        before = client.get("/api/studio/skills").json()
        bad = {**BUILTIN_SKILL, "id": "foreign-contract", "version": "1.0.0"}
        response = client.post("/api/studio/skills/upload?contract_version=2", files={"file": ("skill.json", json.dumps(bad).encode(), "application/json")})
        assert response.status_code == 422, response.text
        assert client.get("/api/studio/skills").json() == before


def test_project_restore_regenerates_identical_current_chapter_files(tmp_path):
    with TestClient(create_app(tmp_path / "workspace", tmp_path / "dist"), base_url="http://127.0.0.1") as client:
        runtime = client.app.state.runtime
        runtime.store.stats()  # Portable project requires the material database.
        document = runtime.studio.documents.upload("星舟.txt", SOURCE.encode(), book_metadata={"author": "作者甲"})
        status = runtime.studio.documents.storage_status(document["id"], verify=True)
        source_dir = tmp_path / "workspace" / "books" / status["storage_folder"]
        original = {path.name: path.read_bytes() for path in source_dir.iterdir() if path.is_file()}
        projects = ProjectStore(tmp_path / "workspace")
        raw = projects.export()
        with zipfile.ZipFile(io.BytesIO(raw)) as bundle:
            assert not any(name.startswith("books/") for name in bundle.namelist())
        restored = Path(projects.restore(raw)["data_dir"])
        recreated = restored / "books" / status["storage_folder"]
        assert {path.name: path.read_bytes() for path in recreated.iterdir() if path.is_file()} == original


def test_chronicle_projection_uses_volume_plan_for_exact_source_version(tmp_path):
    with TestClient(create_app(tmp_path / "workspace", tmp_path / "dist"), base_url="http://127.0.0.1") as client:
        docs = client.app.state.runtime.studio.documents
        document = docs.upload("星舟.txt", "第1章 起程\n他收好钥匙。\n第2章 风雨\n她关上窗。".encode(), preserve_text=True)
        version = docs.get_version(document["id"])
        anchor = {"revealed_at": {"chapter": 1, "offset": version["text"].index("他收好钥匙。")},
                  "source_version_id": version["id"], "evidence": "他收好钥匙。"}
        event = {"id": "event-keys", "name": "携钥匙出发", "description": "他带走钥匙。", "participants": [],
            "setting_ids": [], "causes": [], "results": [], "plotline_ids": [], "story_time": {"label": "", "sort_key": None}, "volume": None, **anchor}
        base = f"/api/studio/documents/{document['id']}"
        saved = client.put(base + "/reference/plot", json={"expected_revision": 0,
            "data": {"schema_version": 2, "logline": "", "events": [event], "plotlines": []}})
        assert saved.status_code == 200, saved.text
        plan = client.put(base + f"/versions/{version['id']}/volumes", json={"expected_revision": 0,
            "volumes": [{"title": "起程卷", "start_chapter": 1, "end_chapter": 2}]})
        assert plan.status_code == 200, plan.text
        projected = client.get(base + "/reference/projection?chapter=1")
        assert projected.status_code == 200, projected.text
        outline = projected.json()["sections"]["plot"]["outlines"]["volumes"][0]
        assert outline["volume"] == "起程卷" and outline["event_ids"] == ["event-keys"]
        assert client.get(base + "/reference").json()["sections"]["plot"]["events"][0]["volume"] is None
