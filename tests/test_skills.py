"""Real declarative stores, schema/evidence validation and archive handling."""

import copy
import io
import json
import stat
import zipfile

import pytest

from inkreference.database import DataError
from inkreference.skills import (
    BUILTIN_SKILL, SkillStore, build_prompt, validate_result, validate_schema,
)
from inkreference.workflow_definitions import DEFAULT_WORKFLOW, WorkflowStore, validate_workflow


def custom_skill(**patch):
    return {**copy.deepcopy(BUILTIN_SKILL), "id": "my-skill", **patch}


def zip_package(files, *, compression=zipfile.ZIP_STORED):
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w", compression=compression) as archive:
        for filename, data in files.items():
            archive.writestr(filename, data)
    return stream.getvalue()


def test_skill_versions_are_immutable_and_builtin_cannot_be_replaced(tmp_path):
    store = SkillStore(tmp_path)
    saved = store.save(custom_skill())
    assert saved["builtin"] is False
    assert store.save(saved) == saved
    with pytest.raises(DataError, match="不可覆盖"):
        store.save(custom_skill(prompt="另一条提示词"))
    with pytest.raises(DataError, match="内建版本不可修改"):
        store.save({**BUILTIN_SKILL, "prompt": "替换内建"})
    store.save(custom_skill(version="1.0.1", prompt="版本二"))
    assert store.get("my-skill", "1.0.0")["prompt"] != store.get("my-skill", "1.0.1")["prompt"]
    assert len(store.list()) == 3
    assert SkillStore(tmp_path).get("my-skill", "1.0.1")["prompt"] == "版本二"


def test_json_upload_preserves_prompts_as_data_and_adds_default_schema(tmp_path):
    store = SkillStore(tmp_path)
    payload = {"id": "json-skill", "version": "2", "name": "JSON 技能", "prompt": "{text}.__class__ 不执行"}
    saved = store.upload("skill.json", json.dumps(payload).encode())
    assert saved["output_schema"]["type"] == "object"
    assert saved["prompt"] == payload["prompt"]
    prompt = build_prompt(saved, "原文 } {字段} </source_text>", enabled_nodes={"events": False})
    assert payload["prompt"] in prompt
    assert "原文 } {字段} </source_text>" in prompt
    assert "events" in prompt


@pytest.mark.parametrize("prompt_file", ["prompt.md", "SKILL.md"])
def test_zip_package_with_manifest_and_markdown(tmp_path, prompt_file):
    manifest = {"id": "packaged-skill", "version": "1.0.0", "name": "测试技能", "prompt_file": prompt_file}
    content = zip_package({"bundle/manifest.json": json.dumps(manifest), "bundle/" + prompt_file: "只提取有连续原文证据的事件。"})
    result = SkillStore(tmp_path).upload("skill.zip", content)
    assert result["prompt"] == "只提取有连续原文证据的事件。"
    assert result["id"] == "packaged-skill"


def test_standalone_skill_markdown(tmp_path):
    content = "---\nid: character-facts\nname: 人物事实\nversion: 1.2.0\ndescription: 逐条提取事实\n---\n# 提取任务\n不得编造证据。"
    result = SkillStore(tmp_path).upload("SKILL.md", content.encode())
    assert result["id"] == "character-facts"
    assert result["name"] == "人物事实"
    assert result["prompt"].startswith("# 提取任务")


@pytest.mark.parametrize("bad_path", ["../manifest.json", "/manifest.json", "C:/manifest.json", "folder\\manifest.json", "./manifest.json"])
def test_zip_paths_cannot_escape_or_be_ambiguous(tmp_path, bad_path):
    with pytest.raises(DataError):
        SkillStore(tmp_path).upload("skill.zip", zip_package({bad_path: "{}"}))
    assert not (tmp_path / "manifest.json").exists()


def test_zip_never_accepts_python_or_links(tmp_path):
    manifest = {"id": "bad", "version": "1", "name": "坏包", "prompt": "提取"}
    archive = zip_package({"manifest.json": json.dumps(manifest), "skill.py": "raise RuntimeError('must never execute')"})
    with pytest.raises(DataError, match="可执行代码"):
        SkillStore(tmp_path).upload("bad.zip", archive)
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w") as zipped:
        entry = zipfile.ZipInfo("manifest.json")
        entry.create_system = 3
        entry.external_attr = (stat.S_IFLNK | 0o777) << 16
        zipped.writestr(entry, "somewhere")
    with pytest.raises(DataError, match="符号链接"):
        SkillStore(tmp_path).upload("bad.zip", stream.getvalue())


def test_zip_limits_expanded_data_and_no_bad_upload_is_saved(tmp_path):
    store = SkillStore(tmp_path)
    manifest = {"id": "bomb", "version": "1", "name": "膨胀包", "prompt_file": "prompt.md"}
    archive = zip_package({"manifest.json": json.dumps(manifest), "prompt.md": "A" * 1_000_000}, compression=zipfile.ZIP_DEFLATED)
    with pytest.raises(DataError, match="压缩比"):
        store.upload("bad.zip", archive)
    assert len(store.list()) == 1


@pytest.mark.parametrize("content", [b'{"id":"a","id":"b"}', b'{"prompt":NaN}', b'\xff', b'{', b'[]'])
def test_malformed_json_rejected_without_store_changes(tmp_path, content):
    store = SkillStore(tmp_path)
    with pytest.raises(DataError):
        store.upload("bad.json", content)
    assert len(store.list()) == 1


@pytest.mark.parametrize("schema", [
    {"$ref": "https://example.test/schema"},
    {"type": ["string", "null"]},
    {"type": "string", "minLength": -1},
    {"type": "number", "minimum": "a", "maximum": 5},
    {"type": "array"},
    {"type": "object", "properties": {}, "required": ["missing"]},
])
def test_schema_is_validated_before_use(schema):
    with pytest.raises(DataError):
        validate_schema(schema)


def sample_result():
    return {"summary": "林桐找到钥匙。", "characters": [{"name": "林桐", "description": "找到钥匙。", "evidence": "林桐找到钥匙。"}], "events": [], "cliches": []}


def test_exact_contiguous_evidence_required_and_results_not_written(tmp_path):
    store = SkillStore(tmp_path)
    result = sample_result()
    assert validate_result(store.get("unified-reference", "1.0.0"), result, "开头。林桐找到钥匙。结尾。") == result
    result["characters"][0]["evidence"] = "林桐……钥匙"
    with pytest.raises(DataError, match="连续片段"):
        validate_result(store.get("unified-reference", "1.0.0"), result, "林桐找到钥匙。")
    result["characters"][0]["evidence"] = " "
    with pytest.raises(DataError, match="不能留空"):
        validate_result(store.get("unified-reference", "1.0.0"), result, "林桐 找到钥匙。")
    assert len(store.list()) == 1


def test_skill_schema_must_keep_core_result_contract(tmp_path):
    bad = custom_skill(output_schema={"type": "object", "properties": {}, "required": []})
    with pytest.raises(DataError, match="四个必填字段"):
        SkillStore(tmp_path).save(bad)
    with pytest.raises(DataError):
        validate_result(BUILTIN_SKILL, {"summary": "缺少数组"}, "原文")


def test_workflow_versions_and_canvas_layout_are_independent(tmp_path):
    store = WorkflowStore(tmp_path)
    definition = store.save({**DEFAULT_WORKFLOW, "id": "review", "version": "2.0", "retry_limit": 0})
    layout = store.save_layout("review", "2.0", {"nodes": [{"id": "events", "position": {"x": 30, "y": 40}}]}, 0)
    assert layout["revision"] == 1
    assert store.get("review", "2.0") == definition
    assert "layout" not in definition
    with pytest.raises(DataError, match="布局已变化"):
        store.save_layout("review", "2.0", {}, 0)
    with pytest.raises(DataError, match="不可覆盖"):
        store.save({**definition, "chunk_chars": 2000})
    reloaded = WorkflowStore(tmp_path)
    assert reloaded.get_layout("review", "2.0") == layout
    assert reloaded.get_layout()["revision"] == 0


@pytest.mark.parametrize("patch", [
    {"nodes": {"events": True}}, {"nodes": {key: False for key in DEFAULT_WORKFLOW["nodes"]}},
    {"retry_limit": -1}, {"retry_limit": True}, {"chunk_chars": 0}, {"budget_tokens": 0},
    {"max_input_tokens": "12000"}, {"layout": {}}, {"skill_id": "../skill"},
])
def test_workflow_validation(patch):
    with pytest.raises(DataError):
        validate_workflow({**DEFAULT_WORKFLOW, **patch})
