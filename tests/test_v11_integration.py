"""Real HTTP integration for shared character state and progress views."""
import copy
from contextlib import closing

from fastapi.testclient import TestClient
from inkreference.api import create_app


def person(identity="source-hero"):
    return {"schema_version": 3, "id": identity, "name": "林青", "aliases": ["小林"],
            "role_tag": "主角", "temperament": "利他", "visual_design": "青衣",
            "initial_state": {"gender": "男", "age": "20", "identity": "旅人", "ability": "寻路",
                "personality": "谨慎", "appearance": "黑发", "relationships": [],
                "narrative_progress": 0, "story_time": None}, "snapshots": []}


def setup_work(runtime):
    store = runtime.store
    store.create_record("works", {"作品ID": "W1", "作品": "合成旅途", "作者": "合成作者", "剧情梗概": "旅人入城。"})
    store.create_record("characters", {"人物ID": "global-hero", "角色名": "林青", "关联作品": '[{"作品ID":"W1"}]'})
    document = runtime.studio.documents.upload("合成旅途.txt", "第1章 入城\n林青走进旧城。\n第2章 离城\n林青走出旧城。".encode())
    return document


def test_new_routes_are_lazy_and_do_not_initialize_research(tmp_path):
    destination = tmp_path / "workspace"
    app = create_app(destination)
    assert not destination.exists()
    with TestClient(app, base_url="http://127.0.0.1") as client:
        assert client.get("/api/library/revision").json()["revision"]
        assert app.state.runtime._studio is None
        assert client.get("/api/health").status_code == 200
    assert not (destination / "workbench.sqlite3").exists()


def test_http_character_context_and_global_identity_are_bidirectional(tmp_path):
    app = create_app(tmp_path)
    with TestClient(app, base_url="http://127.0.0.1") as client:
        runtime = app.state.runtime
        doc = setup_work(runtime)
        prefix = f"/api/studio/documents/{doc['id']}"
        linked = client.put(prefix + "/catalog", json={"expected_work_id": None,
            "expected_version_id": doc["latest_version_id"], "work_id": "W1"})
        assert linked.status_code == 200, linked.text
        saved = client.put(prefix + "/reference/characters", json={"data": [person()], "expected_revision": 0})
        assert saved.status_code == 200, saved.text
        links = client.get(prefix + "/reference/characters/catalog").json()
        assert links["links"] == []
        bound = client.post(prefix + "/reference/characters/source-hero/catalog", json={
            "character_id": "global-hero", "expected_reference_revision": saved.json()["revision"],
            "expected_catalog_revision": links["catalog_revision"]})
        assert bound.status_code == 200, bound.text
        context = bound.json()["contexts"][0]
        changed = copy.deepcopy(context["character"])
        changed["initial_state"]["ability"] = "辨别方向"
        response = client.put("/api/library/characters/global-hero/contexts/W1", json={
            "character": changed, "expected_revision": context["revision"],
            "expected_reference_revision": context["reference_revision"],
            "expected_catalog_revision": bound.json()["catalog_revision"]})
        assert response.status_code == 200, response.text
        assert client.get(prefix + "/reference").json()["sections"]["characters"][0]["initial_state"]["ability"] == "辨别方向"
        global_character = runtime.store.get_record("characters", "global-hero")
        runtime.store.update_record("characters", "global-hero", {"角色名": "林青远"}, global_character["revision"])
        state = client.get(prefix + "/reference").json()
        assert state["sections"]["characters"][0]["name"] == "林青远"
        assert state["sections"]["characters"][0]["initial_state"]["ability"] == "辨别方向"
        state["sections"]["characters"][0]["initial_state"]["age"] = "21"
        response = client.put(prefix + "/reference/characters", json={"data": state["sections"]["characters"], "expected_revision": state["revision"]})
        assert response.status_code == 200, response.text
        contexts = client.get("/api/library/characters/global-hero/contexts").json()
        assert contexts["contexts"][0]["character"]["initial_state"]["age"] == "21"
        stale = client.put("/api/library/characters/global-hero/contexts/W1", json={
            "character": changed, "expected_revision": context["revision"],
            "expected_reference_revision": context["reference_revision"],
            "expected_catalog_revision": bound.json()["catalog_revision"]})
        assert stale.status_code == 409


def test_progress_projection_and_legacy_candidate_preview_do_not_write(tmp_path):
    app = create_app(tmp_path)
    with TestClient(app, base_url="http://127.0.0.1") as client:
        runtime = app.state.runtime
        doc = runtime.studio.documents.upload("分章.txt", "第1章 开始\n林青入城。\n第2章 结束\n林青离城。".encode())
        version = runtime.studio.documents.get_version(doc["id"])
        prefix = f"/api/studio/documents/{doc['id']}/reference"
        old = {"schema_version": 2, "logline": "合成旅途", "events": [{"id": "E1", "name": "离城", "description": "林青离城。", "participants": [],
            "setting_ids": [], "causes": [], "results": [], "plotline_ids": [], "volume": None,
            "story_time": {"label": "1349年7月", "sort_key": 2}, "revealed_at": {"chapter": 2, "offset": version["text"].index("林青离城。")},
            "source_version_id": version["id"], "evidence": "林青离城。"}], "plotlines": []}
        before = runtime.studio.reference.get(doc["id"])
        response = client.post(prefix + "/preview", json={"sections": {"plot": old}, "source_version_id": version["id"]})
        assert response.status_code == 200, response.text
        event = response.json()["sections"]["plot"]["events"][0]
        assert event["narrative_progress"] == 100
        assert event["story_time"] == "1349年7月"
        assert runtime.studio.reference.get(doc["id"]) == before
        event["narrative_progress"] = 20
        event.pop("evidence", None); event.pop("source_version_id", None); event.pop("revealed_at", None)
        event["origin"] = "manual"
        response = client.put(prefix + "/plot", json={"data": {"schema_version": 3, "logline": "旅途", "events": [event], "plotlines": []}, "expected_revision": before["revision"]})
        assert response.status_code == 200, response.text
        assert not client.get(prefix + "/projection?schema_version=3&progress=19").json()["sections"]["plot"]["events"]
        assert len(client.get(prefix + "/projection?schema_version=3&progress=20").json()["sections"]["plot"]["events"]) == 1
        assert client.get(prefix + "/projection?progress=101&schema_version=3").status_code == 422


def test_search_settings_and_key_are_local_and_project_backup_contains_research(tmp_path):
    app = create_app(tmp_path)
    with TestClient(app, base_url="http://127.0.0.1") as client:
        key = "invented-search-key-for-test"
        response = client.put("/api/studio/research/settings", json={"provider": "tavily", "credential_mode": "session", "api_key": key})
        assert response.status_code == 200, response.text
        assert response.json()["key_available"]
        assert key not in response.text
        assert key not in client.get("/api/studio/research/settings").text
        assert app.state.runtime.studio._runner is None
        response = client.get("/api/studio/projects/export")
        assert response.status_code == 200, response.text[:300]
        import io, zipfile, sqlite3
        with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
            assert not any(key.encode() in archive.read(name) for name in archive.namelist() if not name.endswith("/"))
            name = next(name for name in archive.namelist() if name.endswith("workbench.sqlite3"))
            target = tmp_path / "exported-workbench.sqlite3"
            target.write_bytes(archive.read(name))
        with closing(sqlite3.connect(target)) as db:
            assert db.execute("SELECT name FROM sqlite_master WHERE name='research_settings'").fetchone()
