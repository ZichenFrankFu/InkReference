"""Portable backups exercise real databases, files and safe independent restore."""

from __future__ import annotations

import csv
import hashlib
import io
import json
import sqlite3
import zipfile
from pathlib import Path

import pytest

from inkreference.database import DataError, DataStore
from inkreference.documents import DocumentStore
from inkreference.projects import ProjectStore
from inkreference.skills import BUILTIN_SKILL, SkillStore
from inkreference.workflow_definitions import DEFAULT_WORKFLOW, WorkflowStore


def read_archive(raw):
    with zipfile.ZipFile(io.BytesIO(raw)) as archive:
        return {name: archive.read(name) for name in archive.namelist()}


def write_archive(files):
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, raw in files.items():
            archive.writestr(name, raw)
    return stream.getvalue()


def add_manifest_entry(files, name, raw):
    manifest = json.loads(files["manifest.json"])
    manifest["files"].append({"path": name, "bytes": len(raw), "sha256": hashlib.sha256(raw).hexdigest()})
    files[name] = raw
    files["manifest.json"] = json.dumps(manifest).encode()


@pytest.fixture
def project(tmp_path):
    data = tmp_path / "active"
    materials = DataStore(data / "inkreference.sqlite3")
    work = materials.create_record("works", {"作品ID": "W-SOURCE", "作品": "合成作品", "两句话概括": "第一行\n第二行,带逗号"})
    docs = DocumentStore(data)
    raw = "第一章 原件\r\n甲&amp;乙，全文尾标。".encode("utf-8")
    document = docs.upload("原件.txt", raw, work_id=work["id"])
    edited = docs.create_version(document["id"], document["latest_version_id"], {}, manual_text="第一章 人工版\n人工修改与尾标。")
    skills = SkillStore(data)
    skill1 = skills.save({**BUILTIN_SKILL, "id": "backup-skill", "version": "1.0.0", "name": "合成技能"})
    skill2 = skills.save({**skill1, "version": "2.0.0", "prompt": "新的提取规则，保留证据。"})
    workflows = WorkflowStore(data)
    workflow1 = workflows.save({**DEFAULT_WORKFLOW, "id": "backup-workflow", "skill_id": skill1["id"], "skill_version": skill1["version"]})
    workflow2 = workflows.save({**workflow1, "version": "2.0.0", "skill_version": skill2["version"], "budget_tokens": 43210})
    layout = workflows.save_layout(workflow2["id"], workflow2["version"], {
        "nodes": [{"id": "summary", "position": {"x": 125, "y": 275}}], "edges": []}, expected_revision=0)
    with sqlite3.connect(data / "checkpoints.sqlite3") as connection:
        connection.execute("CREATE TABLE saved_checkpoints (thread_id TEXT PRIMARY KEY,state BLOB)")
        connection.execute("INSERT INTO saved_checkpoints VALUES (?,?)", ("thread-synthetic", b"\x00\xffcheckpoint"))
    imported = data / "imports" / "synthetic-batch" / "source.csv"
    imported.parent.mkdir(parents=True)
    imported.write_bytes("作品ID,作品\nW-SOURCE,合成作品\n".encode("utf-8-sig"))
    (data / ".env").write_text("MODEL_API_KEY=synthetic-do-not-back-up", encoding="utf-8")
    return {"data": data, "store": materials, "docs": docs, "document": document,
            "edited": edited, "raw": raw, "skills": skills, "skill1": skill1, "skill2": skill2,
            "workflows": workflows, "workflow1": workflow1, "workflow2": workflow2, "layout": layout,
            "imported": imported, "projects": ProjectStore(data)}


def test_full_backup_restores_independent_copy_and_all_versions(project):
    snapshot = project["projects"].export()
    files = read_archive(snapshot)
    manifest = json.loads(files["manifest.json"])
    assert manifest["format"] == "InkReference-project"
    assert set(files) == {"manifest.json", "inkreference.sqlite3", "workbench.sqlite3", "skills.sqlite3",
                          "workflow_definitions.sqlite3", "checkpoints.sqlite3", "imports/synthetic-batch/source.csv"}
    assert ".env" not in files
    for entry in manifest["files"]:
        assert len(files[entry["path"]]) == entry["bytes"]
        assert hashlib.sha256(files[entry["path"]]).hexdigest() == entry["sha256"]
    result = project["projects"].restore(snapshot)
    restored_path = Path(result["data_dir"])
    assert result["restored"] and result["restart_required"]
    assert restored_path != project["data"]
    assert restored_path.parent == project["data"] / "restored_projects"
    restored = DataStore(restored_path / "inkreference.sqlite3")
    assert restored.all_records("works") == project["store"].all_records("works")
    restored_docs = DocumentStore(restored_path)
    document = project["document"]
    assert restored_docs.get_original(document["id"]) == (project["raw"], "原件.txt")
    assert restored_docs.get_version(document["id"]) == project["edited"]
    assert len(restored_docs.get_document(document["id"])["versions"]) == 2
    for key in ("skill1", "skill2"):
        item = project[key]
        assert SkillStore(restored_path).get(item["id"], item["version"]) == item
    for key in ("workflow1", "workflow2"):
        item = project[key]
        assert WorkflowStore(restored_path).get(item["id"], item["version"]) == item
    assert WorkflowStore(restored_path).get_layout(project["workflow2"]["id"], "2.0.0") == project["layout"]
    assert (restored_path / "imports/synthetic-batch/source.csv").read_bytes() == project["imported"].read_bytes()
    with sqlite3.connect(restored_path / "checkpoints.sqlite3") as connection:
        assert connection.execute("SELECT * FROM saved_checkpoints").fetchone() == ("thread-synthetic", b"\x00\xffcheckpoint")
    copied_work = restored.get_record("works", "W-SOURCE")
    restored.update_record("works", "W-SOURCE", {"作品": "仅恢复副本发生变化"}, copied_work["revision"])
    assert project["store"].get_record("works", "W-SOURCE")["values"]["作品"] == "合成作品"
    assert Path(project["projects"].restore(snapshot)["data_dir"]) != restored_path


def test_backup_includes_committed_wal_rows_without_sidecars(project):
    # Keep a WAL connection open so export must use SQLite's backup API.
    with sqlite3.connect(project["data"] / "inkreference.sqlite3") as connection:
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("CREATE TABLE wal_evidence (value TEXT)")
        connection.execute("INSERT INTO wal_evidence VALUES ('committed-in-wal')")
        connection.commit()
        snapshot = project["projects"].export()
        assert not any(name.endswith(("-wal", "-shm")) for name in read_archive(snapshot))
        result = project["projects"].restore(snapshot)
        with sqlite3.connect(Path(result["data_dir"]) / "inkreference.sqlite3") as restored:
            assert restored.execute("SELECT value FROM wal_evidence").fetchone()[0] == "committed-in-wal"


def test_hash_corruption_rolls_back_partial_restore(project):
    files = read_archive(project["projects"].export())
    files["workbench.sqlite3"] += b"corrupt-hash"
    with pytest.raises(DataError) as caught:
        project["projects"].restore(write_archive(files))
    assert caught.value.status_code == 422
    assert list((project["data"] / "restored_projects").iterdir()) == []
    assert project["docs"].get_original(project["document"]["id"])[0] == project["raw"]


@pytest.mark.parametrize("name", ["../outside.txt", "/tmp/outside.txt", "imports/a/../../outside.txt",
                                  "imports/a/..\\outside.txt", "C:/outside.txt",
                                  "imports/a/./outside.txt", "imports//a/outside.txt"])
def test_restore_rejects_unsafe_and_noncanonical_paths(project, name):
    files = read_archive(project["projects"].export())
    add_manifest_entry(files, name, b"synthetic")
    with pytest.raises(DataError) as caught:
        project["projects"].restore(write_archive(files))
    assert caught.value.status_code == 422
    assert project["store"].stats()["works"] == 1
    if (project["data"] / "restored_projects").is_dir():
        assert list((project["data"] / "restored_projects").iterdir()) == []


@pytest.mark.parametrize("manifest", [None, [], 1, {"format": "wrong"},
                                       {"format": "InkReference-project", "version": 1, "files": "invalid"}])
def test_malformed_manifests_fail_as_reviewable_data_error(project, manifest):
    files = read_archive(project["projects"].export())
    files["manifest.json"] = json.dumps(manifest).encode()
    with pytest.raises(DataError) as caught:
        project["projects"].restore(write_archive(files))
    assert caught.value.status_code == 422


def test_database_corruption_with_valid_hash_still_rejects(project):
    files = read_archive(project["projects"].export())
    raw = b"this is not a sqlite database"
    files["inkreference.sqlite3"] = raw
    manifest = json.loads(files["manifest.json"])
    for entry in manifest["files"]:
        if entry["path"] == "inkreference.sqlite3":
            entry.update(bytes=len(raw), sha256=hashlib.sha256(raw).hexdigest())
    files["manifest.json"] = json.dumps(manifest).encode()
    with pytest.raises(DataError) as caught:
        project["projects"].restore(write_archive(files))
    assert caught.value.status_code == 422
    assert list((project["data"] / "restored_projects").iterdir()) == []


def test_business_table_csv_contains_versions_layouts_and_hex_original(project):
    files = read_archive(project["projects"].export_tables())
    expected = {"inkreference/works.csv", "inkreference/characters.csv", "inkreference/cliches.csv",
                "inkreference/work_cliches.csv", "workbench/doc_documents.csv", "workbench/doc_versions.csv",
                "skills/definitions.csv", "workflow_definitions/definitions.csv", "workflow_definitions/layouts.csv"}
    assert expected <= set(files)
    manifest = json.loads(files["manifest.json"])
    assert manifest == {"format": "InkReference-business-tables", "blob_encoding": "hex", "restore_format": False}

    def rows(name):
        assert files[name].startswith(b"\xef\xbb\xbf")
        return list(csv.DictReader(io.StringIO(files[name].decode("utf-8-sig"), newline="")))

    assert rows("inkreference/works.csv")[0]["id"] == "W-SOURCE"
    assert json.loads(rows("inkreference/works.csv")[0]["values_json"])["两句话概括"] == "第一行\n第二行,带逗号"
    assert bytes.fromhex(rows("workbench/doc_documents.csv")[0]["original"]) == project["raw"]
    assert len(rows("workbench/doc_versions.csv")) == 2
    assert {row["version"] for row in rows("skills/definitions.csv")} == {"1.0.0", "2.0.0"}
    layout = json.loads(rows("workflow_definitions/layouts.csv")[0]["layout_json"])
    assert layout == project["layout"]["layout"]
