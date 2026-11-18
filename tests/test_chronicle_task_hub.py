from __future__ import annotations

import threading

from test_chronicle_api import body, client, create_document, create_model, install_mock, start, wait_for


def test_task_hub_lists_pauses_and_reopens_the_selected_chronicle(client, monkeypatch):
    document, path = create_document(client)
    model = create_model(client)
    entered, release = threading.Event(), threading.Event()
    prompts = install_mock(monkeypatch, client, (entered, release))
    run_id = body(start(client, path, document, model), 202)["run"]["id"]
    task_path = f"/api/studio/tasks/chronicle/{run_id}"
    try:
        assert entered.wait(5)
        page = body(client.get("/api/studio/tasks", params={"kind": "chronicle", "status": "active"}))
        assert page["total"] == 1
        task = page["items"][0]
        assert task["id"] == run_id
        assert task["title"] == document["title"]
        assert task["model"] == model["model"] and task["progress"] == .5
        assert task["created_at"] and task["updated_at"]
        assert task["capabilities"] == {"pause": True, "resume": False, "terminate": False}
        assert "config" not in task and "request" not in task and "result" not in task
        assert client.post(task_path + "/terminate").status_code == 409
        paused = body(client.post(task_path + "/pause"))["task"]
        assert paused["status"] == "pausing"
        assert all(not allowed for allowed in paused["capabilities"].values())
    finally:
        release.set()

    wait_for(client, path, "paused")
    response = body(client.post(task_path + "/resume"))
    assert response["task"]["status"] == "paused"
    assert response["action"]["section"] == "reference"
    assert response["action"]["document_id"] == document["id"]
    assert prompts == ["读取两个原文段落"]

    body(start(client, path, document, model), 202)
    finished = wait_for(client, path, "completed")
    assert finished["run"]["id"] != run_id
    assert body(client.get(task_path))["capabilities"]["resume"] is False
    assert client.post(task_path + "/resume").status_code == 409
