"""Three transform modes use real stores and synthetic HTTP model responses."""
from __future__ import annotations

import copy
import json
from contextlib import contextmanager

import httpx
import pytest
from fastapi.testclient import TestClient

from inkreference.api import create_app
from inkreference.database import DataError
from inkreference.reference_transforms import PROTOCOL


@contextmanager
def context(tmp_path, *, schema=4):
    app = create_app(tmp_path)
    calls, config = [], {}
    with TestClient(app, base_url="http://127.0.0.1") as client:
        studio = app.state.runtime.studio
        document = studio.documents.upload("合成资料.txt", "第1章 旧城\n林青进入旧城。\n第2章 归来\n林青找到归途。".encode())
        proof = {"source_version_id": document["latest_version_id"], "revealed_at": {"chapter": 1, "offset": None}, "evidence": "林青进入旧城。", "origin": "source"}
        event = {"id": "event-one", "name": "抵达", "theme": "旅途", "description": "林青进入旧城", "narrative_progress": 50,
                 "story_time": "1349年7月", "participants": [], "setting_ids": [], "causes": [], "results": [], "plotline_ids": ["line-one"], "volume": None, **proof}
        node = {"id": "node-one", "description" if schema == 4 else "goal": "找到归途", "status": "progressing", "event_ids": [event["id"]], "narrative_progress": 50, "story_time": "1349年7月", **proof}
        line = {"id": "line-one", "title": "归途", "description" if schema == 4 else "goal": "寻找归途", "time_nodes" if schema == 4 else "stages": [node]}
        plot = {"schema_version": schema, "logline": "旅人寻找归途", "events": [event], "plotlines": [line]}
        state = studio.reference.save_section(document["id"], "plot", plot, 0)
        state = studio.reference.save_section(document["id"], "raw_entries", [{"title": "合成条目", "content": "Hello\nworld"}], state["revision"])
        models = studio.models
        models.save_profile({"id": "transform-test", "provider": "openai_compatible", "model": "synthetic-transform", "base_url": "http://127.0.0.1:12345/v1", "context_window": 64000, "max_output_tokens": 2000})

        def handler(request):
            body = json.loads(request.content)
            calls.append(body)
            prompt = body["messages"][-1]["content"]
            envelope = json.loads(prompt.split("输出模板：\n", 1)[1].split("\n<source_text>", 1)[0])
            source = prompt.split("<source_text>\n", 1)[1].rsplit("\n</source_text>", 1)[0]
            if config.get("callback"):
                config["callback"]()
            if "schema_version" in envelope["data"]:
                data = json.loads(source)
                data["logline"] = "合成模型概括的剧情梗概"
                data["events"][0]["description"] = "旅人抵达旧城"
            else:
                data = {"text": "你好\n世界"}
            if config.get("mutate"):
                config["mutate"](data)
            value = {"protocol": PROTOCOL, "request_id": config.get("request_id", envelope["request_id"]), "data": data}
            content = config.get("content", json.dumps(value, ensure_ascii=False))
            return httpx.Response(200, json={"choices": [{"message": {"content": content}, "finish_reason": "stop"}], "usage": {"prompt_tokens": 133, "completion_tokens": 51}})

        models.transport = httpx.MockTransport(handler)
        yield {"client": client, "studio": studio, "document": document, "state": state, "calls": calls, "config": config,
               "path": f"/api/studio/documents/{document['id']}/reference/transform"}


def action(c, kind, **payload):
    return c["client"].post(c["path"], json={"kind": kind, "profile_id": "transform-test", "payload": {
        "output_contract": 4, "expected_revision": c["state"]["revision"], **payload}})


def prompt(c, kind="summarize", **payload):
    result = action(c, kind, prompt_only=True, **payload)
    assert result.status_code == 200, result.text
    return result.json()


def test_summary_copy_projection_manual_template_and_explicit_save_only(tmp_path):
    with context(tmp_path, schema=3) as c:
        before = c["studio"].reference.get(c["document"]["id"])
        p = prompt(c)
        assert p["source_revision"] == before["revision"] and len(p["request_id"]) == 64
        assert p["prompt"].count(p["source"]) == 1
        assert p["template"]["protocol"] == PROTOCOL and p["template"]["data"]["schema_version"] == 4
        assert "epochs" not in p["template"]["data"] and "time_nodes" in p["template"]["data"]["plotlines"][0]
        manual = copy.deepcopy(p["template"]["data"])
        manual["logline"] = "人工概括"
        response = action(c, "summarize", manual_data=manual)
        assert response.status_code == 200, response.text
        assert response.json()["data"]["logline"] == "人工概括" and response.json()["mode"] == "manual"
        assert not c["calls"] and c["studio"].reference.get(c["document"]["id"]) == before
        saved = c["client"].put(c["path"].removesuffix("/transform") + "/plot", json={"data": response.json()["data"], "expected_revision": before["revision"]})
        assert saved.status_code == 200 and saved.json()["revision"] == before["revision"] + 1


def test_contract_two_source_becomes_readonly_v4_template_without_migrating_raw(tmp_path):
    with context(tmp_path) as c:
        old_event = {"id": "event-two", "name": "抵达", "description": "进入旧城", "participants": [], "setting_ids": [], "causes": [], "results": [], "plotline_ids": [],
                     "story_time": {"label": "旧历初春", "sort_key": None}, "volume": None,
                     "source_version_id": c["document"]["latest_version_id"], "revealed_at": {"chapter": 1, "offset": None}, "evidence": "林青进入旧城。"}
        saved = c["studio"].reference.save_section(c["document"]["id"], "plot", {"schema_version": 2, "logline": "旧合同梗概", "events": [old_event], "plotlines": []}, c["state"]["revision"])
        c["state"] = saved
        before = copy.deepcopy(saved)
        result = prompt(c)
        assert result["template"]["data"]["schema_version"] == 4
        assert result["template"]["data"]["events"][0]["id"] == "event-two"
        assert c["studio"].reference.get(c["document"]["id"]) == before
        assert not c["calls"]


def test_automatic_summary_real_model_usage_preserves_proof_and_saved_raw(tmp_path):
    with context(tmp_path) as c:
        before = c["studio"].reference.get(c["document"]["id"])
        p = prompt(c)
        response = action(c, "summarize")
        assert response.status_code == 200, response.text
        result = response.json()
        assert result["data"]["logline"] == "合成模型概括的剧情梗概" and result["mode"] == "automatic"
        event, original = result["data"]["events"][0], p["template"]["data"]["events"][0]
        for key in ("id", "story_time", "narrative_progress", "source_version_id", "revealed_at", "evidence", "plotline_ids"):
            assert event[key] == original[key]
        assert result["usage"]["input_tokens"] == 133
        ledger = c["studio"].models.list_usage(result["run_id"])
        assert len(ledger) == 1 and ledger[0]["usage"]["output_tokens"] == 51
        assert c["studio"].reference.get(c["document"]["id"]) == before
        assert len(c["calls"]) == 1


def test_semi_summary_validated_fence_and_revision_request_binding_without_api(tmp_path):
    with context(tmp_path) as c:
        p = prompt(c)
        envelope = p["template"]
        envelope["data"]["logline"] = "网页模型概括"
        response = action(c, "summarize", reply="```json\n" + json.dumps(envelope, ensure_ascii=False) + "\n```")
        assert response.status_code == 200, response.text
        assert response.json()["mode"] == "semi" and response.json()["data"]["logline"] == "网页模型概括"
        envelope["request_id"] = "old"
        assert action(c, "summarize", reply=json.dumps(envelope)).status_code == 409
        c["studio"].reference.save_section(c["document"]["id"], "metadata", {"edited": True}, c["state"]["revision"])
        stale = action(c, "summarize", reply=json.dumps(envelope))
        assert stale.status_code == 409 and not c["calls"]


@pytest.mark.parametrize("change", [
    lambda data: data["events"][0].update(id="invented-id"),
    lambda data: data["events"][0].update(story_time="9999年"),
    lambda data: data["events"][0].update(narrative_progress=99),
    lambda data: data["events"][0].update(evidence="捏造引文"),
    lambda data: data["events"][0].update(source_version_id="another-source"),
    lambda data: data["events"][0].update(participants=["invented-character"]),
    lambda data: data["plotlines"][0].update(id="invented-line"),
    lambda data: data["plotlines"][0]["time_nodes"][0].update(id="invented-node"),
    lambda data: data["plotlines"][0]["time_nodes"][0].update(status="resolved"),
])
def test_model_summary_cannot_invent_identity_or_change_pinned_fact_metadata(tmp_path, change):
    with context(tmp_path) as c:
        c["config"]["mutate"] = change
        before = c["studio"].reference.get(c["document"]["id"])
        result = action(c, "summarize")
        assert result.status_code == 200 and result.json()["data"] is None and result.json()["validation_error"], result.text
        assert c["studio"].reference.get(c["document"]["id"]) == before
        assert c["studio"].models.usage_summary()["actual_output_tokens"] == 51


def test_translation_all_three_modes_use_same_data_and_preserve_original_entry(tmp_path):
    with context(tmp_path) as c:
        p = prompt(c, "translate", entry_index=0, target_language="中文")
        assert p["source"] == "Hello\nworld" and p["template"]["data"] == {"text": ""}
        manual = action(c, "translate", manual_data={"text": "手填译文"})
        assert manual.status_code == 200 and manual.json()["text"] == "手填译文"
        envelope = {**p["template"], "data": {"text": "网页译文"}}
        semi = action(c, "translate", target_language="中文", reply=json.dumps(envelope))
        assert semi.status_code == 200 and semi.json()["text"] == "网页译文"
        automatic = action(c, "translate")
        assert automatic.status_code == 200 and automatic.json()["text"] == "你好\n世界"
        assert len(c["calls"]) == 1
        assert c["studio"].reference.get(c["document"]["id"])["sections"]["raw_entries"][0]["content"] == "Hello\nworld"


@pytest.mark.parametrize("data", [{}, {"text": ""}, {"text": "   "}, {"text": 3}, {"text": "valid", "plot": {}}, {"logline": "wrong"}])
def test_empty_wrong_translation_objects_rejected_in_manual_and_semi(tmp_path, data):
    with context(tmp_path) as c:
        p = prompt(c, "translate")
        assert action(c, "translate", manual_data=data).status_code == 422
        assert action(c, "translate", reply=json.dumps({**p["template"], "data": data})).status_code == 422
        assert not c["calls"]


def test_duplicates_wrong_envelope_old_epochs_and_mode_mixture_rejected(tmp_path):
    with context(tmp_path) as c:
        p = prompt(c)
        duplicate = json.dumps(p["template"]).replace('"protocol":', '"protocol":"duplicate","protocol":')
        assert action(c, "summarize", reply=duplicate).status_code == 422
        assert action(c, "summarize", manual_data={"logline": "old", "epochs": []}).status_code == 422
        assert action(c, "summarize", manual_data=p["template"]["data"], reply="{}").status_code == 422
        assert action(c, "summarize", manual_data=p["template"]["data"], prompt_only=True).status_code == 422
        assert action(c, "summarize", reply=json.dumps({**p["template"], "commands": []})).status_code == 422


def test_model_reply_wrong_request_id_and_source_revision_change_keep_usage(tmp_path):
    with context(tmp_path) as c:
        c["config"]["request_id"] = "wrong-id"
        invalid = action(c, "translate")
        assert invalid.status_code == 200 and invalid.json()["data"] is None and "不符" in invalid.json()["validation_error"]
        c["config"].pop("request_id")
        c["config"]["callback"] = lambda: c["studio"].reference.save_section(c["document"]["id"], "metadata", {"changed": True}, c["state"]["revision"])
        result = action(c, "translate")
        assert result.status_code == 409 and "用量已保留" in result.text
        assert c["studio"].models.usage_summary()["actual_output_tokens"] == 102


def test_invalid_automatic_json_is_editable_and_semi_correction_makes_no_second_call(tmp_path):
    with context(tmp_path) as c:
        before = c["studio"].reference.get(c["document"]["id"])
        c["config"]["content"] = "{not valid JSON}"
        response = action(c, "summarize")
        assert response.status_code == 200, response.text
        invalid = response.json()
        assert invalid["data"] is None and invalid["content"] == "{not valid JSON}"
        assert invalid["validation_error"] and invalid["diagnostic_id"].startswith("err-")
        assert invalid["usage"]["output_tokens"] == 51 and invalid["call_id"]
        corrected = invalid["template"]
        corrected["data"]["logline"] = "人工修正模型结果"
        result = action(c, "summarize", reply=json.dumps(corrected, ensure_ascii=False))
        assert result.status_code == 200 and result.json()["data"]["logline"] == "人工修正模型结果"
        assert len(c["calls"]) == 1 and c["studio"].reference.get(c["document"]["id"]) == before


def test_prompt_schema_and_output_all_count_toward_context_before_network(tmp_path):
    with context(tmp_path) as c:
        c["studio"].models.save_profile({"context_window": 2000, "max_output_tokens": 1000}, profile_id="transform-test")
        result = action(c, "summarize")
        assert result.status_code == 422 and "上下文" in result.text
        assert not c["calls"] and c["studio"].models.usage_summary()["calls"] == 0


def test_manual_changes_are_explicit_human_input_and_do_not_call_provider(tmp_path):
    with context(tmp_path) as c:
        p = prompt(c)
        data = copy.deepcopy(p["template"]["data"])
        data["events"][0]["story_time"] = "人工修订时间"
        result = action(c, "summarize", manual_data=data)
        assert result.status_code == 200 and result.json()["data"]["events"][0]["story_time"] == "人工修订时间"
        assert not c["calls"]


def test_requests_are_bound_to_translation_text_language_and_summary_granularity(tmp_path):
    with context(tmp_path) as c:
        p = prompt(c, "translate", text="explicit text", target_language="英语")
        old = {**p["template"], "data": {"text": "translated"}}
        assert action(c, "translate", text="another text", target_language="英语", reply=json.dumps(old)).status_code == 409
        assert action(c, "translate", text="explicit text", target_language="中文", reply=json.dumps(old)).status_code == 409
        outline = prompt(c, granularity="book")["template"]
        assert action(c, "summarize", granularity="chapter", reply=json.dumps(outline)).status_code == 409
