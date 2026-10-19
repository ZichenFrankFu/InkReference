"""Invented v3 chronicle/character/setting contract and source-progress tests."""
import copy

import pytest

from inkreference.analysis_projection import project_sections
from inkreference.character_contract import normalize_character, validate_character
from inkreference.chronicle_schema import (REFERENCE_V3_SCHEMA, adapt_v2_section, canonical_stage_schema,
    validate_stage_result, validate_v3_section, validate_links)
from inkreference.database import DataError
from inkreference.documents import DocumentStore
from inkreference.progress import narrative_progress, ordered_story_events
from inkreference.reference_analysis import ReferenceAnalysisStore, validate_section


def event(identity="e1", progress=20, story=None, **changes):
    return {"id": identity, "name": "入城", "theme": "抵达", "description": "林青进入旧城", "narrative_progress": progress,
            "story_time": story, "participants": [], "setting_ids": [], "causes": [], "results": [], "plotline_ids": [], "volume": None, **changes}


def plot(events=None):
    return {"schema_version": 3, "logline": "林青的旅途", "events": events or [], "plotlines": []}


def person(identity="c1", **changes):
    return {"schema_version": 3, "id": identity, "name": "林青", "aliases": ["阿青"], "role_tag": "主角", "temperament": "利他", "visual_design": "灰衣",
            "initial_state": {"gender": None, "age": "", "identity": "旅人", "ability": "寻路", "personality": "谨慎", "appearance": "黑发", "relationships": [], "narrative_progress": 0, "story_time": None}, "snapshots": [], **changes}


def snapshot(snapshot_id="s1", start=20, end=None, **changes):
    return {"id": snapshot_id, "narrative_range": {"start": start, "end": end}, "story_time": None, "event_ids": [],
            "identity": "守卫", "ability": "寻路", "personality": "果断", "appearance": "黑发", "visual_design": "灰衣", "relationships": [], **changes}


def proof(version, quote, chapter=1):
    return {"revealed_at": {"chapter": chapter, "offset": version["text"].index(quote)}, "source_version_id": version["id"], "evidence": quote}


def test_shared_character_contract_manual_fields_and_ranges():
    result = normalize_character(person(snapshots=[snapshot()]))
    assert result["origin"] == result["snapshots"][0]["origin"] == "manual"
    assert result["initial_state"]["gender"] is None
    for update in ({"temperament": "胡乱分类"}, {"snapshots": [snapshot(start=30, end=20)]}):
        with pytest.raises(DataError): validate_character(person(**update))
    with pytest.raises(DataError): validate_character(person(intro="旧业务字段"))
    with pytest.raises(DataError): validate_character(person(), require_provenance=True)


def test_progress_uses_real_chapter_ordinals_and_unheaded_body_percentage():
    source = {"text": "一" * 1000, "chapters": [{"number": index * 2, "start": (index-1)*10, "end": index*10, "body_start": (index-1)*10+1, "raw_marker": f"第{index*2}章"} for index in range(1, 101)]}
    assert narrative_progress(source, {"chapter": 40, "offset": 191}) == 20
    body = {"text": "甲" * 200 + "\n\n" + "乙" * 800, "chapters": [{"number": 1, "start": 0, "body_start": 0, "end": 1002, "kind": "fallback"}]}
    assert narrative_progress(body, {"chapter": 1, "offset": 202}) == 20


def test_story_and_narrative_views_are_flat_and_unknown_dates_keep_narrative_slot():
    events = [event("later", 10, "1349年9月9日下午"), event("unknown", 20), event("earlier", 30, "1346年")]
    result = project_sections({"plot": plot(events)}, target_version=3)
    assert [item["id"] for item in result["plot"]["views"]["narrative"]] == ["later", "unknown", "earlier"]
    assert [item["id"] for item in result["plot"]["views"]["story"]] == ["earlier", "unknown", "later"]
    assert result["plot"]["outlines"]["book"]
    assert "epochs" not in result["plot"]
    assert len(result["plot"]["events"]) == 3


def test_v2_read_adapter_does_not_mutate_saved_data_or_invent_classification():
    old = {"schema_version": 2, "id": "c1", "name": "林青", "intro": "旧说明", "temperament": "温柔", "visual_design": "灰衣", "states": [], "experiences": []}
    original = copy.deepcopy(old)
    result = adapt_v2_section("characters", [old])[0]
    assert result["id"] == "c1" and result["temperament"] == ""
    assert result["initial_state"]["gender"] is None
    assert not {"intro", "states", "experiences", "first_chapter"} & result.keys()
    assert old == original


def test_strict_provider_contract_requires_proof_manual_business_contract_does_not():
    source = "林青入城。"
    manual = {"plot": plot([event()])}
    validate_v3_section("plot", manual["plot"])
    with pytest.raises(DataError): validate_stage_result("plot", manual, source)
    manual["plot"]["events"][0].update(revealed_at={"chapter": 1, "offset": 0}, source_version_id="", evidence=source)
    result = validate_stage_result("plot", manual, source, "v1")
    assert result["plot"]["events"][0]["origin"] == "source"
    assert result["plot"]["events"][0]["source_version_id"] == "v1"
    assert canonical_stage_schema("plot", 2)["properties"]["plot"]["properties"]["schema_version"]["enum"] == [2]


@pytest.mark.parametrize("value", [-1, 101, float("nan"), float("inf")])
def test_progress_rejects_outside_or_nonfinite_values(value):
    with pytest.raises(DataError): validate_v3_section("plot", plot([event(progress=value)]))


def test_manual_save_has_no_fake_proof_and_keeps_user_progress(tmp_path):
    docs = DocumentStore(tmp_path)
    document = docs.upload("book.txt", "第1章 开始\n林青入城。\n第2章 后来\n林青离城。".encode())
    store = ReferenceAnalysisStore(tmp_path, documents=docs)
    result = store.save_section(document["id"], "plot", plot([event(progress=12)]), 0)
    saved = result["sections"]["plot"]["events"][0]
    assert saved["origin"] == "manual" and "evidence" not in saved
    version = docs.get_version(document["id"])
    value = plot([event(progress=33, **proof(version, "林青入城。"))])
    result = store.save_section(document["id"], "plot", value, result["revision"])
    projected = store.project(document["id"], target_version=3)["sections"]["plot"]["events"][0]
    assert projected["narrative_progress"] == 33
    assert projected["evidence"] == "林青入城。"
    assert projected["origin"] == "manual"


def test_approval_derives_progress_from_exact_immutable_source(tmp_path):
    docs = DocumentStore(tmp_path)
    document = docs.upload("book.txt", "第1章 开始\n林青入城。\n第2章 后来\n林青离城。".encode())
    version = docs.get_version(document["id"])
    store = ReferenceAnalysisStore(tmp_path, documents=docs)
    candidate = {"id": "r1", "version_id": version["id"], "document_id": document["id"], "candidates": [{"chunk_id": "c1", "index": 0, "start": 0, "end": len(version["text"]), "status": "completed", "result": {"reference": {"plot": plot([event(progress=1, **proof(version, "林青离城。", 2))])}}}]}
    result = store.commit_candidate(candidate, "c1", ["plot"], 0)
    assert result["sections"]["plot"]["events"][0]["narrative_progress"] == 100
    assert result["sections"]["plot"]["events"][0]["origin"] == "source"


def test_progress_snapshot_projection_relationships_and_event_links():
    people = [person(snapshots=[snapshot(event_ids=["e1"], relationships=[{"character_id": "c2", "relationship": "同伴"}]), snapshot("s2", 60, identity="队长", event_ids=["e2"])]), person("c2")]
    sections = {"plot": plot([event("e1", 20), event("e2", 60)]), "characters": people}
    result = project_sections(sections, target_version=3, progress=30)
    assert [item["id"] for item in result["plot"]["events"]] == ["e1"]
    current = result["characters"][0]
    assert len(current["snapshots"]) == 1 and current["current_snapshot"]["identity"] == "守卫"
    assert current["snapshots"][0]["events"][0]["id"] == "e1"
    assert result["plot"]["events"][0]["participants"] == ["c1"]
    assert len(sections["characters"][0]["snapshots"]) == 2


def test_setting_additions_validate_ids_and_ancestry():
    def setting(identity, earlier=None):
        return {"schema_version": 3, "id": identity, "title": "旧城", "category": "geography", "content": "城墙", "earlier_setting_id": earlier,
                "narrative_progress": 20, "story_time": None, "snapshots": []}
    validate_links({"settings": [setting("s1"), setting("s2", "s1")]})
    with pytest.raises(DataError, match="循环"): validate_links({"settings": [setting("s1", "s2"), setting("s2", "s1")]})
    with pytest.raises(DataError, match="未知 ID"): validate_links({"settings": [setting("s2", "missing")]})


def test_era_appearances_use_progress_and_meme_character_frequency():
    era = {"schema_version": 3, "items": [{"id": "m1", "kind": "meme", "title": "合成梗", "content": "合成含义", "approximate_time": "2020年前后", "appearances": [
        {"narrative_progress": 20, "story_time": None, "event_ids": ["e1"], "character_ids": ["c1"], "context": "林青使用", "usage": "dialogue"},
        {"narrative_progress": 80, "story_time": None, "event_ids": [], "character_ids": [], "context": "叙述引用", "usage": "narration"}]}]}
    validate_v3_section("era_background", era)
    result = project_sections({"era_background": era, "characters": [person(), person("c2")], "plot": plot([event()])}, target_version=3, progress=50)
    assert len(result["era_background"]["items"][0]["appearances"]) == 1
    stats = result["era_background"]["statistics"]
    assert stats["occurrence_count"] == 1 and stats["average_per_character"] == .5


def test_approval_cannot_promote_unverified_manual_fact(tmp_path):
    store = ReferenceAnalysisStore(tmp_path)
    candidate = {"id": "r1", "version_id": "v1", "document_id": "book", "candidates": [{"chunk_id": "c1", "index": 0, "start": 0, "end": 10,
        "status": "completed", "result": {"reference": {"plot": plot([event()])}}}]}
    with pytest.raises(DataError): store.commit_candidate(candidate, "c1", ["plot"], 0)
    assert store.get("book")["revision"] == 0


def test_projected_character_can_be_saved_without_derived_fields_becoming_storage(tmp_path):
    store = ReferenceAnalysisStore(tmp_path)
    result = store.save_section("book", "plot", plot([event()]), 0)
    result = store.save_section("book", "characters", [person(snapshots=[snapshot(event_ids=["e1"])])], result["revision"])
    view = store.project("book", target_version=3)
    assert view["sections"]["characters"][0]["current_snapshot"]
    saved = store.save_section("book", "characters", view["sections"]["characters"], result["revision"])
    assert "current_snapshot" not in saved["sections"]["characters"][0]
    assert "events" not in saved["sections"]["characters"][0]["snapshots"][0]


def test_catalog_callback_runs_in_reference_transaction_and_rolls_back(tmp_path):
    store = ReferenceAnalysisStore(tmp_path)
    def sync(db, document_id, sections):
        assert db.in_transaction
        raise DataError(409, "catalog conflict")
    store.catalog_sync = sync
    with pytest.raises(DataError): store.save_section("book", "plot", plot([event()]), 0)
    assert store.get("book")["revision"] == 0


def test_plot_read_view_can_save_without_derived_fields(tmp_path):
    store = ReferenceAnalysisStore(tmp_path)
    result = store.save_section("book", "plot", plot([event()]), 0)
    view = store.project("book", target_version=3)["sections"]["plot"]
    saved = store.save_section("book", "plot", view, result["revision"])["sections"]["plot"]
    assert "views" not in saved and "outlines" not in saved


def test_legacy_approval_after_v3_keeps_v3_shape(tmp_path):
    store = ReferenceAnalysisStore(tmp_path)
    current = store.save_section("book", "plot", plot([event()]), 0)
    legacy = {"schema_version": 2, "logline": "旧任务", "events": [{"id": "e2", "name": "归来", "description": "林青归来", "participants": [], "setting_ids": [],
        "causes": [], "results": [], "plotline_ids": [], "story_time": {"label": "", "sort_key": None}, "volume": None,
        "revealed_at": {"chapter": 1, "offset": 0}, "source_version_id": "v1", "evidence": "归来"}], "plotlines": []}
    candidate = {"id": "r1", "version_id": "v1", "document_id": "book", "candidates": [{"chunk_id": "c1", "index": 0, "start": 0, "end": 10,
        "status": "completed", "result": {"reference": {"plot": legacy}}}]}
    saved = store.commit_candidate(candidate, "c1", ["plot"], current["revision"])
    assert saved["sections"]["plot"]["schema_version"] == 3
    assert [item["id"] for item in saved["sections"]["plot"]["events"]] == ["e1", "e2"]
    validate_section("plot", saved["sections"]["plot"])


def test_approval_anchors_unique_quote_when_location_unspecified(tmp_path):
    docs = DocumentStore(tmp_path)
    document = docs.upload("plain.txt", ("甲" * 20 + "林青入城。" + "乙" * 75).encode())
    version = docs.get_version(document["id"])
    value = plot([event(progress=99, revealed_at={"chapter": None, "offset": None}, source_version_id=version["id"], evidence="林青入城。")])
    candidate = {"id": "r1", "version_id": version["id"], "document_id": document["id"], "candidates": [{"chunk_id": "c1", "index": 0, "start": 0, "end": len(version["text"]), "status": "completed", "result": {"reference": {"plot": value}}}]}
    saved = ReferenceAnalysisStore(tmp_path, documents=docs).commit_candidate(candidate, "c1", ["plot"], 0)
    assert saved["sections"]["plot"]["events"][0]["narrative_progress"] == 20


def test_progress_source_index_does_not_mutate_immutable_source():
    from inkreference.progress import prepare_progress_sources
    source = {"text": "甲" * 100000 + "\n" + "乙" * 100000, "chapters": []}
    original = copy.deepcopy(source)
    sources = prepare_progress_sources({"v1": source})
    assert narrative_progress(sources["v1"], {"offset": 100001}) == 50
    assert source == original and "_progress_index" not in source
    assert prepare_progress_sources(sources)["v1"] is sources["v1"]


def test_preface_not_counted_in_chapter_progress_and_placeholder_is_counted():
    source = {"text": "甲" * 40, "chapters": [{"kind": "preface", "number": None, "start": 0, "end": 10},
        {"kind": "chapter", "number": 1, "raw_marker": "第1章", "start": 10, "body_start": 11, "end": 20},
        {"kind": "chapter", "number": 2, "placeholder": True, "raw_marker": "第2章", "start": 20, "end": 20},
        {"kind": "chapter", "number": 3, "raw_marker": "第3章", "start": 20, "body_start": 21, "end": 40}]}
    assert narrative_progress(source, {"chapter": 1, "offset": 12}) == pytest.approx(100 / 3, abs=1e-6)
    assert narrative_progress(source, {"chapter": 2, "offset": None}) == pytest.approx(200 / 3, abs=1e-6)


def test_legacy_language_projects_to_complete_v3_editor_contract():
    original = {"style": {"dialogue_ratio": .2, "vocab_complexity": .7}, "rhythm": {"coverage": {"chapters": 10}}, "opening": {"pattern": "动作"}}
    result = project_sections(original, target_version=3)
    for name in ("style", "rhythm", "opening"): validate_section(name, result[name])
    assert result["style"]["legacy"] == original["style"]
    assert "vocab_complexity" not in result["style"]
    assert result["opening"]["pattern"] == "动作"
    assert original["style"].get("schema_version") is None


def test_out_of_order_source_character_approvals_choose_earliest_initial_state():
    from inkreference.reference_analysis import merge_named
    later = person(origin="source", initial_state={**person()["initial_state"], "narrative_progress": 60, "identity": "队长"},
                   snapshots=[snapshot("later", 60, origin="source")])
    earlier = person(origin="source", initial_state={**person()["initial_state"], "narrative_progress": 20, "identity": "旅人"},
                     snapshots=[snapshot("earlier", 20, origin="source")])
    merged = merge_named([later], [earlier], "characters")[0]
    assert merged["initial_state"]["identity"] == "旅人"
    spans = {item["id"]: item["narrative_range"] for item in merged["snapshots"]}
    assert spans["earlier"] == {"start": 20, "end": 60}
    later["origin"] = "manual"
    assert merge_named([later], [earlier], "characters")[0]["initial_state"]["identity"] == "队长"


def test_legacy_chapter_progress_uses_unique_commit_version_without_new_proof(tmp_path):
    docs = DocumentStore(tmp_path)
    text = "\n".join(f"第{index}章 标题\n林青行路{index}。" for index in range(1, 101))
    document = docs.upload("book.txt", text.encode())
    version = docs.get_version(document["id"])
    store = ReferenceAnalysisStore(tmp_path, documents=docs)
    old = {"plot": {"logline": "旅途", "epochs": [{"title": "第一卷", "periods": [{"time": "旧城", "events": [{"name": "入城", "description": "林青入城", "first_chapter": "第20章"}]}]}]}}
    candidate = {"id": "r1", "version_id": version["id"], "document_id": document["id"], "candidates": [{"chunk_id": "c1", "index": 0,
        "start": 0, "end": len(text), "status": "completed", "result": {"reference": old}}]}
    saved = store.commit_candidate(candidate, "c1", ["plot"], 0)
    original = copy.deepcopy(saved["sections"])
    history = copy.deepcopy(store.list_history(document["id"]))
    view = store.project(document["id"], target_version=3)
    event_view = view["sections"]["plot"]["events"][0]
    assert event_view["narrative_progress"] == 20
    assert not {"revealed_at", "evidence", "source_version_id"} & event_view.keys()
    assert store.get(document["id"])["sections"] == original
    assert store.list_history(document["id"]) == history


def test_legacy_progress_does_not_guess_across_source_versions():
    old = {"plot": {"epochs": [{"title": "卷", "periods": [{"events": [{"name": "旧事", "first_chapter": "第1章"}]}]}]}}
    source = {"text": "甲" * 20, "chapters": [{"number": 1, "raw_marker": "第1章", "start": 0, "end": 20, "body_start": 2}]}
    result = project_sections(old, target_version=3, source_versions={"v1": source, "v2": source})
    assert result["plot"]["events"][0]["narrative_progress"] == 0
    result = project_sections(old, target_version=3, source_versions={"v1": source}, legacy_source_version_id="")
    assert result["plot"]["events"][0]["narrative_progress"] == 0


def test_background_reference_write_waits_before_sql_for_shared_workspace_lock(tmp_path):
    import threading
    from concurrent.futures import ThreadPoolExecutor
    store = ReferenceAnalysisStore(tmp_path)
    entered = threading.Event()
    store.catalog_sync = lambda db, document_id, sections: entered.set()
    with ThreadPoolExecutor(max_workers=1) as executor:
        with store.write_lock:
            future = executor.submit(store.save_section, "book", "plot", plot([event()]), 0)
            assert not entered.wait(.05)
        assert future.result(timeout=2)["revision"] == 1
    assert entered.is_set()
