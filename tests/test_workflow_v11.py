"""V3 runtime contracts with real LangGraph and synthetic, metered providers."""
import copy
import json
import sqlite3

import pytest

from inkreference.chronicle_schema import canonical_stage_schema
from inkreference.database import DataError
from inkreference.documents import DocumentStore
from inkreference.fixed_pipeline import FixedPipeline
from inkreference.skills import SkillStore, build_stage_prompt
from inkreference.volumes import VolumeStore
from inkreference.workflow_definitions import WorkflowStore, PROGRESS_WORKFLOW
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


class Models:
    def __init__(self): self.calls = []
    def snapshot(self, identifier):
        return {"id": identifier, "provider": "test", "model": "synthetic-progress", "context_window": 160000, "max_output_tokens": 12000}
    def estimate_messages(self, messages):
        return {"input_tokens": sum(len(message["content"]) for message in messages)}
    def invoke(self, identifier, messages, **kwargs):
        stage = kwargs["chunk_id"].split("::")[-1]
        prompt = messages[-1]["content"]
        text = prompt.split("<source_text>\n", 1)[1].rsplit("\n</source_text>", 1)[0]
        self.calls.append({"stage": stage, "text": text, "messages": messages, **kwargs})
        schema = canonical_stage_schema(stage, version=3)
        result = empty(schema)
        offset = text.find("甲")
        if offset < 0: offset = 0
        proof = {"evidence": text[offset:offset + 1], "revealed_at": {"chapter": None, "offset": offset}, "source_version_id": ""}
        if stage == "plot":
            event = empty(schema["properties"]["plot"]["properties"]["events"]["items"])
            event.update(id="event-" + kwargs["chunk_id"].split("::")[0], name="甲送信", theme="选择", description="甲把信交给乙。", narrative_progress=77, **proof)
            result["plot"].update(logline="甲送信。", events=[event])
        if stage == "characters":
            character = empty(schema["properties"]["characters"]["items"])
            character.update(id="character-jia", name="甲", aliases=["小甲"], role_tag="主角", temperament="利他", **proof)
            result["characters"] = [character]
        return {"content": json.dumps(result, ensure_ascii=False), "call_id": str(len(self.calls)), "usage": {"input_tokens": 20, "output_tokens": 10}}


def setup(tmp_path, text="第1章 来信\n甲把信交给乙。\n第2章 离开\n乙离开。\n"):
    documents = DocumentStore(tmp_path)
    document = documents.upload("synthetic.txt", text.encode(), title="合成作品")
    models = Models()
    runner = WorkflowRunner(tmp_path, documents=documents, models=models)
    return runner, documents, document, models


def test_default_v3_real_graph_derives_progress_and_preserves_proofs(tmp_path):
    runner, documents, document, models = setup(tmp_path)
    run = runner.create_run(document["id"], profile_id="synthetic")
    assert run["workflow_version"] == "3.0.0"
    assert run["snapshots"]["contract_version"] == 3
    final = runner.execute(run["id"])
    assert final["status"] == "needs_review", final["error"]
    assert final["coverage"]["complete"]
    assert [call["stage"] for call in models.calls] == ["plot", "characters", "settings", "language"]
    reference = final["candidates"][0]["result"]["reference"]
    event = reference["plot"]["events"][0]
    assert event["narrative_progress"] == 50  # Provider's guessed 77 is not authoritative.
    assert event["revealed_at"]["chapter"] == 1
    assert event["source_version_id"] == run["version_id"]
    assert reference["characters"][0]["initial_state"]["narrative_progress"] == 50
    assert all("total_chapters" in call["messages"][-1]["content"] for call in models.calls)
    assert sum(stage["tokens"] for stage in final["stages"]) == 120


def test_unchaptered_v3_progress_uses_body_characters(tmp_path):
    runner, _, document, _ = setup(tmp_path, "序" * 20 + "甲" + "乙" * 79)
    run = runner.create_run(document["id"], profile_id="synthetic")
    final = runner.execute(run["id"])
    assert final["status"] == "needs_review", final["error"]
    event = final["candidates"][0]["result"]["reference"]["plot"]["events"][0]
    assert event["narrative_progress"] == 20


def test_v3_snapshot_includes_event_experience_and_derived_range(tmp_path):
    runner, _, document, models = setup(tmp_path)
    invoke = models.invoke
    def with_snapshot(identifier, messages, **kwargs):
        response = invoke(identifier, messages, **kwargs)
        if kwargs["chunk_id"].endswith("::characters"):
            result = json.loads(response["content"])
            character = result["characters"][0]
            schema = canonical_stage_schema("characters", version=3)["properties"]["characters"]["items"]["properties"]["snapshots"]["items"]
            snapshot = empty(schema)
            snapshot.update(id="snapshot-delivery", event_ids=["event-" + kwargs["chunk_id"].split("::")[0]], ability="送信", evidence=character["evidence"], revealed_at=copy.deepcopy(character["revealed_at"]), source_version_id="")
            character["snapshots"] = [snapshot]
            response["content"] = json.dumps(result, ensure_ascii=False)
        return response
    models.invoke = with_snapshot
    run = runner.create_run(document["id"], profile_id="synthetic")
    final = runner.execute(run["id"])
    assert final["status"] == "needs_review", final["error"]
    character = final["candidates"][0]["result"]["reference"]["characters"][0]
    assert character["snapshots"][0]["event_ids"] == ["event-chunk-000001"]
    assert character["snapshots"][0]["narrative_range"] == {"start": 50, "end": None}
    assert character["snapshots"][0]["ability"] == "送信"


def test_v3_saved_volume_boundaries_are_coarse_and_source_is_complete(tmp_path):
    text = "".join(f"第{number}章 来信\n" + "甲把信交给乙。" * 300 + "\n" for number in range(1, 9))
    runner, documents, document, _ = setup(tmp_path, text)
    version = documents.get_version(document["id"])
    VolumeStore(tmp_path, documents).save(document["id"], version["id"], [
        {"title": "上卷", "start_chapter": 1, "end_chapter": 4},
        {"title": "下卷", "start_chapter": 5, "end_chapter": 8},
    ], 0)
    run = runner.create_run(document["id"], profile_id="synthetic")
    assert run["total"] == 2
    assert [group["volume"] for group in run["snapshots"]["chunk_groups"]] == ["上卷", "下卷"]
    with sqlite3.connect(runner.db_path) as db:
        chunks = db.execute("SELECT start,end,text FROM run_chunks WHERE run_id=? ORDER BY position", (run["id"],)).fetchall()
    assert "".join(chunk[2] for chunk in chunks) == version["text"]
    assert chunks[0][0] == 0 and chunks[0][1] == chunks[1][0] and chunks[-1][1] == len(version["text"])


def test_v3_small_context_still_covers_oversize_chapter_without_truncation(tmp_path):
    runner, documents, document, models = setup(tmp_path, "第1章 长信\n" + ("甲写信。\n" * 9000))
    workflow = {**copy.deepcopy(PROGRESS_WORKFLOW), "id": "small-context", "max_input_tokens": 22000, "max_output_tokens": 1024}
    runner.workflows.save(workflow)
    run = runner.create_run(document["id"], workflow_id=workflow["id"], workflow_version="3.0.0", profile_id="synthetic")
    assert run["total"] > 1
    with sqlite3.connect(runner.db_path) as db:
        chunks = db.execute("SELECT id,position,start,end,text FROM run_chunks WHERE run_id=? ORDER BY position", (run["id"],)).fetchall()
    assert "".join(row[4] for row in chunks) == documents.get_version(document["id"])["text"]
    for row in chunks:
        chunk = dict(zip(("id", "position", "start", "end", "text"), row))
        with runner._db() as db:
            context = runner._fixed_context(db, run["id"], chunk, "plot")
        assert runner._estimate(runner._fixed_messages(run["snapshots"], chunk["text"], "plot", context)) <= 22000


def test_v3_stage_override_cannot_change_contract_and_upload_is_atomic(tmp_path):
    runner, _, document, _ = setup(tmp_path)
    workflow = {**copy.deepcopy(PROGRESS_WORKFLOW), "id": "mixed-contract", "stage_skills": {"plot": {"id": "unified-reference", "version": "2.0.0"}}}
    runner.workflows.save(workflow)
    with pytest.raises(DataError, match="同一 v3"):
        runner.create_run(document["id"], workflow_id=workflow["id"], workflow_version="3.0.0", manual=True)
    store = SkillStore(tmp_path)
    before = store.list()
    wrong = {**store.get(), "id": "wrong-format", "output_schema": {}}
    with pytest.raises(DataError, match="不能修改输出格式"):
        store.upload("skill.json", json.dumps(wrong).encode(), require_contract_version=3)
    assert store.list() == before
    uploaded = store.upload("SKILL.md", b"---\nid: custom-v3\nname: Custom\nversion: 1.0.0\ncontract_version: 3\n---\nExtract causal arcs.", require_contract_version=3)
    assert uploaded["contract_version"] == 3
    run = runner.create_run(document["id"], manual=True)
    with pytest.raises(DataError):
        runner.paste_chunk(run["id"], run["candidates"][0]["chunk_id"], json.dumps(empty(canonical_stage_schema("plot", version=2))), run["revision"])
    assert runner.get_run(run["id"])["revision"] == run["revision"]


def test_v2_builtin_contract_prompt_and_cache_namespace_are_preserved(tmp_path):
    skills, workflows = SkillStore(tmp_path), WorkflowStore(tmp_path)
    assert skills.get()["version"] == workflows.get()["version"] == "3.0.0"
    old = skills.get("unified-reference", "2.0.0")
    assert old["contract_version"] == 2
    prompt = build_stage_prompt(old, "characters", "甲。")
    assert '"schema_version":{"enum":[2]' in prompt
    assert "initial_state" not in prompt
    assert workflows.get("default", "2.0.0")["chunk_chars"] == 4000
    assert FixedPipeline._fixed_cache_key([], {}, 20) == FixedPipeline._fixed_cache_key([], {}, 20, 2)
    assert FixedPipeline._fixed_cache_key([], {}, 20, 3) != FixedPipeline._fixed_cache_key([], {}, 20, 2)
