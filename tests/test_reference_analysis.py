"""Reference-tab parity: real stores/graphs, synthetic model boundary only."""
import copy
import json

import pytest

from inkreference.database import DataError
from inkreference.documents import DocumentStore
from inkreference.reference_analysis import ReferenceAnalysisStore, compute_nlp_style, validate_reference
from inkreference.local_security import target_fingerprint
from inkreference.skills import BUILTIN_SKILL, RICH_BUILTIN_SKILL, SkillStore, validate_result
from inkreference.workflow_definitions import DEFAULT_WORKFLOW, REFERENCE_WORKFLOW, WorkflowStore
from inkreference.workflows import WorkflowRunner


SOURCE = "第一章 旧城\n林青走进旧城。城门只有一把钥匙。\n" * 10


class SyntheticModels:
    def __init__(self):
        self.calls = []

    def snapshot(self, profile_id):
        return {"id": profile_id, "provider": "synthetic", "model": "test", "context_window": 50000, "max_output_tokens": 256}

    @staticmethod
    def estimate_messages(messages):
        return {"input_tokens": sum(len(message["content"].encode("utf-8")) for message in messages)}

    def invoke(self, profile_id, messages, **kwargs):
        self.calls.append((profile_id, messages, kwargs))
        text = messages[-1]["content"].split("<source_text>\n", 1)[1].rsplit("\n</source_text>", 1)[0]
        quote = text[:12]
        result = {"summary": "合成测试摘要", "characters": [], "events": [], "cliches": [], "reference": {
            "characters": [{"name": "林青", "intro": "合成测试人物", "evidence": quote}],
            "settings": [{"title": "旧城", "category": "geography", "content": "合成测试设定", "evidence": quote}],
            "style": {"dialogue_ratio": .2, "chapter_signals": [{"chapter": "第1章", "info_density": .8, "chapter_types": ["高潮"], "hooks": [], "payoffs": []}]}}}
        return {"content": json.dumps(result, ensure_ascii=False), "call_id": "synthetic-call", "usage": {"input_tokens": 10, "output_tokens": 20, "source": "synthetic"}}


def make_runner(tmp_path, *, manual=False):
    documents = DocumentStore(tmp_path)
    doc = documents.upload("synthetic.txt", SOURCE.encode(), title="合成参考作品")
    workflows = WorkflowStore(tmp_path)
    workflow = {**REFERENCE_WORKFLOW, "id": "reference-test", "chunk_chars": 100, "max_output_tokens": 256}
    workflows.save(workflow)
    models = SyntheticModels()
    runner = WorkflowRunner(tmp_path, documents=documents, models=models, workflows=workflows)
    run = runner.create_run(doc["id"], workflow_id="reference-test", workflow_version="1.1.0", profile_id=None if manual else "synthetic", manual=manual)
    return runner, run, models, doc, documents


def test_rich_versions_do_not_redefine_legacy_builtin_identity(tmp_path):
    skills, workflows = SkillStore(tmp_path), WorkflowStore(tmp_path)
    assert skills.get()["version"] == workflows.get()["version"] == "2.0.0"
    assert skills.get("unified-reference", "1.1.0")["output_schema"] == RICH_BUILTIN_SKILL["output_schema"]
    assert skills.get("unified-reference", "1.0.0") == {**BUILTIN_SKILL, "builtin": True}
    assert workflows.get("default", "1.0.0") == {**DEFAULT_WORKFLOW, "builtin": True}
    assert "reference" not in BUILTIN_SKILL["output_schema"]["properties"]
    assert "reference" in RICH_BUILTIN_SKILL["output_schema"]["properties"]
    with pytest.raises(DataError, match="内建版本不可修改"):
        skills.save({**BUILTIN_SKILL, "prompt": "覆盖"})


def test_model_rich_facts_need_exact_quotes_but_manual_work_edit_can_be_unquoted(tmp_path):
    result = {"summary": "摘要", "characters": [], "events": [], "cliches": [], "reference": {"settings": [{"title": "旧城", "content": "城中设定"}]}}
    with pytest.raises(DataError, match="evidence"):
        validate_result(RICH_BUILTIN_SKILL, result, SOURCE)
    result["reference"]["settings"][0]["evidence"] = "原文中没有"
    with pytest.raises(DataError, match="连续片段"):
        validate_result(RICH_BUILTIN_SKILL, result, SOURCE)
    store = ReferenceAnalysisStore(tmp_path)
    saved = store.save_section("manual-work", "settings", [{"title": "人工笔记", "content": "读者整理"}], 0)
    assert saved["sections"]["settings"][0]["title"] == "人工笔记"


@pytest.mark.parametrize("style", [{"chapter_signals": "bad"}, {"chapter_signals": [{"hooks": 4}]}, {"chapter_signals": [{"info_density": 2}]}, {"dialogue_ratio": True}])
def test_invalid_style_signals_fail_before_aggregation(style):
    with pytest.raises(DataError):
        validate_reference({"style": style})


def test_single_chunk_real_langgraph_does_not_run_other_chunks(tmp_path):
    runner, run, models, doc, documents = make_runner(tmp_path)
    assert run["total"] > 1
    runner.start_chunk(run["id"], run["candidates"][1]["chunk_id"])
    partial = runner.wait(run["id"])
    assert partial["status"] == "paused"
    assert partial["completed"] == len(models.calls) == 1
    candidate = partial["candidates"][1]
    assert candidate["status"] == "completed"
    assert candidate["result"]["reference"]["style"]["avg_sentence_length"] >= 0
    assert candidate["history"][0]["status"] == "succeeded"
    store = ReferenceAnalysisStore(tmp_path, documents=documents)
    approved = store.commit_candidate(partial, candidate["chunk_id"], ["characters", "settings", "style"], 0)
    assert len(approved["sections"]["characters"]) == len(approved["sections"]["settings"]) == 1
    assert approved["sections"]["rhythm"]["pacing_segments"][0]["pacing"] == "fast"
    assert {entry["source_version_id"] for entry in approved["commits"]} == {run["version_id"]}
    assert store.commit_candidate(partial, candidate["chunk_id"], ["characters", "settings", "style"], 0) == approved
    changed = copy.deepcopy(candidate["result"])
    changed["reference"]["characters"][0]["intro"] = "改动已确认分区"
    with pytest.raises(DataError, match="已有审核"):
        runner.paste_chunk(run["id"], candidate["chunk_id"], json.dumps(changed), partial["revision"])


def test_manual_web_workflow_needs_no_profile_and_partial_paste_is_durable(tmp_path):
    runner, run, models, doc, documents = make_runner(tmp_path, manual=True)
    first = run["candidates"][0]["chunk_id"]
    prompt = runner.chunk_prompt(run["id"], first)
    assert prompt["text"] == SOURCE[run["candidates"][0]["start"]:run["candidates"][0]["end"]]
    assert prompt["text"] in prompt["prompt"]
    with pytest.raises(DataError, match="网页粘贴"):
        runner.start_chunk(run["id"], first)
    native = {"events": [{"name": "读者概括", "description": "人工审核候选"}], "characters": [{"name": "林青", "intro": "读者笔记"}], "settings": [], "style": {}}
    pasted = runner.paste_chunk(run["id"], first, "```json\n" + json.dumps(native, ensure_ascii=False) + "\n```", run["revision"])
    assert pasted["status"] == "paused" and pasted["completed"] == 1
    assert pasted["results"]["characters"] == []  # No ungrounded v8 facts.
    assert pasted["candidates"][0]["result"]["reference"]["characters"][0]["name"] == "林青"
    assert models.calls == []
    reopened = WorkflowRunner(tmp_path, documents=documents, models=models)
    assert reopened.get_run(run["id"])["completed"] == 1


def test_independent_section_approval_keeps_other_sections_editable(tmp_path):
    runner, run, models, doc, documents = make_runner(tmp_path, manual=True)
    chunk_id = run["candidates"][0]["chunk_id"]
    native = {"characters": [{"name": "林青", "intro": "读者笔记"}], "settings": [{"title": "旧城", "content": "原始设定"}]}
    pasted = runner.paste_chunk(run["id"], chunk_id, json.dumps(native), run["revision"])
    store = ReferenceAnalysisStore(tmp_path, documents=documents)
    approved = store.commit_candidate(pasted, chunk_id, ["characters"], 0)
    amended = copy.deepcopy(pasted["candidates"][0]["result"])
    amended["reference"]["settings"][0]["content"] = "已人工修订的设定"
    next_run = runner.paste_chunk(run["id"], chunk_id, json.dumps(amended), pasted["revision"])
    state = store.commit_candidate(next_run, chunk_id, ["settings"], approved["revision"])
    assert state["sections"]["characters"][0]["intro"] == "读者笔记"
    assert state["sections"]["settings"][0]["content"] == "已人工修订的设定"


def test_reference_transform_prompt_only_uses_current_saved_data_without_model(tmp_path):
    runner, run, models, doc, documents = make_runner(tmp_path)
    store = ReferenceAnalysisStore(tmp_path, documents=documents)
    saved = store.save_section(doc["id"], "raw_entries", [{"title": "原文", "content": "Hello\nworld"}], 0)
    source = store.build_source(doc["id"], saved["revision"])
    assert "Hello\nworld" in source["text"]
    preview = runner.reference_transform(doc["id"], "translate", None, {"entry_index": 0, "target_language": "中文", "prompt_only": True})
    assert preview["source"] == "Hello\nworld" and models.calls == []
    with pytest.raises(DataError, match="已变化"):
        store.build_source(doc["id"], 0)
    # An explicit preview text does not index an unrelated/invalid entry.
    preview = runner.reference_transform(doc["id"], "translate", None, {"entry_index": 999, "text": "Explicit", "prompt_only": True})
    assert preview["source"] == "Explicit"


def test_transform_real_model_boundary_is_explicit_target_pinned_and_preview_only(tmp_path):
    runner, run, models, doc, documents = make_runner(tmp_path)
    remote = {"id": "remote", "provider": "synthetic", "model": "test", "connection_mode": "remote", "base_url": "https://example.test/v1", "context_window": 50000, "max_output_tokens": 256}
    models.snapshot = lambda profile_id: copy.deepcopy(remote)
    store = ReferenceAnalysisStore(tmp_path, documents=documents)
    before = store.get(doc["id"])
    payload = {"text": "Hello", "target_language": "中文"}
    with pytest.raises(DataError) as absent:
        runner.reference_transform(doc["id"], "translate", "remote", payload)
    assert absent.value.status_code == 428 and models.calls == []
    with pytest.raises(DataError) as stale:
        runner.reference_transform(doc["id"], "translate", "remote", payload, remote_consent=True, expected_model_fingerprint="old-target")
    assert stale.value.status_code == 409 and models.calls == []
    preview = runner.reference_transform(doc["id"], "translate", "remote", payload, remote_consent=True, expected_model_fingerprint=target_fingerprint(remote))
    assert preview["call_id"] == "synthetic-call" and preview["usage"]["source"] == "synthetic"
    assert len(models.calls) == 1 and models.calls[0][2]["profile_snapshot"] == remote
    assert store.get(doc["id"]) == before  # Apply still requires an explicit edit.


def test_deterministic_nlp_metrics_are_source_based():
    short = compute_nlp_style("你好！你好？……\n")
    assert short["punctuation_profile"] == {"ellipsis": 1, "dash": 0, "exclamation": 1, "question": 1, "comma": 0}
    assert short["avg_sentence_length"] == 2
    assert "dialogue_ratio" not in short
