"""HTTP integration checks with synthetic data and private temporary app dirs."""

from __future__ import annotations

import csv
import io
import json
import subprocess
import sys
import zipfile

import pytest
from fastapi.testclient import TestClient

import inkreference.api as api
from inkreference.api import create_app
from inkreference.database import TABLES


def csv_bytes(table, rows):
    target = io.StringIO(newline="")
    writer = csv.DictWriter(target, fieldnames=TABLES[table]["columns"])
    writer.writeheader()
    writer.writerows({column: row.get(column, "") for column in TABLES[table]["columns"]} for row in rows)
    return target.getvalue().encode("utf-8-sig")


def work_values(identifier="W001"):
    return {"作品ID": identifier, "作品": "测试作品", "类别": "小说", "版本与原作关系": "小说；原创", "两句话概括": "第一行\n第二行", "热度": "baseline"}


def multipart_fixture():
    work = {**work_values(), "典型桥段（剧透）": "C001：一次揭露\n附加细节"}
    character = {"人物ID": "P001", "作品ID": "W001", "作品": "测试作品", "角色": "测试人物", "版本": "小说", "后续能力": "不确定"}
    cliche = {"cliché内容": "C001｜身份揭露", "cliché程度": "0.3", "出现作品": "测试作品〔小说；原创〕"}
    return [("files", (filename, csv_bytes(table, [row]), "text/csv")) for filename, table, row in [
        ("works.csv", "works", work),
        ("characters.csv", "characters", character),
        ("cliches.csv", "cliches", cliche),
    ]]


@pytest.fixture
def client(tmp_path):
    application = create_app(data_dir=tmp_path / "data", static_dir=tmp_path / "absent-dist")
    with TestClient(application) as session:
        yield session


def preview(client, files=None):
    response = client.post("/api/import/preview", files=files or multipart_fixture())
    assert response.status_code == 200, response.text
    return response.json()


def import_fixture(client):
    result = preview(client)
    assert result["errors"] == [], result
    response = client.post("/api/import/commit", json={"batch_id": result["batch_id"], "policy": "skip"})
    assert response.status_code == 200, response.text
    return response.json()


def test_app_creation_is_lazy_and_health_metadata(client, tmp_path):
    untouched = tmp_path / "never-initialized"
    create_app(data_dir=untouched)
    assert not untouched.exists()
    assert client.get("/api/health").json()["status"] == "ok"
    assert {item["key"] for item in client.get("/api/meta").json()["tables"]} == set(TABLES)
    assert client.get("/api/stats").json() == {"works": 0, "characters": 0, "cliches": 0, "work_cliches": 0}
    assert "npm run build" in client.get("/").text


def test_module_import_does_not_create_default_directory(tmp_path):
    import os
    environment = {**os.environ, "INKREFERENCE_DATA_DIR": str(tmp_path / "no-side-effects")}
    # The project's pytest pythonpath is not inherited by a child interpreter.
    environment["PYTHONPATH"] = str(__import__("pathlib").Path(api.__file__).parents[1])
    subprocess.run([sys.executable, "-c", "import inkreference.api"], env=environment, check=True)
    assert not (tmp_path / "no-side-effects").exists()


def test_preview_commit_relations_export_and_reimport(client):
    batch = preview(client)
    assert batch["errors"] == []
    assert batch["stats"] == {"works": 1, "characters": 1, "cliches": 1, "work_cliches": 1}
    assert client.get("/api/stats").json()["works"] == 0
    response = client.post("/api/import/commit", json={"batch_id": batch["batch_id"], "policy": "skip"})
    assert response.status_code == 200, response.text
    assert response.json()["imported"] == 3
    assert client.post("/api/import/commit", json={"batch_id": batch["batch_id"], "policy": "skip"}).status_code == 404
    assert client.get("/api/records/works", params={"q": "第二行"}).json()["total"] == 1
    related = client.get("/api/records/works/W001/relations").json()
    assert related["characters"][0]["id"] == "P001"
    assert related["cliches"][0]["id"] == "C001"
    assert client.get("/api/records/characters", params={"work_id": "W001"}).json()["total"] == 1

    exported = client.get("/api/export")
    assert exported.status_code == 200
    assert exported.headers["content-type"] == "application/zip"
    assert "attachment" in exported.headers["content-disposition"]
    with zipfile.ZipFile(io.BytesIO(exported.content)) as archive:
        assert set(archive.namelist()) == {"works_library_v8.csv", "character_database_v8.csv", "cliche_database_v8.csv"}
        reimport = [("files", (name, archive.read(name), "text/csv")) for name in archive.namelist()]
    repeated = preview(client, reimport)
    assert repeated["errors"] == [], repeated
    committed = client.post("/api/import/commit", json={"batch_id": repeated["batch_id"], "policy": "skip"}).json()
    assert committed["skipped"] == 3
    assert committed["stats"] == response.json()["stats"]

    csv_response = client.get("/api/export/works")
    assert csv_response.content.startswith(b"\xef\xbb\xbf")
    assert 'filename="works_library_v8.csv"' in csv_response.headers["content-disposition"]
    row = next(csv.DictReader(io.StringIO(csv_response.content.decode("utf-8-sig"), newline="")))
    assert row["两句话概括"] == "第一行\n第二行"
    assert row["热度"] == "baseline"
    with zipfile.ZipFile(io.BytesIO(client.get("/api/export?mode=full").content)) as archive:
        assert "manifest.json" in archive.namelist()


def test_crud_revision_guard_and_related_delete(client):
    response = client.post("/api/records/works", json={"values": work_values()})
    assert response.status_code == 201, response.text
    record = response.json()
    assert record["revision"] == 1
    duplicate = client.post("/api/records/works", json={"values": work_values()})
    assert duplicate.status_code == 409
    patched = client.patch("/api/records/works/W001", json={"values": {"作品": "修改作品"}, "expected_revision": 1})
    assert patched.status_code == 200, patched.text
    assert patched.json()["revision"] == 2
    assert client.patch("/api/records/works/W001", json={"values": {"作品": "覆盖"}, "expected_revision": 1}).status_code == 409
    assert client.delete("/api/records/works/W001?expected_revision=1").status_code == 409
    assert client.get("/api/records/works/W001").json()["values"]["作品"] == "修改作品"

    character = {"人物ID": "P001", "作品ID": "W001", "角色": "测试人物"}
    assert client.post("/api/records/characters", json={"values": character}).status_code == 201
    assert client.delete("/api/records/works/W001?expected_revision=2").status_code == 409
    deleted = client.delete("/api/records/works/W001?expected_revision=2&cascade=true")
    assert deleted.status_code == 200, deleted.text
    assert deleted.json()["deleted"] == 2
    assert client.get("/api/records/characters/P001").status_code == 404


def test_invalid_csv_is_reviewable_and_cannot_commit(client):
    result = preview(client, [("files", ("bad.csv", b"unknown\nvalue\n", "text/csv"))])
    assert result["errors"]
    failed = client.post("/api/import/commit", json={"batch_id": result["batch_id"], "policy": "skip"})
    assert failed.status_code == 422
    assert client.get("/api/stats").json()["works"] == 0
    assert result["batch_id"] in client.app.state.runtime.pending


def test_batch_expiration_and_revalidation(client):
    import_fixture(client)
    batch = preview(client)
    work = client.get("/api/records/works/W001").json()
    assert client.patch("/api/records/works/W001", json={"values": {"作者": "后来编辑"}, "expected_revision": work["revision"]}).status_code == 200
    result = client.post("/api/import/commit", json={"batch_id": batch["batch_id"], "policy": "update"})
    assert result.status_code == 409, result.text
    assert client.get("/api/records/works/W001").json()["values"]["作者"] == "后来编辑"
    client.app.state.runtime.pending[batch["batch_id"]].expires_at = 0
    assert client.post("/api/import/commit", json={"batch_id": batch["batch_id"], "policy": "skip"}).status_code == 404


def test_upload_sources_are_private_and_basename_safe(client):
    raw = csv_bytes("works", [work_values()])
    result = preview(client, [("files", (r"C:\Users\Example\..\..\works.csv", raw, "text/csv"))])
    source_dir = client.app.state.runtime.data_dir / "imports" / result["batch_id"]
    manifest = json.loads((source_dir / "manifest.json").read_text("utf-8"))
    stored_name = manifest["files"][0]["stored_name"]
    assert stored_name == "01_works.csv"
    assert (source_dir / stored_name).read_bytes() == raw
    assert len(manifest["files"][0]["sha256"]) == 64
    assert client.get(f"/imports/{result['batch_id']}/{stored_name}").status_code == 404


def test_upload_size_and_file_type_limits(client, monkeypatch):
    assert client.post("/api/import/preview", files=[("files", ("bad.txt", b"abc", "text/plain"))]).status_code == 400
    monkeypatch.setattr(api, "MAX_FILE_BYTES", 4)
    assert client.post("/api/import/preview", files=[("files", ("big.csv", b"12345", "text/csv"))]).status_code == 413
    monkeypatch.setattr(api, "MAX_FILE_BYTES", 100)
    monkeypatch.setattr(api, "MAX_BATCH_BYTES", 4)
    assert client.post("/api/import/preview", files=[("files", ("one.csv", b"123", "text/csv")), ("files", ("two.csv", b"456", "text/csv"))]).status_code == 413
    assert client.get("/api/stats").json()["works"] == 0


@pytest.mark.parametrize("payload", [
    {"values": {"作品ID": "W001", "热度": 1}},
    {"values": {"作品ID": "W001"}, "unexpected": True},
    {"values": {"作品ID": "W001", "未知字段": "值"}},
])
def test_strict_record_validation(client, payload):
    assert client.post("/api/records/works", json=payload).status_code == 422


def test_bad_parameters_and_revision_types(client):
    for path in ["/api/records/not-a-table", "/api/records/works?offset=-1", "/api/records/works?limit=501", "/api/export?mode=other"]:
        assert client.get(path).status_code == 422
    assert client.get("/api/unknown").status_code == 404
    assert client.post("/api/import/commit", json={"batch_id": "missing", "policy": "skip"}).status_code == 404
    assert client.post("/api/import/commit", json={"batch_id": "missing", "policy": "overwrite"}).status_code == 422
    assert client.patch("/api/records/works/W001", json={"values": {}, "expected_revision": True}).status_code == 422
    assert client.patch("/api/records/works/W001", json={"values": {}, "expected_revision": 0}).status_code == 422


def test_mutations_reject_foreign_browser_origin(client):
    payload = {"values": work_values()}
    for origin in ["https://malicious.example", "null", "http://testserver.attacker.example", "http://localhost:5173/path", "http://localhost:bad"]:
        assert client.post("/api/records/works", json=payload, headers={"Origin": origin}).status_code == 403
    assert client.get("/api/stats").json()["works"] == 0
    assert client.post("/api/records/works", json=payload, headers={"Origin": "http://localhost:5173"}).status_code == 201
    assert client.patch("/api/records/works/W001", json={"values": {"作者": "同源修改"}, "expected_revision": 1}, headers={"Origin": "http://testserver"}).status_code == 200


def test_static_build_is_served_without_exposing_api_typos(tmp_path):
    dist = tmp_path / "dist"
    dist.mkdir()
    (dist / "index.html").write_text("<html>synthetic frontend</html>", encoding="utf-8")
    with TestClient(create_app(tmp_path / "data", dist)) as session:
        assert "synthetic frontend" in session.get("/").text
        assert session.get("/api/misspelled").status_code == 404
