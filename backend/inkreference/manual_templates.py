"""Read-only, source-bound forms for human-authored fixed-stage candidates."""
from __future__ import annotations

import copy
import json
from uuid import uuid4

from .chronicle_schema import canonical_stage_schema
from .database import DataError
from .fixed_pipeline import contract, fixed


def template_value(schema: dict, *, key: str = "", source_version_id: str = ""):
    kinds = schema.get("type", "object")
    kinds = kinds if isinstance(kinds, list) else [kinds]
    if key == "source_version_id": return source_version_id
    if key == "id": return "manual_" + uuid4().hex[:16]
    if key == "schema_version": return 4
    if key == "revealed_at": return {"chapter": None, "offset": None}
    if "null" in kinds: return None
    if "object" in kinds:
        return {name: template_value(child, key=name, source_version_id=source_version_id)
                for name, child in schema.get("properties", {}).items() if name in schema.get("required", [])}
    if "array" in kinds:
        if key == "narrative_composition":
            return [{**template_value(schema["items"], source_version_id=source_version_id), "kind": kind}
                    for kind in ("action", "description", "psychology", "exposition")]
        return []
    if "boolean" in kinds: return False
    if "integer" in kinds or "number" in kinds: return max(0, schema.get("minimum", 0))
    return ""


def extraction_template(runner, run_id: str, chunk_id: str) -> dict:
    """Return a canonical field template, never a fabricated model candidate."""
    with runner._db() as db:
        row = runner._job(db, run_id)
        snapshots = json.loads(row["snapshots_json"])
        if not fixed(snapshots) or contract(snapshots) != 4:
            raise DataError(422, "手动模板需要第 4 版固定工作流")
        chunk = db.execute("SELECT * FROM run_chunks WHERE run_id=? AND id=?", (run_id, chunk_id)).fetchone()
        if chunk is None: raise DataError(404, "切块不存在")
        stage = runner._fixed_stage(db, run_id, chunk_id)
        if not stage:
            return {"stage": None, "schema": None, "template": None, "revision": row["revision"], "completed": True}
        schema = canonical_stage_schema(stage, version=4)
        return {"stage": stage, "schema": copy.deepcopy(schema),
                "template": template_value(schema, source_version_id=row["version_id"]),
                "source_version_id": row["version_id"], "revision": row["revision"], "completed": False}
