"""Shared interaction contracts for decisions made by a person or a model.

This module describes domain adapters; it does not replace their validators,
durable tasks or explicit commit transactions. Parsing a reply never writes
reference facts or source files.
"""
from __future__ import annotations

import copy
import json
import math
from typing import Any

from .database import DataError

MODES = ("automatic", "semi", "manual")
MODE_LABELS = {"automatic": "自动", "semi": "半自动", "manual": "手动"}


def mode_contract() -> list[dict]:
    return [{"id": mode, "label": MODE_LABELS[mode], "requires_model": mode == "automatic",
             "copies_prompt": mode == "semi", "uses_template": mode == "manual",
             "editable_preview": True, "explicit_confirmation": True} for mode in MODES]


def parse_json_reply(reply: str, *, maximum: int = 2_000_000, max_depth: int = 18,
                     max_nodes: int = 30000, label: str = "回复") -> dict:
    """Accept one JSON object or one fenced object; reject ambiguous payloads."""
    if not isinstance(reply, str) or len(reply) > maximum:
        raise DataError(422, f"{label}不能超过 {maximum} 个字符")
    candidate = reply.strip()
    if candidate.startswith("```json\n") and candidate.endswith("\n```"):
        candidate = candidate[8:-4]

    def pairs(values):
        result = {}
        for key, value in values:
            if key in result:
                raise ValueError("duplicate field")
            result[key] = value
        return result

    def constant(_):
        raise ValueError("non-finite number")

    try:
        value = json.loads(candidate, object_pairs_hook=pairs, parse_constant=constant)
        nodes = 0

        def check(node, depth=0):
            nonlocal nodes
            nodes += 1
            if depth > max_depth or nodes > max_nodes:
                raise ValueError("oversized structure")
            if isinstance(node, dict):
                for key, child in node.items():
                    key.encode("utf-8", "strict")
                    check(child, depth + 1)
            elif isinstance(node, list):
                for child in node:
                    check(child, depth + 1)
            elif isinstance(node, str):
                node.encode("utf-8", "strict")
            elif isinstance(node, float) and not math.isfinite(node):
                raise ValueError("non-finite number")

        check(value)
        if not isinstance(value, dict):
            raise ValueError("object required")
        return value
    except (ValueError, TypeError, RecursionError, UnicodeError):
        raise DataError(422, f"{label}需为单个有效 JSON 对象或 JSON 代码块") from None


def validate_binding(value: dict, expected: dict[str, Any]) -> None:
    """Validate domain-owned session/version bindings before domain parsing."""
    if any(value.get(key) != expected_value for key, expected_value in expected.items()):
        raise DataError(409, "回复与本次任务不符，请使用当前任务的模板")


_OPERATIONS = (
    {"id": "catalog", "label": "作品信息", "adapter": "research", "protocol": "inkreference.catalog.v1",
     "bindings": ["request_id", "catalog_revision", "title", "author"],
     "paths": {"automatic": "/research/jobs", "semi": "/research/semi/prompt", "manual": "/api/records/{table}",
               "preview": "/research/jobs/{id}/draft", "confirm": "/research/jobs/{id}/approve"},
     "template_path": "/research/semi/prompt", "manual_template": {"work": {"title": "", "author": "", "synopsis": ""}, "characters": [], "cliches": []}},
    {"id": "volumes", "label": "分卷与章节", "adapter": "volumes", "protocol": "inkreference.volume.v1",
     "bindings": ["request_id", "document_id", "version_id", "revision"],
     "paths": {"automatic": "/documents/{document_id}/versions/{version_id}/volumes/research",
               "semi": "/documents/{document_id}/versions/{version_id}/volumes/prompt",
               "manual": "/documents/{document_id}/versions/{version_id}/volumes",
               "preview": "/documents/{document_id}/versions/{version_id}/volumes/paste",
               "confirm": "/documents/{document_id}/versions/{version_id}/volumes"},
     "template_path": "/documents/{document_id}/versions/{version_id}/volumes/prompt",
     "manual_template": {"volumes": [{"title": "", "start_chapter": 1, "end_chapter": 1}]}},
    {"id": "extraction", "label": "作品特征提取", "adapter": "fixed_pipeline", "protocol": "reference/4.0.0",
     "bindings": ["run_id", "chunk_id", "source_version_id", "schema_sha256"],
     "paths": {"automatic": "/jobs", "semi": "/jobs/{run_id}/chunks/{chunk_id}/prompt",
               "manual": "/jobs/{run_id}/chunks/{chunk_id}/paste",
               "preview": "/jobs/{run_id}/chunks/{chunk_id}/paste",
               "confirm": "/jobs/{run_id}/chunks/{chunk_id}/commit"},
     "template_path": "/jobs/{run_id}/chunks/{chunk_id}/template",
     "manual_template": {"plot": {"schema_version": 4, "logline": "", "events": [], "plotlines": []}}},
    {"id": "repair", "label": "快捷修复", "adapter": "quick_repair", "protocol": "inkreference.repair.v1",
     "bindings": ["session_id", "path", "sha256"],
     "paths": {"automatic": "/repair/automatic", "semi": "/repair/prompt", "manual": "/repair/prompt",
               "preview": "/repair/preview", "confirm": "/repair/apply"},
     "template_path": "/repair/prompt", "manual_template": {"protocol": "inkreference.repair.v1", "session_id": "", "summary": "", "files": []}},
    *({"id": operation, "label": label, "adapter": "reference_transform", "protocol": "reference/4.0.0",
       "bindings": ["document_id", "source_revision"],
       "paths": {"automatic": "/documents/{document_id}/reference/transform",
                 "semi": "/documents/{document_id}/reference/transform",
                 "manual": "/documents/{document_id}/reference/{section}",
                 "preview": "/documents/{document_id}/reference/preview",
                 "confirm": "/documents/{document_id}/reference/{section}"},
       "template_path": "/documents/{document_id}/reference/transform",
       "manual_template": template}
      for operation, label, template in (
          ("summarize", "大纲整理", {"plot": {"schema_version": 4, "logline": "", "events": [], "plotlines": []}}),
          ("translate", "资料翻译", {"text": ""}),
      )),
)


def operation_registry() -> dict:
    return {"version": 1, "modes": mode_contract(), "operations": copy.deepcopy(list(_OPERATIONS)),
            "confirmation": "domain_transaction", "api_prefix": "/api/studio"}


def operation_definition(operation_id: str) -> dict:
    for item in _OPERATIONS:
        if item["id"] == operation_id:
            return copy.deepcopy(item) | {"modes": mode_contract()}
    raise DataError(404, "动态功能不存在")
