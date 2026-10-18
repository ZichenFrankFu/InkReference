"""Real LangGraph + SQLite checkpoints; only the model boundary is mocked."""

from __future__ import annotations

import copy
import json
import pickle
import sqlite3
import threading
from pathlib import Path

import pytest

from inkreference.database import DataError
from inkreference.documents import DocumentStore
from inkreference.skills import SkillStore
from inkreference.workflow_definitions import DEFAULT_WORKFLOW, WorkflowStore
from inkreference.workflows import WorkflowRunner
from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer
from langgraph.checkpoint.sqlite import SqliteSaver


TEXT = "林甲抬起头，看见旧友站在门边。旧友把一封未拆的信交给他。🙂\n" * 8


class MockModels:
    """The test response is visibly synthetic; no network or real LLM is used."""
    def __init__(self, handler=None):
        self.handler = handler
        self.calls = []
        self.profile = {"id": "mock-profile", "provider": "test", "model": "synthetic-test-model", "context_window": 20000, "max_output_tokens": 128}

    def snapshot(self, profile_id):
        assert profile_id == "mock-profile"
        return copy.deepcopy(self.profile)

    @staticmethod
    def estimate_messages(messages):
        return {"input_tokens": sum(len(message["content"].encode("utf-8")) for message in messages) + 32, "source": "estimated"}

    def invoke(self, profile_id, messages, **kwargs):
        text = messages[-1]["content"].split("<source_text>\n", 1)[1].rsplit("\n</source_text>", 1)[0]
        call = {"profile_id": profile_id, "text": text, **kwargs}
        self.calls.append(call)
        result = self.handler(call, len(self.calls)) if self.handler else valid_result(text)
        if isinstance(result, Exception):
            raise result
        content = result if isinstance(result, str) else json.dumps(result, ensure_ascii=False)
        return {"content": content, "call_id": f"synthetic-{len(self.calls)}", "usage": {"input_tokens": 12, "output_tokens": 8, "source": "test"}}


def valid_result(text):
    quote = text[:min(15, len(text))]
    item = {"name": "合成测试人物", "description": "合成测试描述", "evidence": quote}
    return {"summary": "合成测试摘要", "characters": [dict(item)], "events": [dict(item)], "cliches": [dict(item)]}


def setup_runner(tmp_path, *, text=TEXT, handler=None, retry_limit=0, budget=100000, chunk_chars=100, materializer=None):
    documents = DocumentStore(tmp_path)
    document = documents.upload("synthetic.txt", text.encode("utf-8"), title="合成作品")
    skills, workflows = SkillStore(tmp_path), WorkflowStore(tmp_path)
    definition = {**copy.deepcopy(DEFAULT_WORKFLOW), "id": "test", "chunk_chars": chunk_chars, "retry_limit": retry_limit, "max_output_tokens": 128, "budget_tokens": budget}
    workflows.save(definition)
    models = MockModels(handler)
    runner = WorkflowRunner(tmp_path, documents=documents, models=models, skills=skills, workflows=workflows, materializer=materializer)
    run = runner.create_run(document["id"], document["latest_version_id"], "test", "1.0.0", "mock-profile")
    return runner, run, models, documents, workflows


def test_background_real_graph_covers_whole_unicode_source_and_waits_for_review(tmp_path):
    runner, run, models, documents, workflows = setup_runner(tmp_path)
    assert run["status"] == "queued" and models.calls == []
    assert run["total"] > 1
    runner.start(run["id"])
    completed = runner.wait(run["id"])
    assert completed["status"] == "needs_review", completed["error"]
    assert completed["coverage"] == {"total_characters": len(TEXT), "covered_characters": len(TEXT), "ratio": 1.0, "complete": True, "missing": []}
    assert completed["progress"] == 1.0
    assert "".join(call["text"] for call in models.calls) == TEXT
    for kind in ("characters", "events", "cliches"):
        for item in completed["results"][kind]:
            assert TEXT[item["start"]:item["end"]] == item["evidence"] == item["quote"]
    assert all(call["profile_snapshot"]["model"] == "synthetic-test-model" for call in models.calls)
    with sqlite3.connect(tmp_path / "checkpoints.sqlite3") as db:
        assert db.execute("SELECT COUNT(*) FROM checkpoints").fetchone()[0] > run["total"]
    with pytest.raises(DataError):
        runner.approve(run["id"], completed["revision"])


def test_provider_and_invalid_quote_retry_attempts_are_visible(tmp_path):
    def handler(call, number):
        if number == 1:
            return DataError(502, "合成提供商错误")
        result = valid_result(call["text"])
        if number == 2:
            result["characters"][0]["evidence"] = "原文中没有的证据"
        return result
    runner, run, models, *_ = setup_runner(tmp_path, text="只有一块短文本，用于检验证据与重试。", handler=handler, retry_limit=2)
    final = runner.execute(run["id"])
    assert final["status"] == "needs_review", final["error"]
    assert len(models.calls) == 3
    candidate = final["candidates"][0]
    assert candidate["attempts"] == 3
    assert [item["status"] for item in candidate["history"]] == ["provider_error", "validation_error", "succeeded"]
    assert "evidence" in candidate["history"][1]["error"]


@pytest.mark.parametrize("bad", ["```json\n{}\n```", "{}", '{"summary":"示例","characters":[],"events":[],"cliches":[{"name":"桥段","description":"描述","evidence":"虚构引用"}]}'])
def test_invalid_outputs_fail_whole_chunk_without_silent_partial_import(tmp_path, bad):
    runner, run, models, *_ = setup_runner(tmp_path, text="用于单块失败测试的合成原文。", handler=lambda *_: bad)
    failed = runner.execute(run["id"])
    assert failed["status"] == "failed"
    assert failed["completed"] == 0 and failed["coverage"]["ratio"] == 0
    assert failed["results"]["characters"] == []
    assert failed["candidates"][0]["error"]
    assert len(models.calls) == 1
    with pytest.raises(DataError):
        runner.approve(run["id"], failed["revision"])


def test_resume_skips_successful_chunks_and_keeps_snapshots(tmp_path):
    failed_once = set()
    def handler(call, number):
        if call["chunk_id"] == "chunk-000002" and call["chunk_id"] not in failed_once:
            failed_once.add(call["chunk_id"])
            return DataError(502, "第二块仅失败一次")
        return valid_result(call["text"])
    runner, run, models, documents, workflows = setup_runner(tmp_path, handler=handler)
    failed = runner.execute(run["id"])
    assert failed["status"] == "failed" and failed["completed"] == 1
    old_calls = len(models.calls)
    models.profile["model"] = "后来修改的模型"
    workflows.save({**copy.deepcopy(DEFAULT_WORKFLOW), "id": "test", "version": "2.0.0", "chunk_chars": 200})
    restarted = WorkflowRunner(tmp_path, documents=documents, models=models, workflows=workflows)
    assert len(models.calls) == old_calls  # constructing/reopening sends no API calls
    final = restarted.execute(run["id"])
    assert final["status"] == "needs_review", final["error"]
    assert [call["chunk_id"] for call in models.calls].count("chunk-000001") == 1
    assert all(call["profile_snapshot"]["model"] == "synthetic-test-model" for call in models.calls)
    assert final["snapshots"]["workflow"]["version"] == "1.0.0"


def test_cancellation_waits_for_inflight_response_then_resumes_without_repeating_success(tmp_path):
    entered, release = threading.Event(), threading.Event()
    def handler(call, number):
        if number == 1:
            entered.set()
            assert release.wait(5)
        return valid_result(call["text"])
    runner, run, models, *_ = setup_runner(tmp_path, handler=handler)
    runner.start(run["id"])
    assert entered.wait(5)
    with pytest.raises(DataError):
        runner.start(run["id"])
    assert runner.cancel(run["id"])["status"] == "cancelling"
    release.set()
    cancelled = runner.wait(run["id"])
    assert cancelled["status"] == "cancelled"
    assert cancelled["completed"] == 1 and len(models.calls) == 1
    runner.resume(run["id"])
    final = runner.wait(run["id"])
    assert final["status"] == "needs_review", final["error"]
    assert [call["chunk_id"] for call in models.calls].count("chunk-000001") == 1


def test_restart_marks_interrupted_as_paused_and_does_not_call_model(tmp_path):
    runner, run, models, documents, workflows = setup_runner(tmp_path)
    with runner._db(write=True) as db:
        db.execute("UPDATE run_jobs SET status='running',stage='extracting' WHERE id=?", (run["id"],))
    restarted = WorkflowRunner(tmp_path, documents=documents, models=models, workflows=workflows)
    assert restarted.get_run(run["id"])["status"] == "paused"
    assert models.calls == []
    final = restarted.execute(run["id"])
    assert final["status"] == "needs_review"


def test_database_success_before_checkpoint_failure_is_not_called_twice(tmp_path, monkeypatch):
    runner, run, models, documents, workflows = setup_runner(tmp_path, text="第一块成功但在checkpoint写入前进程中断。")
    original = runner._validate
    def crash_after_commit(state):
        original(state)
        raise RuntimeError("模拟保存checkpoint之前进程中断")
    monkeypatch.setattr(runner, "_validate", crash_after_commit)
    interrupted = runner.execute(run["id"])
    assert interrupted["status"] == "failed" and interrupted["completed"] == 1
    assert len(models.calls) == 1
    restarted = WorkflowRunner(tmp_path, documents=documents, models=models, workflows=workflows)
    final = restarted.execute(run["id"])
    assert final["status"] == "needs_review", final["error"]
    assert len(models.calls) == 1


def test_budget_prevents_request_and_keeps_usage_distinct_from_estimate(tmp_path):
    runner, run, models, *_ = setup_runner(tmp_path, text="预算不足的短原文。", budget=1)
    failed = runner.execute(run["id"])
    assert failed["status"] == "failed" and failed["stage"] == "budget"
    assert "预算" in failed["error"]
    assert models.calls == []
    assert failed["candidates"][0]["attempts"] == 0


def test_manual_review_protects_evidence_revisions_and_idempotent_approval(tmp_path):
    materializations = []
    def materializer(run):
        materializations.append(copy.deepcopy(run))
        return {"work_id": "NEW-WORK", "run_id": run["id"]}
    runner, run, models, *_ = setup_runner(tmp_path, text="林甲把旧信递给林乙，随后离开码头。", materializer=materializer)
    ready = runner.execute(run["id"])
    edited = copy.deepcopy(ready["results"])
    edited["summary"] = "人工修订摘要"
    edited["characters"][0]["description"] = "人工修订且保留引用"
    saved = runner.edit_result(run["id"], edited, ready["revision"])
    assert saved["results"]["summary"] == "人工修订摘要"
    with pytest.raises(DataError) as rejected:
        runner.edit_result(run["id"], edited, ready["revision"])
    assert rejected.value.status_code == 409
    invalid = copy.deepcopy(saved["results"])
    invalid["events"][0]["start"] += 1
    with pytest.raises(DataError):
        runner.edit_result(run["id"], invalid, saved["revision"])
    with pytest.raises(DataError):
        runner.resume(run["id"])
    approved = runner.approve(run["id"], saved["revision"])
    assert approved["status"] == "approved"
    assert runner.approve(run["id"], saved["revision"])["status"] == "approved"
    assert len(materializations) == 1
    assert materializations[0]["results"]["summary"] == "人工修订摘要"
    assert len(models.calls) == 1
    assert approved["candidates"][0]["result"]["summary"] == "合成测试摘要"


def test_new_document_version_does_not_change_pinned_run(tmp_path):
    runner, run, models, documents, *_ = setup_runner(tmp_path, text="最初文本中的人物递出一封信。")
    documents.create_version(run["document_id"], run["version_id"], {}, manual_text="后来替换的新文本。")
    final = runner.execute(run["id"])
    assert final["status"] == "needs_review"
    assert models.calls[0]["text"] == "最初文本中的人物递出一封信。"
    assert final["version_id"] == run["version_id"]


def test_cancel_queued_job_never_calls_model_until_manual_resume(tmp_path):
    runner, run, models, *_ = setup_runner(tmp_path, text="等待用户明确操作。")
    assert runner.cancel(run["id"])["status"] == "cancelled"
    assert models.calls == []
    assert runner.execute(run["id"])["status"] == "needs_review"


def test_disabled_nodes_must_be_empty_not_silently_dropped(tmp_path):
    runner, run, models, documents, workflows = setup_runner(tmp_path, text="本次仅需要摘要。")
    workflow = {**copy.deepcopy(DEFAULT_WORKFLOW), "id": "summary-only", "nodes": {"summary": True, "characters": False, "events": False, "cliches": False}, "retry_limit": 0}
    workflows.save(workflow)
    run = runner.create_run(run["document_id"], run["version_id"], "summary-only", "1.0.0", "mock-profile")
    failed = runner.execute(run["id"])
    assert failed["status"] == "failed"
    assert "关闭" in failed["error"]


def test_model_context_splits_long_unicode_text_without_truncation(tmp_path):
    runner, _, models, documents, workflows = setup_runner(tmp_path, text=TEXT * 2, chunk_chars=6000)
    models.profile["context_window"] = 6500
    run = runner.create_run(documents.list_documents()[0]["id"], profile_id="mock-profile", workflow_id="test")
    final = runner.execute(run["id"])
    assert final["status"] == "needs_review", final["error"]
    assert "".join(call["text"] for call in models.calls) == TEXT * 2
    assert final["coverage"]["complete"]


def test_response_saved_before_graph_checkpoint_is_reused_without_another_request(tmp_path, monkeypatch):
    runner, run, models, documents, workflows = setup_runner(tmp_path, text="已返回的模型响应应当保存，不必重新付费。")
    extract = runner._extract
    def crash_after_response(state):
        extract(state)
        raise RuntimeError("模拟响应已保存但checkpoint尚未提交")
    monkeypatch.setattr(runner, "_extract", crash_after_response)
    failed = runner.execute(run["id"])
    assert failed["status"] == "failed" and failed["completed"] == 0
    assert failed["candidates"][0]["history"][0]["status"] == "received"
    restarted = WorkflowRunner(tmp_path, documents=documents, models=models, workflows=workflows)
    final = restarted.execute(run["id"])
    assert final["status"] == "needs_review", final["error"]
    assert len(models.calls) == 1
    assert any(event["stage"] == "recovered_response" for event in final["events"])


def test_configuration_errors_are_not_retried_as_paid_provider_failures(tmp_path):
    runner, run, models, *_ = setup_runner(tmp_path, text="密钥缺失属于配置问题。", handler=lambda *_: DataError(422, "密钥未配置"), retry_limit=8)
    failed = runner.execute(run["id"])
    assert failed["status"] == "failed"
    assert len(models.calls) == 1
    assert failed["candidates"][0]["history"][0]["status"] == "configuration_error"


def test_duplicate_json_fields_are_validation_failure(tmp_path):
    output = '{"summary":"first","summary":"second","characters":[],"events":[],"cliches":[]}'
    runner, run, models, *_ = setup_runner(tmp_path, text="重复JSON字段不应被静默覆盖。", handler=lambda *_: output)
    failed = runner.execute(run["id"])
    assert failed["status"] == "failed"
    assert "重复字段" in failed["error"]
    assert failed["candidates"][0]["history"][0]["status"] == "validation_error"


def test_checkpoint_snapshot_mismatch_never_sends_new_requests(tmp_path):
    runner, run, models, documents, workflows = setup_runner(tmp_path, text="固定模型配置不得被checkpoint替换。", handler=lambda *_: DataError(502, "稍后恢复"))
    assert runner.execute(run["id"])["status"] == "failed"
    with sqlite3.connect(tmp_path / "checkpoints.sqlite3", check_same_thread=False) as db:
        saver = SqliteSaver(db, serde=JsonPlusSerializer(pickle_fallback=False, allowed_json_modules=[], allowed_msgpack_modules=[]))
        graph = runner._graph(saver)
        config = {"configurable": {"thread_id": run["id"]}}
        saved = graph.get_state(config)
        changed = copy.deepcopy(saved.values["snapshots"])
        changed["model"]["model"] = "被篡改的模型"
        graph.update_state(config, {"snapshots": changed})
    restarted = WorkflowRunner(tmp_path, documents=documents, models=models, workflows=workflows)
    failed = restarted.execute(run["id"])
    assert failed["status"] == "failed"
    assert "检查点" in failed["error"] and len(models.calls) == 1


def test_chunk_plan_corruption_is_detected_before_model_request(tmp_path):
    runner, run, models, *_ = setup_runner(tmp_path, text="完整版本的正文不应该存在覆盖缺口。")
    with runner._db(write=True) as db:
        db.execute("UPDATE run_chunks SET start=1 WHERE run_id=?", (run["id"],))
    failed = runner.execute(run["id"])
    assert failed["status"] == "failed"
    assert "数据块" in failed["error"] and models.calls == []


def test_declared_custom_skill_fields_survive_in_chunk_candidates(tmp_path):
    from inkreference.skills import BUILTIN_SKILL
    runner, run, models, documents, workflows = setup_runner(tmp_path, text="自定义Skill的附加分析字段需要保留。")
    skill = copy.deepcopy(BUILTIN_SKILL)
    skill.update({"id": "custom", "name": "自定义测试"})
    skill["output_schema"]["properties"]["tone"] = {"type": "string"}
    skill["output_schema"]["required"].append("tone")
    runner.skills.save(skill)
    workflows.save({**copy.deepcopy(DEFAULT_WORKFLOW), "id": "custom", "skill_id": "custom", "retry_limit": 0})
    models.handler = lambda call, _: {**valid_result(call["text"]), "tone": "平静"}
    custom = runner.create_run(run["document_id"], run["version_id"], "custom", "1.0.0", "mock-profile")
    final = runner.execute(custom["id"])
    assert final["status"] == "needs_review", final["error"]
    assert final["candidates"][0]["result"]["tone"] == "平静"


def test_restored_pickle_checkpoint_is_rejected_without_executing_payload(tmp_path):
    runner, run, models, *_ = setup_runner(tmp_path, text="恢复项目时不执行checkpoint携带的对象。", handler=lambda *_: DataError(502, "暂停以备恢复"))
    assert runner.execute(run["id"])["status"] == "failed"
    sentinel = tmp_path / "must-not-exist.txt"
    class UnsafePayload:
        def __reduce__(self):
            return eval, (f"__import__('pathlib').Path({str(sentinel)!r}).write_text('executed')",)
    payload = pickle.dumps(UnsafePayload())
    with sqlite3.connect(tmp_path / "checkpoints.sqlite3") as db:
        db.execute("UPDATE checkpoints SET type='pickle',checkpoint=? WHERE thread_id=?", (payload, run["id"]))
    failed = runner.execute(run["id"])
    assert failed["status"] == "failed" and "pickle" in failed["error"]
    assert not sentinel.exists()
    assert len(models.calls) == 1


def test_shutdown_exposes_inflight_thread_and_prevents_later_model_calls(tmp_path):
    entered, release = threading.Event(), threading.Event()
    def handler(call, number):
        entered.set()
        assert release.wait(5)
        return valid_result(call["text"])
    runner, run, models, *_ = setup_runner(tmp_path, handler=handler)
    runner.start(run["id"])
    assert entered.wait(3)
    try:
        runner.shutdown(timeout=0)
        active = runner.active_threads()
        assert len(active) == 1 and active[0].is_alive()
        with pytest.raises(DataError) as rejected:
            runner.start(run["id"])
        assert rejected.value.status_code == 409
        with pytest.raises(DataError):
            runner.execute(run["id"])
    finally:
        release.set()
    final = runner.wait(run["id"])
    assert final["status"] == "paused"
    assert len(models.calls) == 1  # no request for the next chunk during shutdown
    assert runner.active_threads() == []
