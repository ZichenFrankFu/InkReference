"""HTTP contracts for small read models and guarded chapter body saves.

Performance is protected structurally (no full snapshot read/rescan during
navigation), without machine-dependent wall-clock assertions.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

import inkreference.documents as document_module
from inkreference.api import create_app

PREFIX = "/api/studio"
HEAVY = {"text", "source_text", "chapters", "chunks", "diff", "diagnostics", "source_diagnostics"}


@pytest.fixture
def client(tmp_path):
    with TestClient(create_app(tmp_path / "data", tmp_path / "no-dist"), base_url="http://127.0.0.1") as session:
        yield session


def seed(client, numbers=range(1, 4), *, aside=False):
    paragraph = "🌙林灯沿着江岸走向旧站台。" * 50
    source = "".join(
        f"第{number}章 夜行{number}\r\n{paragraph}\r\n\r\n第二段正文。\r\n"
        + ("求月票，感谢书友！\r\n\x00\x01\r\n" if aside and number == 1 else "")
        for number in numbers
    )
    store = client.app.state.runtime.studio.documents
    document = store.upload("合成正文.txt", source.encode("utf-8"), preserve_text=True)
    return store, document, source


def version_path(document):
    return f"{PREFIX}/documents/{document['id']}/versions/{document['latest_version_id']}"


def test_summary_directory_and_body_pages_do_not_read_or_rescan_full_snapshot(client, monkeypatch):
    store, document, source = seed(client, [*range(1, 12), *range(17, 21)])
    path = version_path(document)

    def forbidden(*args, **kwargs):
        pytest.fail("navigation must use persisted read models, not deserialize or rescan the book")

    monkeypatch.setattr(store, "get_version", forbidden)
    monkeypatch.setattr(document_module, "chapter_proposals", forbidden)
    monkeypatch.setattr(document_module, "diagnose_text", forbidden)

    summary = client.get(path + "?summary=true")
    assert summary.status_code == 200, summary.text
    assert HEAVY.isdisjoint(summary.json())
    assert len(summary.content) < 8000
    navigation = client.get(path + "?include_text=false")
    assert navigation.status_code == 200
    assert "text" not in navigation.json() and "source_text" not in navigation.json()
    assert all("text" not in chapter for chapter in navigation.json()["chapters"])

    index_response = client.get(path + "/chapters")
    assert index_response.status_code == 200
    index = index_response.json()
    placeholders = [chapter for chapter in index["chapters"] if chapter.get("placeholder")]
    assert [chapter["number"] for chapter in placeholders] == [12, 13, 14, 15, 16]
    assert [chapter["display_label"] for chapter in placeholders] == [f"第{n}章" for n in range(12, 17)]
    for chapter in (index["chapters"][0], *placeholders):
        response = client.get(path + f"/chapters/{chapter['id']}?body_only=true")
        assert response.status_code == 200, response.text
        page = response.json()
        assert page["body_only"] is True
        assert page["text"] == page["editable_text"]
        assert "第1章" not in page["text"]
        assert "\n\n" not in page["text"] and "\r\n\r\n" not in page["text"]
        assert len(page["source_offset_map"]) == len(page["text"]) + 1
        if chapter.get("placeholder"):
            assert page["text"] == "" and page["body_total"] == 0
        else:
            assert "\r\n\r\n" in page["expected_text"]
    assert source.startswith("第1章 夜行1\r\n")


def test_summary_save_uses_raw_expected_span_and_keeps_original_history(client):
    store, document, source = seed(client)
    path = version_path(document)
    index = client.get(path + "/chapters").json()
    chapter = index["chapters"][1]
    page = client.get(path + f"/chapters/{chapter['id']}?body_only=true").json()
    edited = page["text"].replace("第二段正文。", "第二段已经核对。\n\n新增一段。")
    payload = {"base_version_id": document["latest_version_id"], "chapter_id": chapter["id"],
               "start": page["editable_start"], "end": page["editable_end"],
               "text": edited, "expected_text": page["expected_text"]}
    response = client.post(f"{PREFIX}/documents/{document['id']}/chapters/edit-slice?summary=true", json=payload)
    assert response.status_code == 201, response.text
    summary = response.json()
    assert HEAVY.isdisjoint(summary)
    assert len(response.content) < 8000
    assert summary["parent_version_id"] == document["latest_version_id"]
    saved = store.get_version(document["id"], summary["id"])
    assert "第二段已经核对。\n新增一段。" in saved["text"]
    assert saved["chapters"][0]["text"] == source[:source.index("第2章")]
    assert saved["text"].endswith(source[source.index("第3章"):])
    assert store.get_version(document["id"], document["latest_version_id"])["text"] == source
    assert store.get_original(document["id"])[0] == source.encode("utf-8")
    stale = client.post(f"{PREFIX}/documents/{document['id']}/chapters/edit-slice?summary=true", json=payload)
    assert stale.status_code == 409
    assert store.get_document(document["id"])["latest_version_id"] == summary["id"]


def test_findings_paging_preserves_exact_spans_and_counts_each_chapter_once(client):
    store, document, _ = seed(client, aside=True)
    path = version_path(document)
    full = client.get(path + "/findings")
    assert full.status_code == 200
    findings = full.json()
    assert findings["total"] >= 2
    assert findings["issue_chapter_count"] == 1
    pieces = []
    for offset in range(findings["total"]):
        response = client.get(path + f"/findings?offset={offset}&limit=1")
        assert response.status_code == 200
        result = response.json()
        assert result["total"] == findings["total"]
        assert result["issue_chapter_count"] == 1
        pieces.extend(result["items"])
    assert pieces == findings["items"]
    chapter_id = findings["items"][0]["chapter_id"]
    selected = client.get(path + f"/findings?chapter_id={chapter_id}").json()
    assert selected["items"] == findings["items"]
    original = store.get_version(document["id"], document["latest_version_id"])
    for item in findings["items"]:
        chapter = next(chapter for chapter in original["chapters"] if chapter["id"] == item["chapter_id"])
        assert chapter["text"][item["start"]:item["end"]] == item["text"]
    assert client.get(path + "/findings?offset=-1").status_code == 422
    assert client.get(path + "/findings?limit=10001").status_code == 422
    assert client.get(path + "/findings?chapter_id=missing").status_code == 404


def test_missing_chapter_save_materializes_one_item_and_stale_retry_cannot_duplicate(client):
    store, document, source = seed(client, [*range(1, 12), 17, 18])
    path = version_path(document)
    index = client.get(path + "/chapters").json()
    missing = next(item for item in index["chapters"] if item.get("placeholder") and item["number"] == 14)
    payload = {"base_version_id": document["latest_version_id"], "chapter_id": missing["id"],
               "title": "补回的夜行", "body_text": "这里是补回的正文。\n\n第二段。"}
    response = client.post(f"{PREFIX}/documents/{document['id']}/chapters/edit?summary=true", json=payload)
    assert response.status_code == 201, response.text
    summary = response.json()
    assert HEAVY.isdisjoint(summary)
    updated = client.get(f"{PREFIX}/documents/{document['id']}/versions/{summary['id']}/chapters").json()
    assert [item["number"] for item in updated["chapters"] if item.get("placeholder")] == [12, 13, 15, 16]
    real = next(item for item in updated["chapters"] if item.get("number") == 14 and not item.get("placeholder"))
    assert real["display_label"] == "第14章 补回的夜行"
    saved = store.get_version(document["id"], summary["id"])
    assert saved["text"].count("第14章 补回的夜行") == 1
    assert "这里是补回的正文。\n第二段。" in saved["text"]
    assert store.get_version(document["id"], document["latest_version_id"])["text"] == source
    assert client.post(f"{PREFIX}/documents/{document['id']}/chapters/edit?summary=true", json=payload).status_code == 409


def test_first_documents_request_and_shutdown_leave_agent_services_uninitialized(tmp_path):
    # A subprocess gives a real cold import; test collection may already have
    # imported LangGraph elsewhere in this pytest process.
    import json
    import os
    from pathlib import Path
    import subprocess
    import sys
    import textwrap

    repo = Path(__file__).resolve().parents[1]
    script = textwrap.dedent("""
        import json, sys
        from pathlib import Path
        from fastapi.testclient import TestClient
        from inkreference.api import create_app
        app = create_app(Path(sys.argv[1]), Path(sys.argv[1]) / 'no-dist')
        with TestClient(app, base_url='http://127.0.0.1') as client:
            response = client.get('/api/studio/documents')
            assert response.status_code == 200, response.text
            studio = app.state.runtime._studio
            assert studio is not None
            assert studio._runner is None and studio._models is None
            assert not any(name == 'langgraph' or name.startswith('langgraph.') for name in sys.modules)
        assert studio._runner is None and studio._models is None
        assert not any(name == 'langgraph' or name.startswith('langgraph.') for name in sys.modules)
        print(json.dumps({'runner': studio._runner, 'models': studio._models, 'langgraph_modules': 0}))
    """)
    result = subprocess.run(
        [sys.executable, "-c", script, str(tmp_path / "fresh-process")], cwd=repo,
        env={**os.environ, "PYTHONPATH": str(repo / "backend")},
        capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert json.loads(result.stdout.strip().splitlines()[-1]) == {"runner": None, "models": None, "langgraph_modules": 0}


def test_filtered_job_summaries_never_render_snapshots_or_chunk_audit_history(client, monkeypatch):
    import contextlib

    store, first, _ = seed(client)
    _, second, _ = seed(client, range(1, 3))
    runs = []
    for document in [first, first, second]:
        response = client.post(PREFIX + "/jobs", json={
            "document_id": document["id"], "version_id": document["latest_version_id"],
            "manual": True, "start": False,
        })
        assert response.status_code == 201, response.text
        runs.append(response.json())
    runner = client.app.state.runtime.studio.runner
    queries = []
    original_db = runner._db

    @contextlib.contextmanager
    def traced_db(*args, **kwargs):
        with original_db(*args, **kwargs) as db:
            db.set_trace_callback(queries.append)
            yield db

    def forbidden(*args, **kwargs):
        pytest.fail("progress summaries must not render full runs or read document snapshots")

    monkeypatch.setattr(runner, "_db", traced_db)
    monkeypatch.setattr(runner, "_render", forbidden)
    monkeypatch.setattr(store, "get_version", forbidden)
    params = {"summary": "true", "document_id": first["id"], "version_id": first["latest_version_id"]}
    response = client.get(PREFIX + "/jobs", params=params)
    assert response.status_code == 200, response.text
    summaries = response.json()["items"]
    assert {item["id"] for item in summaries} == {runs[0]["id"], runs[1]["id"]}
    heavy = {"snapshots", "results", "summary_sources", "candidates", "coverage", "events", "materialization"}
    assert all(heavy.isdisjoint(item) for item in summaries)
    limited = client.get(PREFIX + "/jobs", params={**params, "limit": 1}).json()["items"]
    assert len(limited) == 1 and limited[0] == summaries[0]
    selected = client.get(PREFIX + f"/jobs/{runs[0]['id']}?summary=true")
    assert selected.status_code == 200
    assert selected.json() == next(item for item in summaries if item["id"] == runs[0]["id"])
    assert client.get(PREFIX + "/jobs/not-found?summary=true").status_code == 404
    assert client.get(PREFIX + "/jobs?summary=true&limit=1001").status_code == 422
    assert runner.has_active_runs() is True
    assert queries and not any("snapshots_json" in query or "run_attempts" in query or "SELECT *" in query for query in queries)
