"""HTTP contracts for portable material libraries and the unified source workflow.

All files contain invented fixtures. EPUB conversion is real; these checks make
no model requests and do not stand in for PDF OCR or interactive Windows IME.
"""
from __future__ import annotations

import io
import threading
import time
import zipfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from openpyxl import load_workbook

from inkreference.api import create_app
from inkreference.database import TABLES
from inkreference.library_exchange import COLUMNS

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture
def client(tmp_path):
    with TestClient(create_app(tmp_path / "data", tmp_path / "absent-dist"), base_url="http://127.0.0.1") as session:
        yield session


def checked(response, status=200):
    assert response.status_code == status, response.text
    return response.json()


def csv_files():
    names = {"works": "works_library_v8.csv", "characters": "character_database_v8.csv", "cliches": "cliche_database_v8.csv"}
    return [("files", (names[table], (FIXTURES / f"{table}.csv").read_bytes(), "text/csv")) for table in TABLES]


def preview(client, files=None, *, legacy=False):
    return checked(client.post("/api/import/preview" if legacy else "/api/library/import/preview", files=files or csv_files()))


def commit(client, batch, *, legacy=False):
    return checked(client.post("/api/import/commit" if legacy else "/api/library/import/commit", json={"batch_id": batch["batch_id"], "policy": "skip"}))


def populate(client):
    batch = preview(client)
    assert not batch["errors"], batch
    commit(client, batch)
    relation = {
        "id": "R-HTTP", "source_table": "characters", "source_id": "CH-W900-01",
        "target_table": "works", "target_id": "W902", "relation_type": "版本人物对照",
        "description": '以电影版为对照。\r\n保留“换行”与 emoji😀。',
        "metadata_json": '{"出处":"合成 fixture"}',
    }
    checked(client.post("/api/relations", json={"values": relation}), 201)
    return relation


def snapshot(client):
    return {
        "tables": {table: [row["values"] for row in checked(client.get(f"/api/records/{table}?limit=500"))["items"]] for table in TABLES},
        "relations": [{key: value for key, value in row.items() if key not in {"revision", "created_at", "updated_at"}} for row in checked(client.get("/api/relations"))["items"]],
        "stats": checked(client.get("/api/stats")),
    }


@pytest.mark.parametrize("format", ["db", "csv", "xlsx"])
def test_library_http_roundtrip_all_tables_meaning_and_semantics(client, tmp_path, format):
    relation = populate(client)
    before = snapshot(client)
    exported = client.get("/api/library/export", params={"format": format})
    assert exported.status_code == 200
    extension = "zip" if format == "csv" else format
    assert f"InkReference-library{ '-csv' if format == 'csv' else ''}.{extension}" in exported.headers["content-disposition"]
    if format == "db":
        assert exported.content.startswith(b"SQLite format 3\x00")
    with TestClient(create_app(tmp_path / f"restored-{format}", tmp_path / "other-dist"), base_url="http://127.0.0.1") as target:
        batch = preview(target, [("files", (f"library.{extension}", exported.content, exported.headers["content-type"]))])
        assert batch["errors"] == [] and batch["warnings"] == []
        assert {item["table"] for item in batch["required_tables"] if item["present"]} == set(COLUMNS)
        assert checked(target.get("/api/stats"))["works"] == 0  # preview is read only
        commit(target, batch)
        assert snapshot(target) == before
        related = checked(target.get("/api/records/characters/CH-W900-01/relations"))
        assert any(item["relation_type"] == relation["relation_type"] and item["description"] == relation["description"] for item in related["edges"])
        repeated = preview(target, [("files", (f"library.{extension}", exported.content, exported.headers["content-type"]))])
        outcome = commit(target, repeated)
        assert outcome["skipped"] == 8 and outcome["relations_skipped"] == 1
        assert snapshot(target) == before


def test_library_http_missing_csvs_gives_names_and_does_not_commit(client):
    batch = preview(client, csv_files()[:1])
    message = " ".join(batch["errors"])
    assert "character_database_v8.csv" in message and "cliche_database_v8.csv" in message
    assert not next(item for item in batch["required_tables"] if item["table"] == "characters")["present"]
    response = client.post("/api/library/import/commit", json={"batch_id": batch["batch_id"], "policy": "skip"})
    assert response.status_code == 409
    assert checked(client.get("/api/stats"))["works"] == 0


def test_library_http_version_pair_diagnostic_is_actionable(client):
    files = csv_files()
    _, (_, raw, mime) = files[1]
    files[1] = ("files", ("character_database_v7.csv", raw, mime))
    batch = preview(client, files)
    message = " ".join(batch["errors"])
    assert "character_database_v7.csv" in message and "works_library_v8.csv" in message
    assert "版本疑似不一致" in message
    assert checked(client.get("/api/stats"))["works"] == 0


def test_library_http_mixed_export_batches_identifies_the_two_files(client):
    populate(client)
    def unpack():
        response = client.get("/api/library/export?format=csv")
        with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
            return {name: archive.read(name) for name in archive.namelist()}
    first, second = unpack(), unpack()
    first["characters.csv"] = second["characters.csv"]
    files = [("files", (name, data, "application/json" if name.endswith(".json") else "text/csv")) for name, data in first.items()]
    before = snapshot(client)
    batch = preview(client, files)
    message = " ".join(batch["errors"])
    assert "characters.csv" in message and "manifest.json" in message and "导出批次不一致" in message
    assert client.post("/api/library/import/commit", json={"batch_id": batch["batch_id"], "policy": "update"}).status_code == 409
    assert snapshot(client) == before


def test_library_http_missing_xlsx_sheet_names_the_required_table(client):
    populate(client)
    workbook = load_workbook(io.BytesIO(client.get("/api/library/export?format=xlsx").content))
    del workbook["characters"]
    output = io.BytesIO()
    workbook.save(output)
    workbook.close()
    batch = preview(client, [("files", ("missing-characters.xlsx", output.getvalue(), "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"))])
    assert "characters.csv" in " ".join(batch["errors"])
    assert "缺少必要工作表" in " ".join(batch["errors"])


@pytest.mark.parametrize("legacy_first", [False, True])
def test_import_tokens_cannot_cross_between_legacy_and_complete_library_flows(client, legacy_first):
    batch = preview(client, legacy=legacy_first)
    wrong = "/api/library/import/commit" if legacy_first else "/api/import/commit"
    response = client.post(wrong, json={"batch_id": batch["batch_id"], "policy": "skip"})
    assert response.status_code == 404
    assert checked(client.get("/api/stats"))["works"] == 0
    commit(client, batch, legacy=legacy_first)
    assert checked(client.get("/api/stats"))["works"] > 0
    assert client.post(wrong, json={"batch_id": batch["batch_id"], "policy": "skip"}).status_code == 404


def ingest(client, raw, filename="source.txt", **fields):
    mime = "application/epub+zip" if filename.endswith(".epub") else "text/plain"
    return checked(client.post("/api/studio/ingestions", data=fields, files={"file": (filename, raw, mime)}), 201)


def finish(client, job, timeout=8):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        state = checked(client.get(f"/api/studio/ingestions/{job['id']}"))
        if state["status"] in {"completed", "failed", "cancelled", "interrupted"}:
            assert state["status"] == "completed", state
            assert state["progress"] == 1 and state["completed"] == state["total"]
            return state
        time.sleep(0.01)
    raise AssertionError(state)


def get_version(client, job):
    return checked(client.get(f"/api/studio/documents/{job['document_id']}/versions/{job['version_id']}"))


def test_txt_async_http_measured_progress_and_non_destructive_findings(client, monkeypatch):
    ingestion = client.app.state.runtime.studio.ingestions
    entered, release = threading.Event(), threading.Event()
    update = ingestion._update
    def hold_scan(identifier, **fields):
        update(identifier, **fields)
        if fields.get("stage") == "scan" and fields.get("completed") == 0:
            entered.set()
            assert release.wait(5)
    monkeypatch.setattr(ingestion, "_update", hold_scan)
    raw = "第一章 初见\r\n🌟星舟\ue001启航。\r\n作者说：求月票，感谢支持！\r\n第二章 返航\r\n尾标。\r\n".encode()
    try:
        job = ingest(client, raw)
        assert entered.wait(3)
        progress = checked(client.get(f"/api/studio/ingestions/{job['id']}"))
        assert progress["status"] == "running" and progress["stage"] == "scan"
        assert progress["progress"] == 0 and progress["total"] == 2
    finally:
        release.set()
    done = finish(client, job)
    version = get_version(client, done)
    assert version["text"] == raw.decode()  # CRLF, glyph marker and non-body text survive import
    path = f"/api/studio/documents/{done['document_id']}/versions/{done['version_id']}"
    items = checked(client.get(path + "/findings"))["items"]
    assert any(item["kind"] == "missing_glyph" and not item["removable"] for item in items)
    assert any(item["kind"] == "author_note" and item["removable"] for item in items)
    for item in items:
        chapter = version["chapters"][item["chapter_index"]]
        assert item["text"] == chapter["text"][item["start"]:item["end"]]
    assert client.get(f"/api/studio/documents/{done['document_id']}/original/download").content == raw
    assert checked(client.get("/api/studio/usage"))["items"] == []


def test_ingestion_http_append_replace_source_activation_and_stale_target(client):
    first_raw = "第一章 初见\n首稿不可丢。".encode()
    first = finish(client, ingest(client, first_raw, filename="first.txt"))
    tail_raw = "第二章 前行\n补充尾标😀。".encode()
    appended = finish(client, ingest(client, tail_raw, filename="tail.txt", mode="append", target_document_id=first["document_id"], base_version_id=first["version_id"]))
    assert get_version(client, appended)["text"].startswith(first_raw.decode())
    assert get_version(client, appended)["text"].endswith(tail_raw.decode())
    new_raw = "第一章 更新\r\n替换全文。\r\n".encode()
    replaced = finish(client, ingest(client, new_raw, filename="replacement.txt", mode="replace", target_document_id=first["document_id"], base_version_id=appended["version_id"]))
    assert get_version(client, replaced)["text"] == new_raw.decode()
    assert get_version(client, first)["text"] == first_raw.decode()
    sources = checked(client.get(f"/api/studio/documents/{first['document_id']}/sources"))["items"]
    assert [(item["filename"], item["active"]) for item in sources] == [("first.txt", False), ("tail.txt", False), ("replacement.txt", True)]
    stale = client.post("/api/studio/ingestions", data={"mode": "append", "target_document_id": first["document_id"], "base_version_id": first["version_id"]}, files={"file": ("stale.txt", b"stale", "text/plain")})
    assert stale.status_code == 409
    assert len(checked(client.get("/api/studio/ingestions"))["items"]) == 3
    assert get_version(client, replaced)["text"] == new_raw.decode()


def synthetic_epub():
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w") as archive:
        archive.writestr("META-INF/container.xml", '<container><rootfiles><rootfile full-path="book.opf"/></rootfiles></container>')
        archive.writestr("book.opf", '<package><manifest><item id="b" href="b.xhtml"/><item id="a" href="a.xhtml"/></manifest><spine><itemref idref="a"/><itemref idref="b"/></spine></package>')
        archive.writestr("a.xhtml", '<html><body><h1>第一章 初见</h1><p>星舟<strong>启航</strong>😀。</p></body></html>')
        archive.writestr("b.xhtml", '<html><body><h1>第二章 尾声</h1><p>结尾尾标。</p></body></html>')
    return output.getvalue()


def test_epub_http_converts_to_plain_txt_before_chapter_parsing(client):
    raw = synthetic_epub()
    done = finish(client, ingest(client, raw, filename="synthetic.epub", title="合成双章"))
    version = get_version(client, done)
    assert "星舟启航😀。" in version["text"] and "<strong>" not in version["text"]
    assert version["text"].index("第一章") < version["text"].index("第二章")
    assert len(version["chapters"]) == 2 and version["text"].endswith("结尾尾标。")
    assert client.get(f"/api/studio/documents/{done['document_id']}/original/download").content == raw
    conversion = checked(client.get(f"/api/studio/conversions/{done['conversion_id']}"))
    assert conversion["status"] == "completed" and conversion["outputs"] == {"markdown": True, "text": True}
    assert checked(client.get("/api/studio/usage"))["items"] == []


def test_long_chapter_http_slice_edit_preserves_unseen_unicode_and_revision_guard(client):
    raw = "第一章 长章\r\n" + "甲😀乙" * 75000 + "\r\n第二章 尾章\r\n尾章不可丢。"
    done = finish(client, ingest(client, raw.encode()))
    base = f"/api/studio/documents/{done['document_id']}"
    page = checked(client.get(f"{base}/versions/{done['version_id']}/chapters/chapter-1?start=40000&limit=20000"))
    assert page["body_text"] is None and len(page["editable_text"]) == 20000
    payload = {"base_version_id": done["version_id"], "chapter_id": "chapter-1", "start": page["editable_start"], "end": page["editable_end"], "text": "😀修订标记😀", "expected_text": page["editable_text"]}
    updated = checked(client.post(base + "/chapters/edit-slice", json=payload), 201)
    assert updated["text"] == raw[:40000] + "😀修订标记😀" + raw[60000:]
    assert get_version(client, done)["text"] == raw
    assert client.post(base + "/chapters/edit-slice", json=payload).status_code == 409
    assert client.post(base + "/chapters/edit-slice", json={**payload, "base_version_id": updated["id"], "end": 40002, "expected_text": "错误源片段"}).status_code == 409
    assert client.post(base + "/chapters/edit-slice", json={**payload, "base_version_id": updated["id"], "start": 0, "end": 1, "expected_text": "第"}).status_code == 422
    assert checked(client.get(base))["latest_version_id"] == updated["id"]


@pytest.mark.parametrize("filename,fields", [("a.md", {}), ("a.docx", {}), ("a.txt", {"options": "invalid-json"}), ("a.epub", {"options": "[]"}), ("a.txt", {"mode": "replace"})])
def test_ingestion_http_rejects_unsupported_sources_or_incomplete_settings_without_writes(client, filename, fields):
    response = client.post("/api/studio/ingestions", data=fields, files={"file": (filename, b"synthetic", "application/octet-stream")})
    assert response.status_code == 422
    assert checked(client.get("/api/studio/ingestions"))["items"] == []
    assert checked(client.get("/api/studio/documents"))["items"] == []


def test_work_delete_is_blocked_while_ingestion_has_no_document_yet(client, monkeypatch):
    """Synthetic conversion gate tests the work/document commit race, not OCR."""
    work = checked(client.post("/api/records/works", json={"values": {"作品ID": "W-INGEST", "作品": "合成关联作品", "类别": "小说"}}), 201)
    entered, release = threading.Event(), threading.Event()
    def convert(*args):
        entered.set()
        assert release.wait(5)
        return "# 第一章 初见\nPDF 合成正文。"
    monkeypatch.setattr(client.app.state.runtime.studio.conversions, "_pdf_markdown", convert)
    try:
        job = ingest(client, b"%PDF-synthetic", filename="gated.pdf", work_id=work["id"])
        assert entered.wait(3)
        assert checked(client.get("/api/studio/documents"))["items"] == []
        response = client.delete(f"/api/records/works/{work['id']}?expected_revision={work['revision']}&cascade=true")
        assert response.status_code == 409 and "正在处理" in response.json()["detail"]
        assert checked(client.get(f"/api/records/works/{work['id']}"))["id"] == work["id"]
    finally:
        release.set()
    done = finish(client, job)
    assert checked(client.get(f"/api/studio/documents/{done['document_id']}"))["work_id"] == work["id"]
    assert client.delete(f"/api/records/works/{work['id']}?expected_revision={work['revision']}&cascade=true").status_code == 409


def test_failed_ingestion_retry_revalidates_its_linked_work_before_restart(client):
    work = checked(client.post("/api/records/works", json={"values": {"作品ID": "W-RETRY", "作品": "合成重试作品", "类别": "小说"}}), 201)
    job = ingest(client, b"not-an-epub", filename="broken.epub", work_id=work["id"])
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        failed = checked(client.get(f"/api/studio/ingestions/{job['id']}"))
        if failed["status"] == "failed":
            break
        time.sleep(0.01)
    assert failed["status"] == "failed", failed
    assert checked(client.get("/api/studio/documents"))["items"] == []
    assert client.delete(f"/api/records/works/{work['id']}?expected_revision={work['revision']}").status_code == 200
    retry = client.post(f"/api/studio/ingestions/{job['id']}/retry")
    assert retry.status_code == 404
    unchanged = checked(client.get(f"/api/studio/ingestions/{job['id']}"))
    assert unchanged["status"] == "failed" and unchanged["attempt"] == failed["attempt"]
    assert checked(client.get("/api/studio/documents"))["items"] == []
