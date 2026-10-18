"""Validated immutable workflow versions; editable canvas state is independent."""

from __future__ import annotations

import copy
import json
import sqlite3
from pathlib import Path
from typing import Any

from .database import DataError
from .skills import MAX_PROMPT_CHARS, _VersionedDefinitions, _dump, validate_key

DEFAULT_WORKFLOW = {
    "id": "default", "version": "1.0.0", "name": "参考作品分析",
    "skill_id": "unified-reference", "skill_version": "1.0.0",
    "nodes": {"summary": True, "characters": True, "events": True, "cliches": True},
    "custom_prompt": "", "chunk_chars": 6000, "retry_limit": 2,
    "max_input_tokens": 12000, "max_output_tokens": 3000, "budget_tokens": 100000,
}


def validate_workflow(definition: Any) -> dict[str, Any]:
    if not isinstance(definition, dict):
        raise DataError(422, "工作流定义必须是对象")
    allowed = set(DEFAULT_WORKFLOW) | {"builtin"}
    if set(definition) - allowed:
        raise DataError(422, "工作流定义包含未知字段；画布 layout 应单独保存")
    result = copy.deepcopy(DEFAULT_WORKFLOW)
    result.update({key: copy.deepcopy(value) for key, value in definition.items() if key != "builtin"})
    for key in ("id", "skill_id"):
        validate_key(result[key], key)
    for key in ("version", "skill_version"):
        validate_key(result[key], key, version=True)
    if not isinstance(result["name"], str) or not result["name"].strip() or len(result["name"]) > 120:
        raise DataError(422, "工作流名称需为 1–120 字")
    if (not isinstance(result["nodes"], dict) or set(result["nodes"]) != set(DEFAULT_WORKFLOW["nodes"])
            or any(type(value) is not bool for value in result["nodes"].values())):
        raise DataError(422, "nodes 必须包含 summary、characters、events、cliches 四个布尔开关")
    if not any(result["nodes"].values()):
        raise DataError(422, "至少启用一个提取节点")
    if not isinstance(result["custom_prompt"], str) or len(result["custom_prompt"]) > MAX_PROMPT_CHARS:
        raise DataError(422, "自定义提示词过长或格式无效")
    limits = {"chunk_chars": (100, 100000), "retry_limit": (0, 8),
              "max_input_tokens": (128, 2000000), "max_output_tokens": (64, 128000),
              "budget_tokens": (1, 100000000)}
    for key, (minimum, maximum) in limits.items():
        if type(result[key]) is not int or not minimum <= result[key] <= maximum:
            raise DataError(422, f"{key} 必须是 {minimum}–{maximum} 的整数")
    _dump(result)
    return result


class WorkflowStore(_VersionedDefinitions):
    def __init__(self, data_dir: str | Path):
        super().__init__(data_dir, "workflow_definitions.sqlite3", validate_workflow(DEFAULT_WORKFLOW))
        with self._db() as connection:
            connection.execute("CREATE TABLE IF NOT EXISTS layouts (workflow_id TEXT NOT NULL, workflow_version TEXT NOT NULL, revision INTEGER NOT NULL, layout_json TEXT NOT NULL, PRIMARY KEY(workflow_id,workflow_version))")

    def save(self, definition: dict[str, Any]) -> dict[str, Any]:
        """Save a new immutable version. retry_limit is *additional* attempts."""
        return self._save_validated(validate_workflow(definition))

    def get_layout(self, identifier: str = "default", version: str = "1.0.0") -> dict[str, Any]:
        self.get(identifier, version)
        with self._db() as connection:
            row = connection.execute("SELECT revision,layout_json FROM layouts WHERE workflow_id=? AND workflow_version=?", (identifier, version)).fetchone()
        return {"workflow_id": identifier, "workflow_version": version,
                "revision": row["revision"] if row else 0,
                "layout": json.loads(row["layout_json"]) if row else {"nodes": [], "edges": []}}

    def save_layout(self, identifier: str, version: str, layout: dict[str, Any], expected_revision: int | None = None) -> dict[str, Any]:
        self.get(identifier, version)
        if not isinstance(layout, dict):
            raise DataError(422, "layout 必须是 JSON 对象")
        rendered = _dump(layout)
        if len(rendered.encode("utf-8")) > 256000:
            raise DataError(422, "画布布局不得超过 256 KB")
        if expected_revision is not None and (type(expected_revision) is not int or expected_revision < 0):
            raise DataError(422, "布局 expected_revision 必须是非负整数")
        with self._db() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute("SELECT revision FROM layouts WHERE workflow_id=? AND workflow_version=?", (identifier, version)).fetchone()
            revision = row["revision"] if row else 0
            if expected_revision is not None and expected_revision != revision:
                raise DataError(409, "画布布局已变化，请刷新后重试")
            connection.execute("INSERT INTO layouts VALUES (?,?,?,?) ON CONFLICT(workflow_id,workflow_version) DO UPDATE SET revision=excluded.revision,layout_json=excluded.layout_json", (identifier, version, revision + 1, rendered))
        return self.get_layout(identifier, version)
