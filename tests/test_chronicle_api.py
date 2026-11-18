"""Whole-book integration over the real Studio API and SQLite with a synthetic provider."""
from __future__ import annotations

import copy
import json
import threading
import time

import httpx
import pytest
from fastapi.testclient import TestClient

from inkreference.api import create_app
from inkreference import chronicle_store

PREFIX = "/api/studio"
SOURCE = "第一章 归伞\n钟岚在档案室向陶鹭借伞。\n陶鹭答应把伞借给钟岚。\n钟岚在伞柄内发现一串数字。\n第二章 查号\n陶鹭向钟岚说明那串数字是旧仓库钥匙柜的号码。\n"
RESULT = {
    "events": [
        {"id": "event1", "title": "钟岚借到陶鹭的伞", "type": "推进", "story_time": "借伞当天",
         "location": "档案室", "description": "钟岚向陶鹭借伞。陶鹭答应后，钟岚发现伞柄里有一串数字。",
         "storyline_ids": ["s1", "f1"], "basis": "第一章的现场叙述中，陶鹭答应借伞，钟岚发现伞柄里的数字。",
         "source_lines": [2, 3, 4]},
        {"id": "event2", "title": "钟岚弄清数字含义", "type": "高潮", "story_time": "借伞后",
         "location": "", "description": "陶鹭解释伞柄里的数字是旧仓库钥匙柜的号码。",
         "storyline_ids": ["s1", "f1"], "basis": "第二章的对话中，陶鹭解释数字的含义。", "source_lines": [6]},
    ],
    "characters": [
        {"id": "p1", "name": "钟岚", "aliases": [], "identity": "不明", "basis": "钟岚向陶鹭借伞。",
         "source_lines": [2]},
        {"id": "p2", "name": "陶鹭", "aliases": [], "identity": "不明", "basis": "陶鹭答应把伞借给钟岚。",
         "source_lines": [3]},
    ],
    "relationships": [
        {"character_a_id": "p1", "character_b_id": "p2", "narrative_order": 3, "relation": "借伞",
         "basis": "陶鹭答应把伞借给钟岚。", "source_lines": [3]},
    ],
    "settings": [
        {"id": "w1", "name": "刻有数字的伞", "category": "器物", "description": "陶鹭的伞柄内刻有数字。",
         "source_lines": [3, 4]},
    ],
    "storylines": [
        {"id": "s1", "title": "伞柄数字", "character_ids": ["p1", "p2"], "beginning": "钟岚向陶鹭借伞。",
         "development": "陶鹭同意借伞。", "turn": "钟岚发现伞柄数字。", "conclusion": "陶鹭说明数字的含义。"},
    ],
    "foreshadowing": [
        {"id": "f1", "title": "伞柄上的数字", "status": "已收回", "storyline_id": "s1",
         "plant": {"basis": "第一章的现场叙述中，钟岚发现伞柄内有数字。", "source_lines": [4], "event_id": "event1"},
         "return": {"basis": "第二章的对话中，陶鹭解释数字的含义。", "source_lines": [6], "event_id": "event2"}},
    ],
    "questions": [
        {"id": "Q01", "question": "借到伞和发现伞柄数字要按一个事件还是两个事件整理？",
         "options": [{"id": "A", "label": "合为借伞", "effect": "在借伞事件中保留发现数字的经过。"},
                     {"id": "B", "label": "分开记录", "effect": "把发现数字列为单独事件。"}],
         "source_lines": [3, 4]},
    ],
}
COLUMNS = ["叙述序号", "标题", "类型", "故事中时间", "地点", "事件描述", "所属故事线", "定位依据"]


@pytest.fixture
def client(tmp_path):
    app = create_app(tmp_path / "data", tmp_path / "no-dist")
    with TestClient(app, base_url="http://127.0.0.1") as session:
        yield session


def body(response, status=200):
    assert response.status_code == status, response.text
    return response.json()


def create_document(client, source=SOURCE):
    doc = body(client.post(PREFIX + "/documents", files={"file": ("borrowed-umbrella.txt", source.encode(), "text/plain")},
                           data={"title": "合成样书"}), 201)
    return doc, f"{PREFIX}/documents/{doc['id']}/chronicle"


def create_model(client, **overrides):
    return body(client.post(PREFIX + "/models", json={
        "name": "合成响应模型", "provider": "openai_compatible", "base_url": "http://127.0.0.1:1234/v1",
        "model": "synthetic-chronicle", "context_window": 32768, "max_output_tokens": 2048, "timeout_seconds": 5,
        **overrides,
    }), 201)


def start(client, path, document, model, **extra):
    return client.post(path + "/runs", json={
        "version_id": document["latest_version_id"], "profile_id": model["id"], **extra,
    })


def wait_for(client, path, target):
    deadline = time.monotonic() + 8
    while time.monotonic() < deadline:
        state = body(client.get(path))
        if state["run"] and state["run"]["status"] == target:
            return state
        if state["run"] and state["run"]["status"] == "failed":
            pytest.fail(f"拆书失败: {state['run']}")
        time.sleep(.015)
    pytest.fail(f"等待 {target} 超时: {state['run']}")


def install_mock(monkeypatch, client, hold=None):
    prompts = []

    def handle(request):
        prompt = json.loads(request.content)["messages"][-1]["content"]
        prompts.append(prompt)
        output = copy.deepcopy(RESULT)
        if prompt == "应用校准":
            output["events"][0]["title"] = "钟岚借伞并发现数字"
            output["questions"] = []
        if hold and len(prompts) == 1:
            entered, release = hold
            entered.set()
            assert release.wait(5), "合成模型未收到继续信号"
        return httpx.Response(200, json={
            "choices": [{"message": {"content": json.dumps(output, ensure_ascii=False)}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 8, "completion_tokens": 80},
        })

    def analysis(lines, generate, progress, rules=(), **_options):
        assert lines[:6] == SOURCE.splitlines()
        progress("extract", 1, 2)
        generate("读取两个原文段落")
        result = json.loads(generate("复核全书"))
        progress("review", 2, 2)
        return result

    def calibration(data, question, choice, note, lines, generate, **options):
        assert question["id"] == "Q01" and choice == "A" and options["scope"] == "book"
        assert lines[3] == SOURCE.splitlines()[3]
        return json.loads(generate("应用校准")), "同一次借伞中的连续行动合并。"

    monkeypatch.setattr(chronicle_store, "run_chronicle", analysis)
    monkeypatch.setattr(chronicle_store, "apply_calibration", calibration)
    client.app.state.runtime.studio.models.transport = httpx.MockTransport(handle)
    return prompts


def test_chronicle_outline_evidence_versions_and_calibration(client, monkeypatch):
    document, path = create_document(client)
    selected = document["latest_version_id"]
    model = create_model(client)
    prompts = install_mock(monkeypatch, client)

    assert body(client.get(path))["result"] is None
    assert client.get(path + "/outline").status_code == 404
    started = start(client, path, document, model)
    assert started.is_success, started.text
    state = wait_for(client, path, "completed")
    assert state["version_id"] == selected and state["revision"] == 1
    assert state["run"]["current"] == state["run"]["total"] == 2
    event, later_event = body(client.get(path + "/outline"))["事件"]
    assert list(event) == COLUMNS
    assert (event["叙述序号"], later_event["叙述序号"]) == ("E001", "E002")
    assert event["故事中时间"] == "借伞当天"
    assert event["所属故事线"] == ["s1 伞柄数字", "f1 伞柄上的数字（埋设）"]
    assert later_event["所属故事线"] == ["s1 伞柄数字", "f1 伞柄上的数字（收回）"]
    assert "L2" in event["定位依据"]
    evidence = body(client.get(path + "/source", params={"lines": "6,2,6"}))
    assert evidence["version_id"] == selected
    assert [(row["line"], row["text"]) for row in evidence["lines"]] == [(6, SOURCE.splitlines()[5]), (2, SOURCE.splitlines()[1])]
    assert "查号" in evidence["lines"][0]["chapter"]
    assert client.get(path + "/source", params={"lines": "999999"}).status_code == 404
    assert client.get(path + "/source", params={"lines": "2;3"}).status_code == 422

    answer_path = path + "/calibrations/Q01"
    deferred = client.post(answer_path, json={"version_id": selected, "expected_revision": 1, "choice": "defer",
                                             "scope": "item", "note": "稍后决定"})
    assert deferred.is_success, deferred.text
    assert deferred.json()["revision"] == 2 and deferred.json()["decisions"][0]["choice"] == "defer"
    assert len(deferred.json()["result"]["questions"]) == 1
    assert len(prompts) == 2
    answer = {"version_id": selected, "expected_revision": 2, "choice": "A", "scope": "book", "note": "同一次借伞"}
    assert client.post(answer_path, json={**answer, "expected_revision": 1}).status_code == 409
    calibrated = client.post(answer_path, json=answer)
    assert calibrated.is_success, calibrated.text
    applied = wait_for(client, path, "completed")
    assert applied["revision"] == 3 and applied["result"]["questions"] == []
    assert applied["decisions"][0]["choice"] == "A" and prompts[-1] == "应用校准"
    assert body(client.get(path + "/outline"))["事件"][0]["标题"] == "钟岚借伞并发现数字"

    latest = body(client.post(path.removesuffix("/chronicle") + "/versions", json={
        "base_version_id": selected, "options": {},
        "manual_text": SOURCE.replace("在档案室", "在值班室"),
    }), 201)["id"]
    assert latest != selected
    assert body(client.get(path))["version_id"] == selected
    assert body(client.get(path + "/source", params={"lines": "2"}))["lines"][0]["text"] == SOURCE.splitlines()[1]
    alternate = body(client.get(path + "/source", params={"lines": "2", "version_id": latest}))
    assert "值班室" in alternate["lines"][0]["text"]
    assert body(client.get(path, params={"version_id": latest}))["result"] is None


def test_chronicle_pause_resume_reuses_completed_provider_response(client, monkeypatch):
    document, path = create_document(client)
    model = create_model(client)
    entered, release = threading.Event(), threading.Event()
    prompts = install_mock(monkeypatch, client, (entered, release))

    started = start(client, path, document, model)
    assert started.is_success, started.text
    run_id = started.json()["run"]["id"]
    try:
        assert entered.wait(5)
        running = body(client.get(path))
        assert running["run"]["status"] == "running"
        assert running["run"]["current"] == 1 and running["result"] is None
        assert start(client, path, document, model).status_code == 409
        paused = client.post(path + f"/runs/{run_id}/pause", json={})
        assert paused.is_success, paused.text
    finally:
        release.set()
    wait_for(client, path, "paused")
    resumed = client.post(path + f"/runs/{run_id}/resume", json={})
    assert resumed.is_success, resumed.text
    assert wait_for(client, path, "completed")["run"]["id"] == run_id
    assert prompts == ["读取两个原文段落", "复核全书"]


def test_chronicle_remote_profile_requires_target_confirmation(client, monkeypatch):
    document, path = create_document(client)
    model = create_model(client, base_url="https://chronicle.invalid/v1", connection_mode="remote")
    prompts = install_mock(monkeypatch, client)
    assert start(client, path, document, model).status_code == 428
    assert start(client, path, document, model, remote_consent=True).status_code == 428
    assert start(client, path, document, model, remote_consent=True, expected_model_fingerprint="f" * 64).status_code == 409
    assert prompts == [] and body(client.get(path))["run"] is None



def test_chronicle_processes_provider_json_and_keeps_zero_questions(client):
    source = SOURCE.replace("第二章 查号", "钟岚拿着伞回到值班室。")
    document, path = create_document(client, source)
    model = create_model(client)
    extraction = {**copy.deepcopy(RESULT), "questions": []}
    phases = []

    def handle(request):
        prompt = json.loads(request.content)["messages"][-1]["content"]
        if not phases:
            assert "当前原文" in prompt
            assert all(line in prompt for line in source.splitlines())
            phases.append("提取")
            output = extraction
        else:
            assert "复核本批" in prompt
            phases.append("复核")
            output = {}
        return httpx.Response(200, json={
            "choices": [{"message": {"content": json.dumps(output, ensure_ascii=False)}, "finish_reason": "stop"}],
        })

    client.app.state.runtime.studio.models.transport = httpx.MockTransport(handle)
    started = start(client, path, document, model)
    assert started.status_code == 202, started.text
    completed = wait_for(client, path, "completed")
    assert phases[0] == "提取" and set(phases[1:]) == {"复核"}
    assert completed["result"]["questions"] == []
    assert [row["title"] for row in completed["result"]["events"]] == [row["title"] for row in extraction["events"]]
    assert completed["result"]["foreshadowing"][0]["return"]["event_id"] == "E002"
