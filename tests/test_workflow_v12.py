"""Contract 4 chunk planning and durable task controls with real LangGraph.

Provider calls use HTTPX wire fixtures; no live model or billable request runs.
"""
import copy
import hashlib
import json
import sqlite3
import threading
from contextlib import closing

import httpx
import pytest

from inkreference.chronicle_schema import canonical_stage_schema
from inkreference.database import DataError
from inkreference.documents import DocumentStore
from inkreference.fixed_pipeline import FixedPipeline
from inkreference.models import ModelService
from inkreference.skills import SkillStore, build_stage_prompt
from inkreference.volumes import VolumeStore
from inkreference.workflow_definitions import ADAPTIVE_WORKFLOW, WorkflowStore
from inkreference.workflows import WorkflowRunner


def empty(schema):
    if "enum" in schema: return schema["enum"][0]
    kind = schema["type"]
    if isinstance(kind, list):
        if "null" in kind: return None
        kind = kind[0]
    if kind == "object": return {name: empty(schema["properties"][name]) for name in schema.get("required", [])}
    if kind == "array": return []
    if kind in {"integer", "number"}: return schema.get("minimum", 0)
    return "x" * schema.get("minLength", 0)


def stage_result(stage, text):
    schema = canonical_stage_schema(stage, version=4)
    result = empty(schema)
    if stage == "language":
        item_schema = schema["properties"]["style"]["properties"]["narrative_composition"]["items"]
        offset = text.find("甲")
        if offset < 0: offset = 0
        result["style"]["narrative_composition"] = []
        for kind in ("action", "description", "psychology", "exposition"):
            item = empty(item_schema)
            item.update(kind=kind, percentage=25, assessment="合成分类", evidence=text[offset:offset + 1], revealed_at={"chapter": None, "offset": offset}, source_version_id="")
            result["style"]["narrative_composition"].append(item)
    return result


def setup(tmp_path, text=None, *, block=False):
    documents = DocumentStore(tmp_path)
    document = documents.upload("synthetic.txt", (text or "第1章 来信\n甲把信交给乙。\n第2章 离开\n乙离开。\n").encode(), title="合成作品")
    entered, release, requests = threading.Event(), threading.Event(), []
    def handle(request):
        payload = json.loads(request.content)
        prompt = payload["messages"][-1]["content"]
        stage = prompt.split("固定阶段：", 1)[1].split("（", 1)[0]
        body = prompt.split("<source_text>\n", 1)[1].rsplit("\n</source_text>", 1)[0]
        requests.append({"stage": stage, "text": body, "payload": payload})
        if block and len(requests) == 1:
            entered.set()
            assert release.wait(10), "test must release the synthetic response"
        result = stage_result(stage, body)
        return httpx.Response(200, json={"choices": [{"message": {"content": json.dumps(result, ensure_ascii=False)}, "finish_reason": "stop"}], "usage": {"prompt_tokens": 20, "completion_tokens": 10}})
    models = ModelService(tmp_path, transport=httpx.MockTransport(handle))
    profile = models.save_profile({"provider": "openai_compatible", "model": "synthetic-v4", "base_url": "http://127.0.0.1:1234/v1", "context_window": 160000, "max_output_tokens": 16000})
    runner = WorkflowRunner(tmp_path, documents=documents, models=models)
    return runner, document, models, profile, entered, release, requests


def test_default_v4_real_graph_contract_and_coverage(tmp_path):
    runner, doc, models, profile, _, _, requests = setup(tmp_path)
    run = runner.create_run(doc["id"], profile_id=profile["id"])
    assert run["workflow_version"] == "4.0.0"
    assert run["snapshots"]["contract_version"] == 4
    final = runner.execute(run["id"])
    assert final["status"] == "needs_review", final["error"]
    assert final["coverage"]["complete"]
    assert [request["stage"] for request in requests] == ["plot", "characters", "settings", "language"]
    assert len(models.list_usage(run["id"])) == 4
    assert sum(stage["tokens"] for stage in final["stages"]) == 120
    assert final["candidates"][0]["result"]["reference"]["style"]["schema_version"] == 4


def test_pause_preserves_successful_wire_call_and_resumes_without_double_billing(tmp_path):
    runner, doc, models, profile, entered, release, requests = setup(tmp_path, block=True)
    run = runner.create_run(doc["id"], profile_id=profile["id"])
    runner.start(run["id"])
    try:
        assert entered.wait(10)
        pending = runner.pause(run["id"])
        assert pending["status"] == "cancelling" and pending["stage"] == "pausing"
    finally:
        release.set()
    paused = runner.wait(run["id"], 10)
    assert paused["status"] == "paused", paused["error"]
    assert len(requests) == 1
    assert paused["stages"][0]["completed"] == 1
    assert len(models.list_usage(run["id"])) == 1
    runner.resume(run["id"])
    final = runner.wait(run["id"], 10)
    assert final["status"] == "needs_review", final["error"]
    assert [request["stage"] for request in requests] == ["plot", "characters", "settings", "language"]
    assert len(models.list_usage(run["id"])) == 4
    assert sum(stage["tokens"] for stage in final["stages"]) == 120


def test_terminate_keeps_received_usage_and_reopens_without_model_calls_after_restart(tmp_path):
    runner, doc, models, profile, entered, release, requests = setup(tmp_path, block=True)
    run = runner.create_run(doc["id"], profile_id=profile["id"])
    runner.start(run["id"])
    try:
        assert entered.wait(10)
        stopping = runner.terminate(run["id"])
        assert stopping["stage"] == "terminating"
        with pytest.raises(DataError): runner.pause(run["id"])
    finally:
        release.set()
    final = runner.wait(run["id"], 10)
    assert final["status"] == "terminated", final["error"]
    assert len(requests) == len(models.list_usage(run["id"])) == 1
    restarted = WorkflowRunner(tmp_path, documents=runner.documents, models=models)
    assert restarted.get_run(run["id"])["status"] == "terminated"
    with pytest.raises(DataError):
        restarted.start(run["id"])
    with pytest.raises(DataError):
        restarted.paste_chunk(run["id"], run["candidates"][0]["chunk_id"], json.dumps({"characters": []}), final["revision"])
    assert len(requests) == 1
    snapshots = restarted.get_run(run["id"])["snapshots"]
    reopened = restarted.reopen(run["id"])
    assert reopened["status"] == "paused"
    assert len(requests) == len(models.list_usage(run["id"])) == 1
    assert restarted.get_run(run["id"])["snapshots"] == snapshots
    assert reopened["stages"][0]["completed"] == 1
    restarted.resume(run["id"])
    completed = restarted.wait(run["id"], 10)
    assert completed["status"] == "needs_review", completed["error"]
    assert [request["stage"] for request in requests] == ["plot", "characters", "settings", "language"]
    assert len(models.list_usage(run["id"])) == 4


def test_terminated_intent_survives_process_interruption_without_auto_calls(tmp_path):
    runner, doc, _, profile, _, _, requests = setup(tmp_path)
    run = runner.create_run(doc["id"], profile_id=profile["id"])
    with runner._db(write=True) as db:
        db.execute("UPDATE run_jobs SET status='cancelling',cancel_requested=1,stop_kind='terminated',stage='terminating' WHERE id=?", (run["id"],))
    restarted = WorkflowRunner(tmp_path, documents=runner.documents, models=runner.models)
    assert restarted.get_run(run["id"])["status"] == "terminated"
    assert not requests


def test_v4_story_volumes_do_not_force_tiny_model_chunks(tmp_path):
    text = "".join(f"第{i}章 来信\n" + "甲送信。" * 50 + "\n" for i in range(1, 9))
    runner, doc, _, profile, _, _, _ = setup(tmp_path, text)
    version = runner.documents.get_version(doc["id"])
    VolumeStore(tmp_path, runner.documents).save(doc["id"], version["id"], [{"title": f"卷{i}", "start_chapter": i, "end_chapter": i} for i in range(1, 9)], 0)
    run = runner.create_run(doc["id"], profile_id=profile["id"])
    assert run["total"] == 1
    assert run["snapshots"]["chunk_groups"][0]["start_chapter"] == 1
    assert run["snapshots"]["chunk_groups"][0]["end_chapter"] == 8


def test_v4_smallest_stage_context_counts_full_prompts_and_preserves_every_character(tmp_path):
    text = "第1章 长信\n" + "甲写信。\n" * 35000
    runner, doc, models, profile, _, _, _ = setup(tmp_path, text)
    short = models.save_profile({"provider": "openai_compatible", "model": "synthetic-small", "base_url": "http://127.0.0.1:1234/v1", "context_window": 24000, "max_output_tokens": 1024})
    workflow = copy.deepcopy(ADAPTIVE_WORKFLOW)
    workflow.update(id="smallest-stage", max_output_tokens=1024, stage_models={"characters": short["id"]}, stage_prompts={"plot": "细读原文。" * 300})
    runner.workflows.save(workflow)
    run = runner.create_run(doc["id"], workflow_id=workflow["id"], workflow_version="4.0.0", profile_id=profile["id"])
    assert run["total"] > 1
    with closing(sqlite3.connect(runner.db_path)) as db:
        db.row_factory = sqlite3.Row
        chunks = [dict(row) for row in db.execute("SELECT * FROM run_chunks WHERE run_id=? ORDER BY position", (run["id"],))]
    assert "".join(chunk["text"] for chunk in chunks) == runner.documents.get_version(doc["id"])["text"]
    assert chunks[0]["start"] == 0 and chunks[-1]["end"] == len(text)
    assert all(left["end"] == right["start"] for left, right in zip(chunks, chunks[1:]))
    for chunk in chunks:
        for stage in ("plot", "characters", "settings", "language"):
            with runner._db() as db: context = runner._fixed_context(db, run["id"], chunk, stage)
            model = runner._fixed_model(run["snapshots"], stage)
            budget = model["context_window"] - runner._fixed_output_reserve(run["snapshots"], stage)
            assert runner._estimate(runner._fixed_messages(run["snapshots"], chunk["text"], stage, context)) <= budget


def test_v3_prompts_and_cache_namespace_remain_immutable(tmp_path):
    skills, workflows = SkillStore(tmp_path), WorkflowStore(tmp_path)
    old = skills.get("unified-reference", "3.0.0")
    assert old["contract_version"] == 3
    assert workflows.get("default", "3.0.0")["chunk_chars"] == 100000
    assert skills.get()["contract_version"] == 4
    assert workflows.get()["version"] == "4.0.0"
    changed = copy.deepcopy(old); changed["prompt"] += "改写"
    with pytest.raises(DataError): skills.save(changed)
    for stage in ("plot", "characters", "settings", "language"):
        # Exact historical prompt hashes captured from the pre-v12 source.
        digest = hashlib.sha256(build_stage_prompt(old, stage, "甲。", context={}).encode()).hexdigest()
        assert digest == V3_PROMPT_HASHES[stage]
    assert FixedPipeline._fixed_cache_key([], {}, 20, 4) != FixedPipeline._fixed_cache_key([], {}, 20, 3)


V3_PROMPT_HASHES = {'plot': '6c5daccbacc3fb017d32904ffcdd227599327a8eb577b7e149cac269d6609c53', 'characters': 'dee3acdc78e7163dfb92f8aec555232dd5551679e5237ec8ce248c0cbaa6171a', 'settings': 'a034c9ce5f3aa4ed1799037124e113135dd3e66190c8463ea7e0116301f19add', 'language': '56685a165fc35275083a1fa51782d3f1cc1ea7ecf0b11f4cfb3d28b86fbe0ba1'}


def test_v4_event_memory_preserves_progress_and_old_contract_memory_is_unchanged(tmp_path):
    runner, doc, _, profile, _, _, _ = setup(tmp_path)
    current = runner.create_run(doc["id"], profile_id=profile["id"])
    historical = runner.create_run(doc["id"], workflow_version="3.0.0", profile_id=profile["id"])
    for run in (current, historical):
        with runner._db(write=True) as db:
            chunk = dict(db.execute("SELECT * FROM run_chunks WHERE run_id=? LIMIT 1", (run["id"],)).fetchone())
            db.execute("INSERT INTO run_entity_index (run_id,kind,id,label,summary,position,narrative_progress) VALUES (?,?,?,?,?,?,?)", (run["id"], "events", "letter", "甲", "甲送信", 0, 50.0))
        with runner._db() as db: memory = runner._fixed_context(db, run["id"], chunk, "characters")
        assert memory["events"][0]["id"] == "letter"
        if run["snapshots"]["contract_version"] == 4:
            assert memory["events"][0]["narrative_progress"] == 50
        else:
            assert "narrative_progress" not in memory["events"][0]


def test_existing_entity_index_migrates_without_losing_saved_ids(tmp_path):
    runner, doc, _, profile, _, _, _ = setup(tmp_path)
    run = runner.create_run(doc["id"], profile_id=profile["id"])
    with runner._db(write=True) as db:
        db.execute("ALTER TABLE run_entity_index DROP COLUMN narrative_progress")
        db.execute("INSERT INTO run_entity_index (run_id,kind,id,label,summary,position) VALUES (?,?,?,?,?,?)", (run["id"], "events", "existing-id", "甲", "旧事件", 0))
    restarted = WorkflowRunner(tmp_path, documents=runner.documents, models=runner.models)
    with restarted._db() as db:
        old = db.execute("SELECT id,summary,narrative_progress FROM run_entity_index WHERE id='existing-id'").fetchone()
    assert tuple(old) == ("existing-id", "旧事件", None)


def test_new_skill_contract_cannot_be_replaced_by_legacy_stage_schema(tmp_path):
    runner, doc, _, profile, _, _, requests = setup(tmp_path)
    uploaded = runner.skills.upload("SKILL.md", b"---\nid: concise-v4\nname: Concise\nversion: 1.0.0\ncontract_version: 4\n---\nSummarize the major story turns.", require_contract_version=4)
    assert uploaded["contract_version"] == 4
    bad = copy.deepcopy(uploaded)
    bad["id"] = "bad-output-shape"
    bad["output_schema"] = runner.skills.get("unified-reference", "3.0.0")["output_schema"]
    with pytest.raises(DataError): runner.skills.save(bad)
    workflow = copy.deepcopy(ADAPTIVE_WORKFLOW)
    workflow.update(id="mixed-contract", stage_skills={"characters": {"id": "unified-reference", "version": "3.0.0"}})
    runner.workflows.save(workflow)
    with pytest.raises(DataError) as rejected:
        runner.create_run(doc["id"], workflow_id=workflow["id"], workflow_version="4.0.0", profile_id=profile["id"])
    assert "同一 v4" in rejected.value.detail
    assert not requests


def test_chapter_ranges_are_normalized_deduplicated_and_do_not_send_outside_text(tmp_path):
    text = "第1章 外部\n甲绝不能出现在选中输入之外甲。\n第2章 选中\n甲带信入城。\n第3章 排除\n甲秘密三。\n第4章 选中\n甲来到城门。"
    runner, doc, models, profile, _, _, requests = setup(tmp_path, text)
    ranges = [{"start_chapter": 4, "end_chapter": 4}, {"start_chapter": 2, "end_chapter": 2}, {"start_chapter": 2, "end_chapter": 2}]
    run = runner.create_run(doc["id"], profile_id=profile["id"], chapter_ranges=ranges)
    assert run["scope"]["chapter_ranges"] == [{"start_chapter": 2, "end_chapter": 2}, {"start_chapter": 4, "end_chapter": 4}]
    duplicate = runner.create_run(doc["id"], profile_id=profile["id"], chapter_ranges=list(reversed(ranges)))
    assert duplicate["id"] == run["id"] and duplicate["deduplicated"]
    result = runner.execute(run["id"])
    assert result["status"] == "needs_review", result["error"]
    assert result["coverage"]["complete"]
    assert len(requests) == 8
    for request in requests:
        assert "秘密三" not in request["text"] and "绝不能" not in request["text"]
        assert "第2章" in request["text"] or "第4章" in request["text"]
    assert len(models.list_usage(run["id"])) == 8
    again = runner.create_run(doc["id"], profile_id=profile["id"], chapter_ranges=ranges)
    assert again["id"] == run["id"] and again["status"] == "needs_review"
    assert len(requests) == 8


def test_range_omits_empty_and_missing_chapters_and_rejects_only_empty(tmp_path):
    runner, doc, _, profile, _, _, _ = setup(tmp_path, "第1章 出发\n甲出发。\n第3章 空白\n")
    run = runner.create_run(doc["id"], profile_id=profile["id"], chapter_ranges=[{"start_chapter": 1, "end_chapter": 3}])
    assert run["scope"]["chapter_count"] == 3
    assert [chapter["index"] for chapter in run["scope"]["skipped_chapters"]] == [2, 3]
    with pytest.raises(DataError) as empty_range:
        runner.create_run(doc["id"], profile_id=profile["id"], chapter_ranges=[{"start_chapter": 2, "end_chapter": 3}])
    assert empty_range.value.status_code == 422
    assert len(empty_range.value.detail["skipped_chapters"]) == 2
    with pytest.raises(DataError):
        runner.create_run(doc["id"], profile_id=profile["id"], chapter_ranges=[{"start_chapter": 0, "end_chapter": 1}])


def test_ab_models_remain_independent_and_select_preserves_both_candidates(tmp_path):
    runner, doc, models, a, _, _, requests = setup(tmp_path)
    b = models.save_profile({"provider": "openai_compatible", "model": "synthetic-alternative", "base_url": "http://127.0.0.1:1234/v1", "context_window": 160000, "max_output_tokens": 16000})
    comparison = runner.create_comparison(doc["id"], [{"label": "A", "profile_id": a["id"]}, {"label": "B", "profile_id": b["id"]}], chapter_ranges=[{"start_chapter": 1, "end_chapter": 1}])
    assert not requests
    a_id, b_id = [variant["run_id"] for variant in comparison["variants"]]
    assert a_id != b_id
    for run_id in (a_id, b_id):
        final = runner.execute(run_id)
        assert final["status"] == "needs_review", final["error"]
        assert len(models.list_usage(run_id)) == 4
    original_b = copy.deepcopy(runner.get_run(b_id)["candidates"])
    chosen = runner.select_comparison(comparison["id"], a_id, comparison["revision"])
    assert chosen["selected_run_id"] == a_id and chosen["revision"] == 2
    assert runner.get_run(b_id)["candidates"] == original_b
    assert len(requests) == 8
    with pytest.raises(DataError): runner.select_comparison(comparison["id"], b_id, 1)
    repeated = runner.create_comparison(doc["id"], [{"label": "A", "profile_id": a["id"]}, {"label": "B", "profile_id": b["id"]}], chapter_ranges=[{"start_chapter": 1, "end_chapter": 1}])
    assert [variant["run_id"] for variant in repeated["variants"]] == [a_id, b_id]
    assert len(requests) == 8
    with pytest.raises(DataError):
        runner.create_comparison(doc["id"], [{"profile_id": a["id"]}, {"profile_id": a["id"]}])


def test_only_approved_complete_source_chunks_are_reused_across_ranges(tmp_path):
    runner, doc, models, profile, _, _, requests = setup(tmp_path)
    old = runner.create_run(doc["id"], profile_id=profile["id"], chapter_ranges=[{"start_chapter": 1, "end_chapter": 1}])
    old = runner.execute(old["id"])
    assert old["status"] == "needs_review"
    with runner._db(write=True) as db: db.execute("UPDATE run_jobs SET status='approved' WHERE id=?", (old["id"],))
    new = runner.create_run(doc["id"], profile_id=profile["id"], chapter_ranges=[{"start_chapter": 1, "end_chapter": 2}])
    assert len(new["scope"]["reused_chunks"]) == 1
    assert new["scope"]["reused_chunks"][0]["run_id"] == old["id"]
    assert new["candidates"][0]["status"] == "completed"
    assert new["stages"][0]["tokens"] == 0
    final = runner.execute(new["id"])
    assert final["status"] == "needs_review", final["error"]
    assert final["coverage"]["complete"]
    assert len(requests) == 8
    assert all("第1章" not in request["text"] for request in requests[4:])
    assert len(models.list_usage(old["id"])) == len(models.list_usage(new["id"])) == 4


def test_partial_old_chunk_is_linked_but_not_cut_or_sent_outside_selected_scope(tmp_path):
    source = "第1章 前文\n甲外部秘密。\n第2章 重叠\n甲交信。\n第3章 新文\n甲离开。"
    runner, doc, _, profile, _, _, requests = setup(tmp_path, source)
    old = runner.create_run(doc["id"], profile_id=profile["id"], chapter_ranges=[{"start_chapter": 1, "end_chapter": 2}])
    runner.execute(old["id"])
    with runner._db(write=True) as db: db.execute("UPDATE run_jobs SET status='approved' WHERE id=?", (old["id"],))
    new = runner.create_run(doc["id"], profile_id=profile["id"], chapter_ranges=[{"start_chapter": 2, "end_chapter": 3}])
    assert not new["scope"]["reused_chunks"]
    assert new["scope"]["overlapping_runs"][0]["chapter_ranges"] == [{"start_chapter": 2, "end_chapter": 2}]
    final = runner.execute(new["id"])
    assert final["status"] == "needs_review", final["error"]
    assert all("外部秘密" not in request["text"] for request in requests[4:])
    assert len(requests) == 8


def test_ab_rejects_effectively_identical_stage_models_despite_different_main_profiles(tmp_path):
    runner, doc, models, a, _, _, requests = setup(tmp_path)
    b = models.save_profile({"provider": "openai_compatible", "model": "alternative", "base_url": "http://127.0.0.1:1234/v1", "context_window": 160000})
    definition = copy.deepcopy(ADAPTIVE_WORKFLOW)
    definition.update(id="identical-stage-targets", stage_models={stage: a["id"] for stage in ("plot", "characters", "settings", "language")})
    runner.workflows.save(definition)
    with pytest.raises(DataError):
        runner.create_comparison(doc["id"], [{"profile_id": a["id"]}, {"profile_id": b["id"]}], workflow_id=definition["id"])
    assert not requests


def test_ab_write_gate_requires_selection_and_blocks_switch_after_contribution(tmp_path):
    runner, doc, models, a, _, _, _ = setup(tmp_path)
    b = models.save_profile({"provider": "openai_compatible", "model": "alternative-gate", "base_url": "http://127.0.0.1:1234/v1", "context_window": 160000})
    group = runner.create_comparison(doc["id"], [{"profile_id": a["id"]}, {"profile_id": b["id"]}])
    a_id, b_id = [variant["run_id"] for variant in group["variants"]]
    for run_id in (a_id, b_id): runner.execute(run_id)
    with pytest.raises(DataError): runner.assert_comparison_selection(a_id)
    selected = runner.select_comparison(group["id"], a_id, 1)
    runner.assert_comparison_selection(a_id, group["id"])
    with pytest.raises(DataError): runner.assert_comparison_selection(b_id, group["id"])
    with runner._db(write=True) as db: db.execute("UPDATE run_jobs SET status='approved' WHERE id=?", (a_id,))
    with pytest.raises(DataError): runner.select_comparison(group["id"], b_id, selected["revision"])
    assert runner.get_comparison(group["id"])["selected_run_id"] == a_id


@pytest.mark.parametrize("source,expected", [("第1章 初见\n甲到城门。\n第3章 离开\n甲离城。", 100.0), ("第20章 初见\n甲到城门。\n第21章 离开\n甲离城。", 100.0)])
def test_v4_snapshot_narrative_context_uses_global_chapter_coordinates(tmp_path, source, expected):
    runner, doc, _, profile, _, _, _ = setup(tmp_path, source)
    run = runner.create_run(doc["id"], profile_id=profile["id"])
    context = run["snapshots"]["document"]["narrative_context"]
    expected_total = 3 if "第3章" in source else 21
    assert context["chapter_count"] == expected_total
    offset = source.rindex("甲")
    event = {"narrative_progress": 0, "revealed_at": {"chapter": expected_total, "offset": offset}, "evidence": "甲"}
    with runner._db() as db: chunk = dict(db.execute("SELECT * FROM run_chunks WHERE run_id=? LIMIT 1", (run["id"],)).fetchone())
    runner._fixed_progress({"events": [event]}, run["snapshots"], chunk)
    assert event["narrative_progress"] == expected
