"""Invented growth fixtures: plans, bounded reads and immutable backup data."""
import base64
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
import json
import sqlite3

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from inkreference.database import DataError, DataStore
from inkreference.documents import DocumentStore
from inkreference.library_exchange import export_library, parse_library_files, preview_library_import, commit_library_import
from inkreference.models import ModelService
from inkreference.projects import ProjectStore
from inkreference.storage_health import StorageHealth, create_storage_router
from inkreference.storage_utils import INDEX_SPECS, ensure_growth_indexes, json_field_expression


def _work(store, identifier="W1", heat="8"):
    return store.create_record("works", {"作品ID": identifier, "作品": "合成" + identifier, "作者": "合成作者", "讨论热度": heat, "商业规模": heat, "类别": "中文小说"})


def _call(index, *, run="R1", status="success", source="actual", amount=None):
    return {"id": f"call-{index:04}", "run_id": run, "profile_id": "synthetic", "started_at": f"2026-01-{index % 28 + 1:02}T00:00:00Z", "status": status,
            "usage": {"input_tokens": index, "output_tokens": index + 1 if source != "unknown" else None, "input_source": source, "output_source": source},
            "cost": {"amount": amount, "currency": "USD" if amount is not None else None}}


def _save_calls(service, calls):
    with service._connect() as db:
        db.executemany("INSERT INTO usage_calls VALUES(?,?,?,?,?)", [(r["id"], r["run_id"], r["profile_id"], r["started_at"], json.dumps(r)) for r in calls])
        ensure_growth_indexes(db, "workbench")


def test_growth_plan_is_idempotent_and_does_not_scan_business_rows(tmp_path):
    store = DataStore(tmp_path / "inkreference.sqlite3")
    _work(store)
    with store.connect() as db:
        before = [tuple(row) for row in db.execute("SELECT * FROM works")]
        trace = []
        db.set_trace_callback(trace.append)
        assert ensure_growth_indexes(db, "catalog") == []
        db.set_trace_callback(None)
        assert before == [tuple(row) for row in db.execute("SELECT * FROM works")]
        assert not any("SELECT" in sql.upper() and "FROM WORKS" in sql.upper() for sql in trace)
    with closing(sqlite3.connect(tmp_path / "empty.db")) as db:
        assert ensure_growth_indexes(db, "workbench") == []
        with pytest.raises(ValueError): ensure_growth_indexes(db, "unknown")


@pytest.mark.parametrize("table,field,suffix", [("works", "类别", "category"), ("characters", "角色定位", "role"), ("characters", "性格基调", "temperament"), ("cliches", "人设塑造", "trait"), ("cliches", "剧情机制", "mechanism")])
def test_allowlisted_json_filter_uses_expression_index(tmp_path, table, field, suffix):
    store = DataStore(tmp_path / "catalog.db")
    with store.connect() as db:
        expression = json_field_expression(field)
        plan = " ".join(row[3] for row in db.execute(f"EXPLAIN QUERY PLAN SELECT id FROM {table} WHERE {expression}=? ORDER BY sort_order,id LIMIT 50", ("合成",)))
    assert f"ix_growth_{table}_{suffix}_v1" in plan
    assert "USE TEMP B-TREE" not in plan


def test_filter_count_pagination_and_literal_values_remain_correct(tmp_path):
    store = DataStore(tmp_path / "catalog.db")
    for i in range(8):
        row = _work(store, "W" + str(i))
        store.update_record("works", row["id"], {"类别": "电影" if i % 2 else "中文小说", "用户是否完整体验过": "是" if i == 0 else ""}, row["revision"])
    result = store.list_records("works", filters={"category": "中文小说"}, offset=1, limit=2, include_work_facets=False)
    assert result["total"] == 4
    assert [row["id"] for row in result["items"]] == ["W2", "W4"]
    assert result["facets"]["works"] == [] and result["facets"]["works_paged"]
    assert len(store.list_records("works")["facets"]["works"]) == 8
    assert store.list_records("works", filters={"category": "' OR 1=1 --"})["total"] == 0
    assert store.list_records("works", filters={"seen": "unknown"})["total"] == 7


def test_score_cache_invalidates_for_other_connections_rollback_and_relations(tmp_path):
    store = DataStore(tmp_path / "catalog.db")
    _work(store); _work(store, "W2", "2")
    store.create_record("cliches", {"clichéID": "C1", "cliché内容": "合成桥段", "关联作品": '[{"作品ID":"W1"}]'})
    assert store.get_record("cliches", "C1")["computed"]["cliche_score"] == 80
    with store.connect() as db:
        value = json.loads(db.execute("SELECT values_json FROM works WHERE id='W2'").fetchone()[0])
        value.update({"讨论热度": "8", "商业规模": "8"})
        db.execute("UPDATE works SET values_json=? WHERE id='W2'", (json.dumps(value),))
    assert store.get_record("cliches", "C1")["computed"]["cliche_score"] == 50
    with pytest.raises(RuntimeError):
        with store.transaction() as db:
            db.execute("DELETE FROM works WHERE id='W2'")
            assert store._render(db, "cliches", store._row(db, "cliches", "C1"))["computed"]["cliche_score"] == 100
            raise RuntimeError()
    assert store.get_record("cliches", "C1")["computed"]["cliche_score"] == 50
    with store.connect() as db:
        db.execute("DELETE FROM work_cliches WHERE cliche_id='C1'")
    assert store.get_record("cliches", "C1")["computed"]["matched_work_count"] == 0


def test_parallel_reads_and_writes_do_not_keep_stale_score(tmp_path):
    store = DataStore(tmp_path / "catalog.db")
    _work(store); _work(store, "W2", "2")
    store.create_record("cliches", {"clichéID": "C1", "cliché内容": "合成桥段", "关联作品": '[{"作品ID":"W1"}]'})
    def read(_): return store.get_record("cliches", "C1")["computed"]["cliche_score"]
    with ThreadPoolExecutor(max_workers=4) as pool: assert set(pool.map(read, range(16))) == {80}
    row = store.get_record("works", "W2")
    store.update_record("works", "W2", {"讨论热度": "8", "商业规模": "8"}, row["revision"])
    with ThreadPoolExecutor(max_workers=4) as pool: assert set(pool.map(read, range(16))) == {50}


def test_rollback_epoch_reuse_does_not_publish_speculative_totals(tmp_path):
    store = DataStore(tmp_path / "catalog.db")
    _work(store); _work(store, "W2", "2")
    store.create_record("cliches", {"clichéID": "C1", "cliché内容": "合成桥段", "关联作品": '[{"作品ID":"W1"}]'})
    assert store.get_record("cliches", "C1")["computed"]["cliche_score"] == 80
    with pytest.raises(RuntimeError):
        with store.transaction() as db:
            db.execute("DELETE FROM works WHERE id='W2'")
            assert store._render(db, "cliches", store._row(db, "cliches", "C1"))["computed"]["cliche_score"] == 100
            raise RuntimeError()
    # No intervening read repairs a bad cache before this different transaction
    # reaches the same epoch number as the rolled-back delete.
    record = store.get_record("works", "W2")
    store.update_record("works", "W2", {"讨论热度": "4", "商业规模": "4"}, record["revision"])
    assert store.get_record("cliches", "C1")["computed"]["cliche_score"] == round(800 / 12, 4)


def test_usage_keyset_page_no_duplicates_on_same_timestamps_and_new_inserts(tmp_path):
    service = ModelService(tmp_path)
    calls = [_call(i) for i in range(125)]
    _save_calls(service, calls)
    first = service.list_usage_page(limit=50)
    assert len(first["items"]) == 50 and first["next_cursor"]
    _save_calls(service, [{**_call(999), "started_at": "2027-01-01T00:00:00Z"}])
    second = service.list_usage_page(limit=50, cursor=first["next_cursor"])
    third = service.list_usage_page(limit=50, cursor=second["next_cursor"])
    ids = [r["id"] for page in (first, second, third) for r in page["items"]]
    assert len(ids) == len(set(ids)) == 125 and not third["next_cursor"]
    assert set(ids) == {r["id"] for r in calls}
    assert len(service.list_usage()) == 126


@pytest.mark.parametrize("limit,cursor", [(0, None), (201, None), (True, None), (50, "not a cursor"), (50, base64.urlsafe_b64encode(b'["2026","id","other-run"]').decode())])
def test_usage_page_rejects_invalid_cursors_and_limits(tmp_path, limit, cursor):
    with pytest.raises(DataError): ModelService(tmp_path).list_usage_page(limit=limit, cursor=cursor)


def test_usage_sql_summary_exact_money_and_unknown_direction(tmp_path, monkeypatch):
    service = ModelService(tmp_path)
    _save_calls(service, [_call(1, amount=0.1), _call(2, source="estimated", amount=0.2), _call(3, status="pending", source="unknown"), _call(4, run="R2", status="error")])
    monkeypatch.setattr(service, "list_usage", lambda *args: pytest.fail("summary must not hydrate the ledger"))
    summary = service.usage_summary("R1")
    assert summary == {"calls": 3, "successful_calls": 2, "failed_calls": 0, "pending_calls": 1, "actual_input_tokens": 1, "actual_output_tokens": 2,
                       "estimated_input_tokens": 2, "estimated_output_tokens": 3, "unknown_input_calls": 1, "unknown_output_calls": 1,
                       "unpriced_calls": 1, "costs": [{"currency": "USD", "amount": 0.3, "source": "estimated"}]}
    assert service.usage_summary("missing")["calls"] == 0


def test_real_app_usage_page_preserves_legacy_api_and_filter_guard(tmp_path):
    from inkreference.api import create_app
    app = create_app(data_dir=tmp_path, static_dir=tmp_path / "missing-dist")
    _save_calls(app.state.runtime.studio.models, [_call(i, run="R1" if i < 100 else "R2") for i in range(125)])
    with TestClient(app, base_url="http://127.0.0.1") as client:
        first = client.get("/api/studio/usage?limit=50").json()
        assert len(first["items"]) == 50 and first["summary"]["calls"] == 125
        second = client.get("/api/studio/usage", params={"limit": 50, "cursor": first["next_cursor"]}).json()
        assert len(second["items"]) == 50
        assert not {r["id"] for r in first["items"]} & {r["id"] for r in second["items"]}
        assert client.get("/api/studio/usage").json()["items"] == app.state.runtime.studio.models.list_usage()
        assert client.get("/api/studio/usage", params={"limit": 50, "run_id": "R2", "cursor": first["next_cursor"]}).status_code == 422
        filtered = client.get("/api/studio/usage?limit=50&run_id=R2").json()
        assert len(filtered["items"]) == filtered["summary"]["calls"] == 25 and filtered["next_cursor"] is None
        assert client.get("/api/studio/usage?limit=201").status_code == 422


def test_health_metadata_only_and_non_destructive_optimize(tmp_path):
    store = DataStore(tmp_path / "inkreference.sqlite3")
    _work(store)
    documents = DocumentStore(tmp_path)
    doc = documents.upload("合成.txt", "第1章 合成\n合成正文。".encode())
    health = StorageHealth(tmp_path)
    with store.connect() as db: before = [tuple(r) for r in db.execute("SELECT * FROM works")]
    snapshot = health.inspect()
    assert snapshot["index_plan_version"] == 1 and len(snapshot["databases"]) == 2
    assert "合成正文" not in json.dumps(snapshot, ensure_ascii=False)
    result = health.optimize()
    assert not result["destructive"] and all(row["status"] == "ready" for row in result["items"])
    with store.connect() as db: assert [tuple(r) for r in db.execute("SELECT * FROM works")] == before
    assert documents.get_version(doc["id"])["text"] == "第1章 合成\n合成正文。"
    with pytest.raises(DataError): health.optimize("../../elsewhere")


def test_health_does_not_create_missing_database_and_api_guard(tmp_path):
    assert StorageHealth(tmp_path).inspect()["databases"] == []
    assert StorageHealth(tmp_path).optimize()["items"] == []
    assert not list(tmp_path.iterdir())
    app = FastAPI()
    guarded = []
    app.include_router(create_storage_router(lambda: StorageHealth(tmp_path), lambda: guarded.append(True)))
    with TestClient(app) as client:
        assert client.get("/api/studio/storage").status_code == 200
        assert client.post("/api/studio/storage/optimize", json={}).status_code == 200
        assert client.post("/api/studio/storage/optimize", json={"database": "unknown"}).status_code == 422
    assert guarded == [True]


def test_health_survives_concurrent_wal_creation_and_close(tmp_path):
    service = ModelService(tmp_path)
    health = StorageHealth(tmp_path)
    def append(index):
        _save_calls(service, [_call(index)])
    with ThreadPoolExecutor(max_workers=4) as pool:
        futures = [pool.submit(append if index % 2 else lambda _: health.inspect(), index) for index in range(40)]
        for future in futures: future.result()
    assert service.usage_summary()["calls"] == 20
    assert health.inspect()["databases"][0]["status"] == "ready"


def test_optimize_defers_when_another_writer_owns_database(tmp_path):
    store = DataStore(tmp_path / "inkreference.sqlite3")
    with store.connect() as connection:
        connection.execute("DROP INDEX ix_growth_works_category_v1")
    with store.transaction():
        result = StorageHealth(tmp_path).optimize("inkreference.sqlite3")
        assert result["items"][0]["status"] == "deferred"
    assert StorageHealth(tmp_path).optimize("inkreference.sqlite3")["items"][0]["status"] == "ready"


def test_actual_maintenance_api_classifies_busy_as_retryable(tmp_path):
    from inkreference.api import create_app
    app = create_app(data_dir=tmp_path, static_dir=tmp_path / "missing-dist")
    with TestClient(app, base_url="http://127.0.0.1") as client:
        store = app.state.runtime.store
        with store.connect() as connection:
            connection.execute("DROP INDEX ix_growth_works_category_v1")
        with store.transaction():
            response = client.post("/api/studio/storage/optimize", json={"database": "inkreference.sqlite3"})
        assert response.status_code == 200
        item = response.json()["items"][0]
        assert item["status"] == "deferred" and "diagnostic_id" not in item


def test_actual_maintenance_api_classifies_corrupt_database_with_diagnostic(tmp_path):
    from inkreference.api import create_app
    marker = b"synthetic-not-a-database-private-marker"
    corrupt = tmp_path / "skills.sqlite3"
    corrupt.write_bytes(marker)
    app = create_app(data_dir=tmp_path, static_dir=tmp_path / "missing-dist")
    with TestClient(app, base_url="http://127.0.0.1") as client:
        response = client.post("/api/studio/storage/optimize", json={"database": "skills.sqlite3"})
        assert response.status_code == 200
        item = response.json()["items"][0]
        assert item["status"] == "error" and item["diagnostic_id"].startswith("err-")
        inspected = client.get("/api/studio/storage").json()
        assert next(row for row in inspected["databases"] if row["name"] == "skills.sqlite3")["status"] == "unavailable"
    assert corrupt.read_bytes() == marker
    records = [json.loads(line) for path in (tmp_path / "logs").glob("*.jsonl") for line in path.read_text().splitlines()]
    diagnostic = next(row for row in records if row.get("diagnostic_id") == item["diagnostic_id"])
    assert diagnostic["component"] == "storage" and diagnostic["operation"] == "optimize" and diagnostic["status"] == "sqlite_notadb"
    assert marker.decode() not in json.dumps(records)


def test_indexed_project_backup_and_portable_library_preserve_rows(tmp_path):
    store = DataStore(tmp_path / "inkreference.sqlite3")
    _work(store)
    documents = DocumentStore(tmp_path)
    doc = documents.upload("合成.txt", "第1章 合成\n合成正文。".encode())
    before = store.get_record("works", "W1")["values"]
    StorageHealth(tmp_path).optimize()
    restored = ProjectStore(tmp_path).restore(ProjectStore(tmp_path).export())
    target = DataStore(restored["data_dir"] + "/inkreference.sqlite3")
    assert target.get_record("works", "W1")["values"] == before
    assert DocumentStore(restored["data_dir"]).get_version(doc["id"])["text"] == documents.get_version(doc["id"])["text"]
    portable = DataStore(tmp_path / "portable.db")
    batch = parse_library_files([("library.db", export_library(store, "db"))])
    assert not preview_library_import(portable, batch)["errors"]
    commit_library_import(portable, batch)
    assert portable.get_record("works", "W1")["values"] == before


def test_legacy_csv_preview_copy_registers_score_functions_and_preserves_original(tmp_path):
    from pathlib import Path
    from inkreference.csvio import parse_files, preview_import, commit_import, export_zip
    import io
    import zipfile
    store = DataStore(tmp_path / "catalog.db")
    fixtures = Path(__file__).parent / "fixtures"
    first = parse_files([(table + ".csv", (fixtures / (table + ".csv")).read_bytes()) for table in ("works", "characters", "cliches")])
    assert not preview_import(store, first)["errors"]
    commit_import(store, first)
    before = store.catalog_snapshot()
    with zipfile.ZipFile(io.BytesIO(export_zip(store))) as archive:
        repeated = parse_files([(name, archive.read(name)) for name in archive.namelist()])
    preview = preview_import(store, repeated)
    assert not preview["errors"]
    assert store.catalog_snapshot() == before
    assert commit_import(store, repeated)["skipped"] == 8
