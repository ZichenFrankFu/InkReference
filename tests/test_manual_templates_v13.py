"""Human fixed-stage forms through actual HTTP/SQLite; no model is invoked.

The reusable v12 fixture supplies valid stage output, then synthetic human
facts exercise the same source proof and review boundary as model candidates.
"""
from __future__ import annotations

import copy
import json

import httpx
import pytest
from fastapi.testclient import TestClient

from inkreference.api import create_app
from inkreference.chronicle_schema import canonical_stage_schema
from test_workflow_v12 import empty, stage_result


SOURCE = "第1章 来信\n甲把信交给乙。\n第2章 隐藏\n乙收藏了范围外的秘密。\n第3章 归途\n甲沿着河岸离开。\n"
QUOTE = "甲把信交给乙。"
OUTSIDE = "乙收藏了范围外的秘密。"
STAGES = ("plot", "characters", "settings", "language")


@pytest.fixture
def human_app(tmp_path):
    app = create_app(tmp_path)
    calls = []
    with TestClient(app, base_url="http://127.0.0.1") as client:
        runtime = app.state.runtime

        def forbidden(request):
            calls.append(request)
            raise AssertionError("Manual template/paste/review must not invoke a model")

        runtime.studio.models.transport = httpx.MockTransport(forbidden)
        document = runtime.studio.documents.upload("人工作品.txt", SOURCE.encode(), title="合成人工作品")
        yield client, runtime, document, calls
        assert not calls
        assert not runtime.studio.models.list_usage()


def new_run(client, document, **overrides):
    values = {"document_id": document["id"], "version_id": document["latest_version_id"],
              "workflow_version": "4.0.0", "manual": True, "interaction_mode": "manual"}
    values.update(overrides)
    response = client.post("/api/studio/jobs", json=values)
    assert response.status_code == 201, response.text
    return response.json()


def base(run, chunk=None):
    return f"/api/studio/jobs/{run['id']}/chunks/{chunk or run['candidates'][0]['chunk_id']}"


def reference(client, document):
    response = client.get(f"/api/studio/documents/{document['id']}/reference")
    assert response.status_code == 200, response.text
    return response.json()


def source_slice(runtime, run, chunk_id=None):
    candidate = next(item for item in run["candidates"] if item["chunk_id"] == (chunk_id or run["candidates"][0]["chunk_id"]))
    text = runtime.studio.documents.get_version(run["document_id"], run["version_id"])["text"]
    return text[candidate["start"]:candidate["end"]]


def human_data(stage, text, version_id, *, event=False):
    data = stage_result(stage, text)
    if stage == "plot":
        data["plot"]["logline"] = "甲交出信件后踏上归途。"
        if event:
            schema = canonical_stage_schema("plot", version=4)["properties"]["plot"]["properties"]["events"]["items"]
            fact = empty(schema)
            fact.update(id="manual-letter", name="交出信件", theme="托付", description=QUOTE,
                        evidence=QUOTE, source_version_id=version_id,
                        revealed_at={"chapter": 1, "offset": text.index(QUOTE)})
            data["plot"]["events"] = [fact]

    def bind(node):
        if isinstance(node, dict):
            if "source_version_id" in node:
                node["source_version_id"] = version_id
            for child in node.values():
                bind(child)
        elif isinstance(node, list):
            for child in node:
                bind(child)

    bind(data)
    return data


def paste(client, run, data, *, expected_revision=None, chunk_id=None):
    return client.post(base(run, chunk_id) + "/paste", json={
        "expected_revision": run["revision"] if expected_revision is None else expected_revision,
        "content": json.dumps(data, ensure_ascii=False),
    })


def fill_four(client, runtime, run, *, event=False):
    text = source_slice(runtime, run)
    for stage in STAGES:
        template = client.get(base(run) + "/template")
        assert template.status_code == 200, template.text
        assert template.json()["stage"] == stage
        assert template.json()["revision"] == run["revision"]
        response = paste(client, run, human_data(stage, text, run["version_id"], event=event))
        assert response.status_code == 200, response.text
        run = response.json()
    return run


def test_manual_four_stage_preview_and_explicit_commit_preserve_source_and_zero_usage(human_app):
    client, runtime, document, _ = human_app
    before = reference(client, document)
    run = new_run(client, document)
    assert run["status"] == "queued" and run["snapshots"]["interaction_mode"] == "manual"
    assert run["snapshots"]["model"]["provider"] == "manual"
    run = fill_four(client, runtime, run, event=True)
    assert run["status"] == "needs_review" and run["coverage"]["complete"]
    assert run["candidates"][0]["result"]["reference"]["plot"]["events"][0]["id"] == "manual-letter"
    assert all(not item["history"] and item["attempts"] == 0 for item in run["candidates"])
    assert all(stage["tokens"] == 0 for stage in run["stages"])
    assert reference(client, document) == before  # Candidates remain outside the reference store.
    completed = client.get(base(run) + "/template").json()
    assert completed == {"stage": None, "schema": None, "template": None,
                         "revision": run["revision"], "completed": True}
    committed = client.post(base(run) + "/commit", json={
        "sections": ["plot", "characters", "settings", "style", "rhythm", "opening", "era_background"],
        "expected_revision": before["revision"],
    })
    assert committed.status_code == 200, committed.text
    state = committed.json()
    event = state["sections"]["plot"]["events"][0]
    assert event["id"] == "manual-letter" and event["evidence"] == QUOTE
    assert event["source_version_id"] == document["latest_version_id"]
    assert len(state["commits"]) == 7
    repeated = client.post(base(run) + "/commit", json={"sections": ["plot"], "expected_revision": before["revision"]})
    assert repeated.status_code == 200 and repeated.json() == state
    assert runtime.studio.documents.get_version(document["id"], document["latest_version_id"])["text"] == SOURCE


def test_template_is_read_only_and_contains_schema_not_book_text_or_keys(human_app):
    client, runtime, document, _ = human_app
    secret = "synthetic-test-key-never-export"
    profile = runtime.studio.models.save_profile({"id": "local-template", "name": "本机模型",
        "provider": "openai_compatible", "model": "synthetic-model", "base_url": "http://127.0.0.1:1234/v1",
        "credential_mode": "session", "api_key": secret, "context_window": 160000, "max_output_tokens": 16000})
    run = new_run(client, document, manual=False, interaction_mode="automatic", profile_id=profile["id"], start=False)
    before = copy.deepcopy(run)
    state = reference(client, document)
    first = client.get(base(run) + "/template")
    assert first.status_code == 200, first.text
    payload = first.json()
    assert payload["stage"] == "plot" and payload["completed"] is False
    assert payload["schema"] == canonical_stage_schema("plot", version=4)
    assert payload["template"]["plot"] == {"schema_version": 4, "logline": "", "events": [], "plotlines": []}
    assert payload["source_version_id"] == document["latest_version_id"]
    raw = json.dumps(payload, ensure_ascii=False)
    assert all(value not in raw for value in (SOURCE, QUOTE, OUTSIDE, secret, "synthetic-model", "1234/v1"))
    assert client.get(base(run) + "/template").json() == payload
    assert client.get(f"/api/studio/jobs/{run['id']}").json() == before
    assert reference(client, document) == state


@pytest.mark.parametrize("manual,mode", [(False, "manual"), (False, "semi"), (True, "automatic")])
def test_incompatible_manual_flag_and_interaction_mode_rejected_without_task(human_app, manual, mode):
    client, runtime, document, _ = human_app
    response = client.post("/api/studio/jobs", json={"document_id": document["id"],
        "version_id": document["latest_version_id"], "manual": manual, "interaction_mode": mode})
    assert response.status_code == 422, response.text
    assert not runtime.studio.runner.list_runs()


def test_manual_and_semi_same_scope_have_distinct_fingerprints_and_reuse_their_own_runs(human_app):
    client, _, document, _ = human_app
    human = new_run(client, document)
    semi = new_run(client, document, interaction_mode="semi")
    legacy_semi = new_run(client, document, interaction_mode=None)
    assert human["id"] != semi["id"]
    assert semi["id"] == legacy_semi["id"] and legacy_semi["deduplicated"]
    assert new_run(client, document)["id"] == human["id"]
    assert new_run(client, document, interaction_mode="semi")["id"] == semi["id"]


def test_http_requires_explicit_source_version_and_rejects_another_document_version(human_app):
    client, runtime, document, _ = human_app
    response = client.post("/api/studio/jobs", json={"document_id": document["id"], "manual": True, "interaction_mode": "manual"})
    assert response.status_code == 422
    other = runtime.studio.documents.upload("另一作品.txt", "第1章 外来\n外来版本。".encode())
    response = client.post("/api/studio/jobs", json={"document_id": document["id"],
        "version_id": other["latest_version_id"], "manual": True, "interaction_mode": "manual"})
    assert response.status_code == 404, response.text
    assert not runtime.studio.runner.list_runs()


def test_legacy_v3_has_no_manual_template_and_rejects_new_human_mode(human_app):
    client, _, document, _ = human_app
    old = new_run(client, document, workflow_version="3.0.0", interaction_mode="semi")
    before = copy.deepcopy(old)
    response = client.get(base(old) + "/template")
    assert response.status_code == 422
    assert client.get(f"/api/studio/jobs/{old['id']}").json() == before
    response = client.post("/api/studio/jobs", json={"document_id": document["id"],
        "version_id": document["latest_version_id"], "workflow_version": "3.0.0", "manual": True, "interaction_mode": "manual"})
    assert response.status_code == 422


@pytest.mark.parametrize("defect", ["scope", "source_version", "schema", "missing_proof", "incorrect_offset"])
def test_human_facts_use_strict_schema_and_source_proof_without_mutation(human_app, defect):
    client, runtime, document, _ = human_app
    state = reference(client, document)
    run = new_run(client, document, chapter_ranges=[{"start_chapter": 1, "end_chapter": 1}])
    data = human_data("plot", source_slice(runtime, run), run["version_id"], event=True)
    fact = data["plot"]["events"][0]
    if defect == "scope":
        fact.update(evidence=OUTSIDE, description=OUTSIDE, revealed_at={"chapter": 2, "offset": 0})
    elif defect == "source_version":
        other = runtime.studio.documents.upload("异书.txt", "第1章 同文\n甲把信交给乙。".encode())
        fact["source_version_id"] = other["latest_version_id"]
    elif defect == "schema":
        data["plot"]["schema_version"] = 3
    elif defect == "missing_proof":
        del fact["evidence"]
    else:
        fact["revealed_at"]["offset"] += 1
    rejected = paste(client, run, data)
    assert rejected.status_code == 422, rejected.text
    assert client.get(f"/api/studio/jobs/{run['id']}").json() == run
    assert reference(client, document) == state
    assert client.get(base(run) + "/template").json()["stage"] == "plot"


def test_revision_conflict_and_wrong_stage_do_not_damage_saved_candidate(human_app):
    client, runtime, document, _ = human_app
    run = new_run(client, document)
    data = human_data("plot", source_slice(runtime, run), run["version_id"], event=True)
    saved = paste(client, run, data)
    assert saved.status_code == 200, saved.text
    current = saved.json()
    template = client.get(base(current) + "/template").json()
    assert template["stage"] == "characters" and template["revision"] == current["revision"]
    for revision in (run["revision"], current["revision"]):
        rejected = paste(client, current, data, expected_revision=revision)
        assert rejected.status_code == (409 if revision == run["revision"] else 422), rejected.text
        assert client.get(f"/api/studio/jobs/{run['id']}").json() == current
    assert client.get(base(current, "missing-chunk") + "/template").status_code == 404
    assert client.get("/api/studio/jobs/missing-run/chunks/chunk-000001/template").status_code == 404


def test_latest_upload_does_not_retarget_a_fixed_manual_template(human_app):
    client, runtime, document, _ = human_app
    run = new_run(client, document)
    new = runtime.studio.documents.create_version(document["id"], document["latest_version_id"], {}, manual_text=SOURCE + "新修订独有正文。")
    assert new["id"] != run["version_id"]
    template = client.get(base(run) + "/template").json()
    assert template["source_version_id"] == run["version_id"]
    assert "新修订独有正文" not in json.dumps(template, ensure_ascii=False)
    final = fill_four(client, runtime, run, event=True)
    assert final["version_id"] == run["version_id"] and final["status"] == "needs_review"


def test_incomplete_and_stale_reference_commit_are_rejected_without_facts(human_app):
    client, runtime, document, _ = human_app
    before = reference(client, document)
    run = new_run(client, document)
    rejected = client.post(base(run) + "/commit", json={"sections": ["plot"], "expected_revision": before["revision"]})
    assert rejected.status_code == 409
    run = fill_four(client, runtime, run, event=True)
    changed = client.put(f"/api/studio/documents/{document['id']}/reference",
        json={"structure_type": "narrative", "expected_revision": before["revision"]})
    assert changed.status_code == 200, changed.text
    rejected = client.post(base(run) + "/commit", json={"sections": ["plot"], "expected_revision": before["revision"]})
    assert rejected.status_code == 409, rejected.text
    assert reference(client, document) == changed.json()
    assert not reference(client, document)["commits"]


def test_unreviewed_semi_chunk_is_never_partially_reused_by_human_mode(human_app):
    client, runtime, document, _ = human_app
    old = new_run(client, document, interaction_mode="semi",
        chapter_ranges=[{"start_chapter": 1, "end_chapter": 1}])
    old = fill_four(client, runtime, old, event=True)
    assert old["status"] == "needs_review"
    human = new_run(client, document, chapter_ranges=[{"start_chapter": 1, "end_chapter": 3}])
    assert human["scope"]["reused_chunks"] == []
    assert all(item["status"] == "pending" and item["result"] is None for item in human["candidates"])
    assert all(stage["completed"] == 0 for stage in human["stages"])


def linked_work(client, runtime, document):
    runtime.store.create_record("works", {"作品ID": "MANUAL-WORK", "作品": "合成人工作品"})
    linked = client.put(f"/api/studio/documents/{document['id']}/catalog", json={
        "expected_work_id": None, "expected_version_id": document["latest_version_id"],
        "work_id": "MANUAL-WORK",
    })
    assert linked.status_code == 200, linked.text


def test_approved_complete_human_source_chunk_can_be_reused_with_its_audit(human_app):
    client, runtime, document, _ = human_app
    linked_work(client, runtime, document)
    old = new_run(client, document,
        chapter_ranges=[{"start_chapter": 1, "end_chapter": 1}])
    assert old["snapshots"]["model"]["model"] == "手动录入"
    old = fill_four(client, runtime, old, event=True)
    approved = client.post(f"/api/studio/jobs/{old['id']}/approve", json={"expected_revision": old["revision"]})
    assert approved.status_code == 200, approved.text
    assert approved.json()["status"] == "approved"
    original = client.get(f"/api/studio/jobs/{old['id']}").json()
    human = new_run(client, document, chapter_ranges=[{"start_chapter": 1, "end_chapter": 3}])
    assert len(human["scope"]["reused_chunks"]) == 1
    reused = human["scope"]["reused_chunks"][0]
    assert reused["run_id"] == old["id"] and reused["chunk_id"] == old["candidates"][0]["chunk_id"]
    assert human["candidates"][0]["status"] == "completed"
    assert human["candidates"][0]["result"] == old["candidates"][0]["result"]
    assert client.get(base(human) + "/template").json()["completed"] is True
    assert any(item["status"] == "pending" for item in human["candidates"][1:])
    assert client.get(f"/api/studio/jobs/{old['id']}").json() == original


def test_approved_semi_candidates_do_not_cross_into_human_mode(human_app):
    client, runtime, document, _ = human_app
    linked_work(client, runtime, document)
    semi = new_run(client, document, interaction_mode="semi",
        chapter_ranges=[{"start_chapter": 1, "end_chapter": 1}])
    assert semi["snapshots"]["model"]["model"] == "网页复制粘贴"
    semi = fill_four(client, runtime, semi, event=True)
    approved = client.post(f"/api/studio/jobs/{semi['id']}/approve", json={"expected_revision": semi["revision"]})
    assert approved.status_code == 200, approved.text
    assert approved.json()["status"] == "approved"
    original = approved.json()
    human = new_run(client, document, chapter_ranges=[{"start_chapter": 1, "end_chapter": 3}])
    assert human["snapshots"]["model"]["model"] == "手动录入"
    # Human-authored and external-model candidates have distinct provenance.
    assert human["scope"]["reused_chunks"] == []
    assert all(item["status"] == "pending" and item["result"] is None for item in human["candidates"])
    assert client.get(f"/api/studio/jobs/{semi['id']}").json() == original
