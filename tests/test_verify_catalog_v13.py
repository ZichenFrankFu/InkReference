"""Synthetic verifier checks distinguish stored input from derived score views."""
import csv
import importlib.util
import json
from pathlib import Path

import pytest

from inkreference.catalog_schema_v11 import COLUMNS, project_values


def verifier():
    path = Path(__file__).resolve().parents[1] / "scripts" / "verify_catalog.py"
    spec = importlib.util.spec_from_file_location("verify_catalog_v13", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def fixture_csvs(tmp_path, score=""):
    data = {"works": [project_values("works", {"作品ID": "WZ", "作品": "合成首本", "作者": "合成作者", "讨论热度": "8", "商业规模": "8", "关联cliché": '[{"clichéID":"C1"}]'}),
                      project_values("works", {"作品ID": "WA", "作品": "合成次本", "作者": "另一合成作者", "讨论热度": "2", "商业规模": "2"})],
            "characters": [], "cliches": [project_values("cliches", {"clichéID": "C1", "cliché内容": "护送同伴", "cliché评分": score, "关联作品": '[{"作品ID":"WZ"}]'})]}
    paths = []
    for table, rows in data.items():
        path = tmp_path / (table + ".csv")
        with path.open("w", encoding="utf-8-sig", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=COLUMNS[table])
            writer.writeheader(); writer.writerows(rows)
        paths.append(path)
    return paths


@pytest.mark.parametrize("score", ["", "12.3456"])
def test_verifier_preserves_raw_score_and_reports_derived_recomputation(tmp_path, score):
    paths = fixture_csvs(tmp_path, score)
    before = [path.read_bytes() for path in paths]
    result = verifier().verify(paths)
    assert result["status"] == "passed"
    assert result["raw_import_fields_preserved"] and result["raw_import_order_preserved"]
    assert result["canonical_export_fields_and_order_preserved"]
    assert result["derived_fields_recomputed"] == ["cliché评分"]
    assert set(result["formats"]) == {"db", "csv", "xlsx"}
    assert result["repeated_import_idempotent"] and result["input_bytes_unchanged"]
    assert "all_fields_and_order_preserved" not in json.dumps(result)
    assert [path.read_bytes() for path in paths] == before


def test_verifier_rejects_non_derived_export_field_change(tmp_path, monkeypatch):
    from inkreference.library_exchange import _collect, _export_db, _digest
    module = verifier()
    original = module.export_library
    def tamper(store, format):
        if format != "db": return original(store, format)
        rows, manifest = _collect(store)
        rows["works"][0]["作者"] = "替换的合成作者"
        manifest["tables"]["works"]["sha256"] = _digest(rows["works"])
        return _export_db(rows, manifest)
    monkeypatch.setattr(module, "export_library", tamper)
    with pytest.raises(AssertionError, match="db changed canonical fields or record order"):
        module.verify(fixture_csvs(tmp_path))


def test_verifier_rejects_non_derived_persisted_import_change(tmp_path, monkeypatch):
    module = verifier()
    original = module.commit_library_import
    def tamper(store, batch, policy):
        result = original(store, batch, policy)
        with store.transaction() as connection:
            value = json.loads(connection.execute("SELECT values_json FROM works ORDER BY sort_order,id LIMIT 1").fetchone()[0])
            value["作者"] = "替换的合成作者"
            connection.execute("UPDATE works SET values_json=? WHERE id=?", (json.dumps(value), value["作品ID"]))
        return result
    monkeypatch.setattr(module, "commit_library_import", tamper)
    with pytest.raises(AssertionError, match="Raw import changed fields or record order"):
        module.verify(fixture_csvs(tmp_path))
