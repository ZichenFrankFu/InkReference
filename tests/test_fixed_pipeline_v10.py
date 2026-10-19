"""Real LangGraph and SQLite; synthetic model boundary, no network inference."""
import copy
import json
import sqlite3

import pytest

from inkreference.chronicle_schema import canonical_stage_schema
from inkreference.database import DataError
from inkreference.documents import DocumentStore
from inkreference.skills import FIXED_BUILTIN_SKILL, SkillStore, validate_skill
from inkreference.workflow_definitions import FIXED_STAGES, FIXED_WORKFLOW, WorkflowStore, validate_workflow
from inkreference.workflows import WorkflowRunner


def empty(schema):
    if "enum" in schema: return schema["enum"][0]
    kind = schema["type"]
    if isinstance(kind, list):
        if "null" in kind: return None
        kind = kind[0]
    if kind == "object": return {key: empty(schema["properties"][key]) for key in schema.get("required", [])}
    if kind == "array": return []
    if kind in {"integer", "number"}: return schema.get("minimum", 0)
    return "x" * schema.get("minLength", 0)


class Models:
    def __init__(self, fail=None):
        self.calls, self.fail = [], fail
    def snapshot(self, identifier):
        return {"id": identifier, "provider": "test", "model": "synthetic-fixed", "context_window": 64000, "max_output_tokens": 1024}
    def estimate_messages(self, messages):
        return {"input_tokens": sum(len(message["content"]) for message in messages)}
    def invoke(self, profile_id, messages, **kw):
        stage = kw["chunk_id"].rsplit("::", 1)[1]
        prompt = messages[-1]["content"]
        text = prompt.split("<source_text>\n", 1)[1].rsplit("\n</source_text>", 1)[0]
        context = json.loads(prompt.split("前序实体与事件：\n", 1)[1].split("\n固定输出 Schema：", 1)[0])
        call = {"stage": stage, "text": text, "prompt": prompt, "context": context, "profile_id": profile_id, **kw}
        self.calls.append(call)
        if self.fail:
            value = self.fail(call, len(self.calls))
            if isinstance(value, Exception): raise value
            if value is not None: return {"content": json.dumps(value), "usage": {"input_tokens": 12, "output_tokens": 8}}
        result = empty(canonical_stage_schema(stage, version=2))
        if stage == "plot":
            event = empty(canonical_stage_schema(stage, version=2)["properties"]["plot"]["properties"]["events"]["items"])
            event.update(id="event-" + kw["chunk_id"].split("::")[0], name="传信", description="甲传信。", evidence=text[:5])
            event["revealed_at"]["offset"] = 0
            result["plot"]["events"] = [event]
            result["plot"]["logline"] = "甲传信。"
        if stage == "characters":
            item = empty(canonical_stage_schema(stage, version=2)["properties"]["characters"]["items"])
            item.update(id="character-jia", name="甲", intro="甲传信。", evidence=text[:5])
            item["revealed_at"]["offset"] = 0
            result["characters"] = [item]
        return {"content": json.dumps(result, ensure_ascii=False), "call_id": str(len(self.calls)), "usage": {"input_tokens": 12, "output_tokens": 8}}


def setup(tmp_path, *, fail=None, text="第1章 来信\n甲把信交给乙。\n第2章 离开\n甲离开房间。\n", patch=None):
    docs, workflows = DocumentStore(tmp_path), WorkflowStore(tmp_path)
    document = docs.upload("synthetic.txt", text.encode(), title="固定流程合成作品")
    definition = {**copy.deepcopy(FIXED_WORKFLOW), "id": "fixed-test", "chunk_chars": 100, "max_input_tokens": 50000, "max_output_tokens": 1024, "retry_limit": 0, **(patch or {})}
    workflows.save(definition)
    models = Models(fail)
    runner = WorkflowRunner(tmp_path, documents=docs, workflows=workflows, models=models)
    run = runner.create_run(document["id"], workflow_id="fixed-test", workflow_version="2.0.0", profile_id="synthetic")
    return runner, run, models, docs


def test_fixed_real_graph_stage_order_and_complete_source_coverage(tmp_path):
    runner, run, models, docs = setup(tmp_path, text="第1章 来信\n甲把信交给乙。\n" * 20)
    final = runner.execute(run["id"])
    assert final["status"] == "needs_review", final["error"]
    assert final["coverage"]["complete"]
    assert final["progress"] == 1
    assert [call["stage"] for call in models.calls] == [stage for stage in FIXED_STAGES for _ in range(run["total"])]
    source = docs.get_version(run["document_id"])["text"]
    for stage in FIXED_STAGES:
        assert "".join(call["text"] for call in models.calls if call["stage"] == stage) == source
    assert all(len(json.dumps(call["context"], ensure_ascii=False, separators=(",", ":"))) <= 6000 for call in models.calls)
    assert any(call["context"]["events"] for call in models.calls if call["stage"] == "characters")
    assert all(stage["completed"] == run["total"] and stage["tokens"] == run["total"] * 20 for stage in final["stages"])
    assert all(item["stage"] in FIXED_STAGES for candidate in final["candidates"] for item in candidate["history"])
    with sqlite3.connect(tmp_path / "checkpoints.sqlite3") as db:
        assert db.execute("SELECT COUNT(*) FROM checkpoints").fetchone()[0] > len(models.calls)
    # Candidate only; nothing is approved without the existing human-review path.
    assert final["materialization"] is None


def test_failed_stage_resume_preserves_success_and_does_not_repeat_plot(tmp_path):
    failed = {"once": False}
    def failure(call, n):
        if call["stage"] == "characters" and not failed["once"]:
            failed["once"] = True
            return DataError(502, "synthetic transient")
    runner, run, models, _ = setup(tmp_path, fail=failure)
    state = runner.execute(run["id"])
    assert state["status"] == "failed"
    plot_calls = len([call for call in models.calls if call["stage"] == "plot"])
    assert state["stages"][0]["progress"] == 1
    final = runner.execute(run["id"])
    assert final["status"] == "needs_review", final["error"]
    assert len([call for call in models.calls if call["stage"] == "plot"]) == plot_calls
    assert final["stages"][1]["attempts"] == run["total"] + 1


def test_wrong_stage_schema_retries_locally_and_retains_audit(tmp_path):
    runner, run, models, _ = setup(tmp_path, fail=lambda call, n: {"characters": []} if n == 1 else None, patch={"retry_limit": 1})
    final = runner.execute(run["id"])
    assert final["status"] == "needs_review", final["error"]
    assert [call["stage"] for call in models.calls[:2]] == ["plot", "plot"]
    assert final["candidates"][0]["history"][0]["status"] == "validation_error"


def test_stage_budget_blocks_call_before_submission(tmp_path):
    runner, run, models, _ = setup(tmp_path, patch={"stage_settings": {"characters": {"budget_tokens": 1}}})
    final = runner.execute(run["id"])
    assert final["status"] == "failed" and final["stage"] == "budget"
    assert all(call["stage"] == "plot" for call in models.calls)
    assert final["stages"][0]["progress"] == 1
    assert final["stages"][1]["attempts"] == 0


def test_stage_input_reserves_schema_memory_and_output(tmp_path):
    runner, run, models, docs = setup(tmp_path)
    assert all(attempt["input_estimate"] + attempt["output_reserve"] <= 64000 for candidate in run["candidates"] for attempt in candidate["history"])
    models.snapshot = lambda _: {"id": "small", "provider": "test", "model": "small", "context_window": 100, "max_output_tokens": 64}
    with pytest.raises(DataError, match="上下文"):
        runner.create_run(run["document_id"], workflow_id="fixed-test", workflow_version="2.0.0", profile_id="small")
    assert not models.calls


@pytest.mark.parametrize("patch", [{"nodes": {"summary": False, "characters": True, "events": True, "cliches": True}}, {"stage_prompts": {"unknown": "x"}}, {"stage_settings": {"plot": {"topology": []}}}])
def test_fixed_workflow_rejects_topology_changes(patch):
    with pytest.raises(DataError): validate_workflow({**copy.deepcopy(FIXED_WORKFLOW), **patch})


def test_skill_contract_is_locked_and_stage_instructions_remain_customizable(tmp_path):
    skill = {**copy.deepcopy(FIXED_BUILTIN_SKILL), "id": "custom-content", "prompt": "关注能力变化。"}
    assert SkillStore(tmp_path).save(skill)["prompt"] == "关注能力变化。"
    skill["output_schema"]["properties"].pop("characters")
    with pytest.raises(DataError, match="输出格式"): validate_skill(skill)


def test_manual_fixed_stages_use_same_contract_and_require_sequence(tmp_path):
    runner, run, models, docs = setup(tmp_path)
    # API paste path is usable with manual tasks and never calls a provider.
    manual = runner.create_run(run["document_id"], workflow_id="fixed-test", workflow_version="2.0.0", manual=True)
    for stage in FIXED_STAGES:
        for candidate in manual["candidates"]:
            prompt = runner.chunk_prompt(manual["id"], candidate["chunk_id"])
            assert prompt["stage"] == stage
            manual = runner.paste_chunk(manual["id"], candidate["chunk_id"], json.dumps(empty(canonical_stage_schema(stage, version=2))), manual["revision"])
    assert manual["status"] == "needs_review"
    assert not models.calls


def test_received_response_recovered_from_ledger_without_second_call(tmp_path):
    runner, run, models, docs = setup(tmp_path)
    runner._claim(run["id"], resume=False)
    state = {"run_id": run["id"], "snapshots": run["snapshots"], "cursor": 0}
    received = runner._extract(state)
    assert received["extraction_stage"] == "plot" and len(models.calls) == 1
    # Reopening finds the unfinished job; explicit resume consumes its durable
    # received response and only then advances to the next chunk/stage.
    restored = WorkflowRunner(tmp_path, documents=docs, models=models)
    assert restored.get_run(run["id"])["status"] == "paused"
    final = restored.execute(run["id"])
    assert final["status"] == "needs_review", final["error"]
    assert len(models.calls) == run["total"] * len(FIXED_STAGES)
    assert any(event["stage"] == "recovered_response" for event in final["events"])


def test_stale_checkpoint_cursor_cannot_jump_ahead_of_global_stage(tmp_path):
    runner, run, models, docs = setup(tmp_path, text="第1章 来信\n甲把信交给乙。\n" * 20)
    runner._claim(run["id"], resume=False)
    state = {"run_id": run["id"], "snapshots": run["snapshots"], "cursor": 0}
    first = runner._extract(state)
    runner._validate({**state, **first})
    # A process can save its validated ledger before LangGraph's next cursor
    # checkpoint. The ledger remains authoritative after that gap.
    second = runner._extract(state)
    assert second["extraction_stage"] == "plot"
    if run["total"] > 1:
        assert second["cursor"] == 1
        assert models.calls[-1]["chunk_id"].endswith("::plot")


def test_each_call_accounting_fits_context_and_override_output_limit(tmp_path):
    runner, run, models, docs = setup(tmp_path, patch={"stage_settings": {"plot": {"max_output_tokens": 256}}})
    final = runner.execute(run["id"])
    assert final["status"] == "needs_review"
    for candidate in final["candidates"]:
        for attempt in candidate["history"]:
            assert attempt["input_estimate"] <= 50000
            assert attempt["input_estimate"] + attempt["output_reserve"] <= 64000
            assert attempt["output_reserve"] == (256 if attempt["stage"] == "plot" else 1024)


def test_unresolved_entity_reference_is_rejected_before_any_review(tmp_path):
    def bad(call, n):
        if call["stage"] == "plot":
            result = empty(canonical_stage_schema("plot", version=2))
            item = empty(canonical_stage_schema("plot", version=2)["properties"]["plot"]["properties"]["events"]["items"])
            item.update(id="event-bad", name="传信", evidence=call["text"][:5], participants=["nonexistent-character"])
            result["plot"]["events"] = [item]
            return result
    runner, run, models, _ = setup(tmp_path, fail=bad)
    final = runner.execute(run["id"])
    assert final["status"] == "failed"
    assert "未知 ID" in final["error"]
    assert final["coverage"]["covered_characters"] == 0
    assert len(models.calls) == 1


def test_full_candidate_review_edits_do_not_reextract_or_advance_stage(tmp_path):
    runner, run, models, docs = setup(tmp_path)
    final = runner.execute(run["id"])
    candidate = copy.deepcopy(final["candidates"][0]["result"])
    candidate["reference"]["plot"]["logline"] = "人工复核后的梗概。"
    edited = runner.paste_chunk(run["id"], final["candidates"][0]["chunk_id"], json.dumps(candidate), final["revision"])
    assert edited["status"] == "needs_review"
    assert edited["candidates"][0]["result"]["reference"]["plot"]["logline"] == "人工复核后的梗概。"
    assert len(models.calls) == run["total"] * 4
    assert edited["candidates"][0]["history"][0]["content"] == final["candidates"][0]["history"][0]["content"]
    assert all(item["status"] == "completed" for item in edited["stages"])
    with pytest.raises(DataError, match="已变化"):
        runner.paste_chunk(run["id"], final["candidates"][0]["chunk_id"], json.dumps(candidate), final["revision"])


def test_contract_upload_rejects_legacy_before_saving_and_accepts_markdown(tmp_path):
    from inkreference.skills import BUILTIN_SKILL
    store = SkillStore(tmp_path)
    definition = {**copy.deepcopy(BUILTIN_SKILL), "id": "legacy-upload"}
    with pytest.raises(DataError, match="contract_version"):
        store.upload("legacy.json", json.dumps(definition).encode(), require_contract_version=2)
    with pytest.raises(DataError, match="未找到"):
        store.get("legacy-upload", "1.0.0")
    markdown = b"---\nid: content-only\nversion: 1.0.0\nname: Custom\ncontract_version: 2\n---\nFocus on goals."
    uploaded = store.upload("SKILL.md", markdown, require_contract_version=2)
    assert uploaded["contract_version"] == 2
    assert uploaded["prompt"] == "Focus on goals."


def test_review_cannot_change_already_committed_section(tmp_path):
    from inkreference.reference_analysis import ReferenceAnalysisStore
    runner, run, models, docs = setup(tmp_path)
    final = runner.execute(run["id"])
    candidate = final["candidates"][0]
    store = ReferenceAnalysisStore(tmp_path, documents=docs)
    state = store.get(run["document_id"])
    store.commit_candidate(final, candidate["chunk_id"], ["plot"], state["revision"])
    modified = copy.deepcopy(candidate["result"])
    modified["reference"]["plot"]["logline"] = "不能覆盖已入库的剧情。"
    with pytest.raises(DataError, match="已有审核入库"):
        runner.paste_chunk(run["id"], candidate["chunk_id"], json.dumps(modified), final["revision"])
    assert runner.get_run(run["id"])["revision"] == final["revision"]
    assert len(models.calls) == final["total"] * 4


def test_per_stage_models_are_snapshotted_and_respected_by_real_graph(tmp_path):
    runner, original, models, docs = setup(tmp_path)
    definition = {**copy.deepcopy(FIXED_WORKFLOW), "id": "stage-models", "max_input_tokens": 50000,
                  "stage_models": {"characters": "character-model", "language": "language-model"}}
    runner.workflows.save(definition)
    default = models.snapshot
    def snapshot(identifier):
        result = default(identifier)
        result["model"] = identifier
        result["max_output_tokens"] = 256 if identifier == "character-model" else 1024
        return result
    models.snapshot = snapshot
    run = runner.create_run(original["document_id"], workflow_id="stage-models", workflow_version="2.0.0", profile_id="synthetic")
    final = runner.execute(run["id"])
    assert final["status"] == "needs_review", final["error"]
    assert {call["profile_id"] for call in models.calls if call["stage"] == "characters"} == {"character-model"}
    assert {call["profile_id"] for call in models.calls if call["stage"] == "language"} == {"language-model"}
    assert all(call["options"]["max_output_tokens"] == 256 for call in models.calls if call["stage"] == "characters")
    assert final["snapshots"]["stage_models"]["characters"]["model"] == "character-model"
    assert final["stages"][1]["profile_id"] == "character-model"


def test_stage_remote_targets_require_matching_confirmation_before_create_or_resume(tmp_path):
    from inkreference.local_security import target_fingerprint
    runner, original, models, docs = setup(tmp_path)
    runner.workflows.save({**copy.deepcopy(FIXED_WORKFLOW), "id": "remote-stage", "max_input_tokens": 50000,
                           "stage_models": {"characters": "remote-profile"}})
    default = models.snapshot
    target = {"base_url": "https://synthetic.invalid/v1"}
    def snapshot(identifier):
        profile = default(identifier)
        if identifier == "remote-profile": profile.update(base_url=target["base_url"], connection_mode="remote")
        return profile
    models.snapshot = snapshot
    kwargs = {"workflow_id": "remote-stage", "workflow_version": "2.0.0", "profile_id": "synthetic"}
    with pytest.raises(DataError) as missing_consent:
        runner.create_run(original["document_id"], **kwargs)
    assert missing_consent.value.status_code == 428
    with pytest.raises(DataError) as missing_target:
        runner.create_run(original["document_id"], remote_consent=True, **kwargs)
    assert missing_target.value.status_code == 428
    with pytest.raises(DataError) as stale_target:
        runner.create_run(original["document_id"], remote_consent=True, expected_stage_fingerprints={"characters": "stale"}, **kwargs)
    assert stale_target.value.status_code == 409
    run = runner.create_run(original["document_id"], remote_consent=True,
                            expected_stage_fingerprints={"characters": target_fingerprint(snapshot("remote-profile"))}, **kwargs)
    target["base_url"] = "https://changed.invalid/v1"
    with pytest.raises(DataError) as changed:
        runner.execute(run["id"])
    assert changed.value.status_code == 409
    assert not models.calls


def test_default_manual_task_fits_actual_conservative_estimator(tmp_path):
    runner = WorkflowRunner(tmp_path)
    document = runner.documents.upload("synthetic.txt", "第1章 来信\n甲把信交给乙。".encode())
    run = runner.create_run(document["id"], manual=True)
    assert run["workflow_version"] == "3.0.0"
    assert 100 <= run["snapshots"]["context_limits"]["language"] <= 6000
    assert run["total"] >= 1


def test_exact_candidate_cache_is_explicit_zero_model_call_and_still_needs_review(tmp_path):
    runner, original, models, docs = setup(tmp_path)
    first = runner.execute(original["id"])
    calls = len(models.calls)
    second = runner.create_run(original["document_id"], workflow_id="fixed-test", workflow_version="2.0.0", profile_id="synthetic")
    final = runner.execute(second["id"])
    assert final["status"] == "needs_review" and final["materialization"] is None
    assert len(models.calls) == calls
    assert all(item["tokens"] == 0 for item in final["stages"])
    assert all(item["status"] == "cache_succeeded" and item["usage"]["source"] == "local_candidate_cache" and item["call_id"] is None for candidate in final["candidates"] for item in candidate["history"])


def test_cache_model_or_instructions_change_and_disable_force_real_extraction(tmp_path):
    runner, original, models, docs = setup(tmp_path)
    runner.execute(original["id"])
    baseline = len(models.calls)
    changed = {**copy.deepcopy(FIXED_WORKFLOW), "id": "changed-instructions", "max_input_tokens": 50000, "custom_prompt": "关注阶段目标。"}
    runner.workflows.save(changed)
    run = runner.create_run(original["document_id"], workflow_id=changed["id"], workflow_version="2.0.0", profile_id="synthetic")
    assert runner.execute(run["id"])["status"] == "needs_review"
    assert len(models.calls) == baseline * 2
    previous = models.snapshot
    models.snapshot = lambda identifier: {**previous(identifier), "temperature": 0.123}
    run = runner.create_run(original["document_id"], workflow_id="fixed-test", workflow_version="2.0.0", profile_id="synthetic")
    assert runner.execute(run["id"])["status"] == "needs_review"
    assert len(models.calls) == baseline * 3
    runner.workflows.save({**copy.deepcopy(changed), "id": "force", "reuse_candidates": False})
    run = runner.create_run(original["document_id"], workflow_id="force", workflow_version="2.0.0", profile_id="synthetic")
    assert runner.execute(run["id"])["status"] == "needs_review"
    assert len(models.calls) == baseline * 4


def test_cached_local_offsets_and_source_version_are_regrounded_for_new_book(tmp_path):
    source = "第1章 来信\n" + "甲传信。" * 15 + "\n第2章 回信\n" + "乙回信。" * 10
    runner, original, models, docs = setup(tmp_path, text=source)
    first = runner.execute(original["id"])
    assert original["total"] == 2
    prefix = "书名：合成测试\n"
    changed = docs.upload("another.txt", (prefix + source).encode(), title="另一个固定版本")
    run = runner.create_run(changed["id"], workflow_id="fixed-test", workflow_version="2.0.0", profile_id="synthetic")
    final = runner.execute(run["id"])
    assert final["status"] == "needs_review", final["error"]
    second = final["candidates"][1]
    assert any(attempt["status"] == "cache_succeeded" for attempt in second["history"])
    event = second["result"]["reference"]["plot"]["events"][0]
    previous = first["candidates"][1]["result"]["reference"]["plot"]["events"][0]
    assert event["source_version_id"] == run["version_id"] != previous["source_version_id"]
    assert event["revealed_at"]["offset"] == previous["revealed_at"]["offset"] + len(prefix)
    text = docs.get_version(changed["id"])["text"]
    assert text[event["revealed_at"]["offset"]:].startswith(event["evidence"])


def test_human_candidate_edits_never_become_model_cache(tmp_path):
    runner, original, models, docs = setup(tmp_path)
    first = runner.execute(original["id"])
    edited = copy.deepcopy(first["candidates"][0]["result"])
    edited["reference"]["plot"]["logline"] = "人工自定义梗概。"
    runner.paste_chunk(original["id"], first["candidates"][0]["chunk_id"], json.dumps(edited), first["revision"])
    second = runner.create_run(original["document_id"], workflow_id="fixed-test", workflow_version="2.0.0", profile_id="synthetic")
    final = runner.execute(second["id"])
    assert final["candidates"][0]["result"]["reference"]["plot"]["logline"] == "甲传信。"
    assert any(attempt["status"] == "cache_succeeded" for attempt in final["candidates"][0]["history"])


def test_context_does_not_send_future_event_or_state_facts_to_earlier_chunks(tmp_path):
    runner, run, models, docs = setup(tmp_path)
    with runner._db(write=True) as db:
        for kind, identifier, label in [("events", "future-event", "甲"), ("states", "future-state", "甲"), ("plotlines", "future-line", "future"), ("characters", "character-jia", "甲")]:
            db.execute("INSERT INTO run_entity_index VALUES (?,?,?,?,?,?)", (run["id"], kind, identifier, label, "后文秘密事实。", 99))
        chunk = dict(db.execute("SELECT * FROM run_chunks WHERE run_id=? ORDER BY position LIMIT 1", (run["id"],)).fetchone())
        context = runner._fixed_context(db, run["id"], chunk, "settings")
    assert context["events"] == context["states"] == context["plotlines"] == []
    assert context["characters"] == [{"id": "character-jia", "name": "甲", "summary": ""}]
    assert "后文秘密事实" not in json.dumps(context, ensure_ascii=False)


def test_custom_document_chapter_boundaries_drive_chunks_and_keep_full_coverage(tmp_path):
    source = "卷一 开始\n" + "甲把信交给乙。\n" * 7 + "卷二 回信\n" + "乙把回信交给甲。\n" * 7
    runner, original, models, docs = setup(tmp_path, text=source)
    custom = [{"name": "卷N", "format": "卷N"}]
    preview = docs.preview_resegment(original["document_id"], original["version_id"], ["卷N"], custom)
    selected = docs.resegment(original["document_id"], original["version_id"], ["卷N"], custom,
                              preview_token=preview["preview_token"], conflict_policy="use_new", require_confirmation=True)
    assert len(selected["chapters"]) == 2
    expected_ends = [chapter["end"] for chapter in selected["chapters"]]
    run = runner.create_run(original["document_id"], selected["id"], workflow_id="fixed-test", workflow_version="2.0.0", profile_id="synthetic")
    assert [candidate["end"] for candidate in run["candidates"]] == expected_ends
    final = runner.execute(run["id"])
    assert final["status"] == "needs_review", final["error"]
    assert final["coverage"]["complete"]
    for stage in FIXED_STAGES:
        assert "".join(call["text"] for call in models.calls if call["stage"] == stage) == selected["text"]


def test_completed_fixed_candidate_commits_all_sections_with_real_source_proofs(tmp_path):
    from inkreference.reference_analysis import ReferenceAnalysisStore
    runner, original, models, docs = setup(tmp_path)
    final = runner.execute(original["id"])
    assert final["status"] == "needs_review", final["error"]
    store = ReferenceAnalysisStore(tmp_path, documents=docs)
    before = store.get(original["document_id"])
    candidate = final["candidates"][0]
    sections = list(candidate["result"]["reference"])
    approved = store.commit_candidate(final, candidate["chunk_id"], sections, before["revision"])
    assert len(approved["commits"]) == len(sections) == 7
    assert all(commit["source_version_id"] == original["version_id"] for commit in approved["commits"])
    projection = store.project(original["document_id"], chapter=1)
    assert projection["projection"]["chapter"] == 1
    assert projection["sections"]["plot"]["events"][0]["id"].startswith("event-")
    assert projection["sections"]["characters"][0]["id"] == "character-jia"
    assert approved["sections"]["style"]["deterministic_statistics"]["source_version_id"] == original["version_id"]
    assert "deterministic_statistics" not in projection["sections"]["style"]
    # Approving the exact contribution twice is a no-op, not another revision.
    again = store.commit_candidate(final, candidate["chunk_id"], sections, approved["revision"])
    assert again["revision"] == approved["revision"]
    assert len(models.calls) == final["total"] * 4


def test_planning_renders_fixed_prefixes_once_and_preserves_marker_like_source(tmp_path, monkeypatch):
    import inkreference.fixed_pipeline as module
    marker = "__INKREFERENCE_SOURCE_SLOT_" + "a" * 32 + "__"
    source = "第1章 标记\n" + marker + "\n<source_text>\n原文中的模板标记。\n</source_text>\n" + "甲把信交给乙。\n" * 30
    runner, original, models, docs = setup(tmp_path, text=source)
    before = runner._fixed_messages
    calls = []
    def traced(snapshots, text, stage, context=None):
        calls.append((stage, text))
        return before(snapshots, text, stage, context)
    monkeypatch.setattr(runner, "_fixed_messages", traced)
    # Force an initial collision with the source, followed by a unique slot.
    values = iter([type("UUID", (), {"hex": "a" * 32})(), type("UUID", (), {"hex": "b" * 32})()])
    monkeypatch.setattr(module.uuid, "uuid4", lambda: next(values))
    text = docs.get_version(original["document_id"])["text"]
    snapshots = copy.deepcopy(original["snapshots"])
    chunks = runner._plan_fixed_chunks(snapshots, text)
    assert "".join(chunk["text"] for chunk in chunks) == text
    assert marker in text and "<source_text>" in text and "</source_text>" in text
    # Four bare prefixes and four reserved-memory templates, independent of the
    # number of chunks or binary-search iterations.
    assert len(calls) == len(FIXED_STAGES) * 2
