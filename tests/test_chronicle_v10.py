"""Synthetic strict-schema, disclosure, identity and language regression tests."""
import copy

import pytest

from inkreference.chronicle_schema import canonical_stage_schema, validate_stage_result, validate_v2_section
from inkreference.database import DataError
from inkreference.analysis_projection import project_sections
from inkreference.language_analysis import compute_language_statistics, era_statistics
from inkreference.reference_analysis import ReferenceAnalysisStore, merge_named, validate_reference

SOURCE = "林青入城。\n林青加入守城会。\n林青失去钥匙。\n真相是他早已是卧底。"


def anchor(chapter=1, quote="林青入城。"):
    return {"revealed_at": {"chapter": chapter, "offset": SOURCE.index(quote)}, "source_version_id": "v1", "evidence": quote}


def state(identity="s1", value="守卫", chapter=1, attribute="identity", **changes):
    return {"id": identity, "attribute": attribute, "value": value, "change_type": "addition", "supersedes": [],
            "event_id": None, "setting_ids": [], "character_ids": [], "story_time": {"label": "", "sort_key": None}, **anchor(chapter), **changes}


def event(identity="e1", chapter=1, story="2033-01", **changes):
    return {"id": identity, "name": "入城", "description": "林青进入旧城", "participants": [], "setting_ids": [], "causes": [],
            "results": [], "plotline_ids": [], "story_time": {"label": story or "", "sort_key": story}, "volume": "第一卷", **anchor(chapter), **changes}


def character(identity="c1", **changes):
    return {"schema_version": 2, "id": identity, "name": "林青", "intro": "末章揭露的卧底", "temperament": "未来性格", "visual_design": "末章白发",
            "states": [state()], "experiences": [], **anchor(), **changes}


def plot(events=None, **changes):
    return {"schema_version": 2, "logline": "全书未来结局", "events": events or [], "plotlines": [], **changes}


def test_v2_stage_contract_and_exact_evidence_are_fixed():
    result = validate_stage_result("plot", {"plot": plot([event()])}, SOURCE, "v1")
    assert result["plot"]["events"][0]["id"] == "e1"
    assert canonical_stage_schema("characters")["additionalProperties"] is False
    for mutate in (lambda data: data["plot"].update(unknown=True),
                   lambda data: data["plot"]["events"][0].update(evidence="不存在"),
                   lambda data: data["plot"]["events"][0]["revealed_at"].update(offset=2),
                   lambda data: data["plot"]["events"][0].update(source_version_id="other")):
        bad = copy.deepcopy(result)
        mutate(bad)
        with pytest.raises(DataError): validate_stage_result("plot", bad, SOURCE, "v1")


def test_unknown_time_and_ids_do_not_become_guesses():
    data = plot([event(story=None)])
    assert validate_v2_section("plot", data)["events"][0]["story_time"] == {"label": "", "sort_key": None}
    data["events"][0]["story_time"]["sort_key"] = "2033"
    with pytest.raises(DataError, match="未知故事时间"): validate_v2_section("plot", data)
    bad = plot([event(participants=["invented"] )])
    with pytest.raises(DataError, match="未知 ID"): validate_stage_result("plot", {"plot": bad}, SOURCE)
    validate_stage_result("plot", {"plot": bad}, SOURCE, known_entities={"characters": [{"id": "invented"}]})


def test_duplicate_ids_and_causal_cycles_rejected():
    with pytest.raises(DataError, match="ID 重复"):
        validate_v2_section("plot", plot([event(), event()]))
    with pytest.raises(DataError, match="循环"):
        validate_stage_result("plot", {"plot": plot([event(results=["e2"]), event("e2", results=["e1"])])}, SOURCE)


def test_identity_merge_does_not_collapse_namesakes_or_rename_ids():
    first = character()
    second = character("c2")
    merged = merge_named([first], [second, character(states=[state("s2", "队长", chapter=2)])], "characters")
    assert [item["id"] for item in merged] == ["c1", "c2"]
    assert [item["id"] for item in merged[0]["states"]] == ["s1", "s2"]
    renamed = character(name="林青的新称呼")
    assert len(merge_named(merged, [renamed], "characters")) == 2


def test_two_clocks_links_and_outlines_are_generated_from_events():
    events = [event("early", 4, "2000", causes=[]), event("late", 1, "2033", causes=["early"])]
    original = {"plot": plot(events)}
    full = project_sections(original)
    assert [e["id"] for e in full["plot"]["views"]["story"]] == ["early", "late"]
    assert [e["id"] for e in full["plot"]["views"]["narrative"]] == ["late", "early"]
    assert full["plot"]["outlines"]["volumes"][0]["event_ids"] == ["late", "early"]
    before = project_sections(original, 1)
    assert [e["id"] for e in before["plot"]["events"]] == ["late"]
    assert before["plot"]["events"][0]["causes"] == []
    assert "未来" not in before["plot"]["logline"]
    assert original["plot"]["events"][1]["causes"] == ["early"]


def test_state_revelations_correct_prior_knowledge_without_erasing_formal_history(tmp_path):
    person = character(states=[state(), state("s2", "卧底", 4, change_type="revelation", supersedes=["s1"])])
    store = ReferenceAnalysisStore(tmp_path)
    saved = store.save_section("book", "characters", [person], 0)
    assert store.project("book", 1)["sections"]["characters"][0]["current_state"]["identity"]["value"] == "守卫"
    assert store.project("book", 4)["sections"]["characters"][0]["current_state"]["identity"]["value"] == "卧底"
    assert store.project("book", 1)["sections"]["characters"][0]["intro"] == ""
    for i in range(15): saved = store.save_section("book", "metadata", {"edit": i}, saved["revision"])
    assert len(store.list_history("book")) == 10
    assert len(store.get("book")["sections"]["characters"][0]["states"]) == 2


def test_experiences_are_linked_to_visible_events_and_unknown_anchors_do_not_leak():
    exp = {"event_id": "e1", "changes": [state("s2", "队长", 2)], **anchor(2)}
    person = character(experiences=[exp])
    sections = {"plot": plot([event(chapter=2)]), "characters": [person, character("unknown", revealed_at={"chapter": None,"offset": None})]}
    early = project_sections(sections, 1)
    assert len(early["characters"]) == 1 and early["characters"][0]["experiences"] == []
    later = project_sections(sections, 2)
    assert later["characters"][0]["experiences"][0]["event"]["id"] == "e1"
    assert later["characters"][0]["current_state"]["identity"]["value"] == "队长"


def test_language_statistics_have_explicit_counts_and_no_vocabulary_score():
    result = compute_language_statistics("甲乙。\n丙丁！\n\n戊己？", source_version_id="v2", chapter_count=1)
    assert result["sentence_length"]["count"] == 3
    assert result["paragraph_length"]["mean"] == 3
    assert result["punctuation_frequency"]["question"]["count"] == 1
    assert result["coverage"] == {"characters": 9, "chapters": 1}
    assert "vocab_complexity" not in result


def test_era_character_frequencies_use_dialogue_thought_and_include_zero_users():
    common = {"character_id": "c1", "context": "使用热梗", "usage": "dialogue", **anchor()}
    narration = {**common, "usage": "narration", "revealed_at": {"chapter": 2, "offset": 12}}
    era = {"items": [{"id": "m1", "kind": "meme", "occurrences": [common, copy.deepcopy(common), narration]}]}
    stats = era_statistics(era, [character(), character("c2")], character_count=10000)
    assert stats["occurrence_count"] == stats["per_10000_chars"] == 2
    assert stats["highest_characters"] == [{"character_id": "c1", "name": "林青", "count": 1}]
    assert stats["average_per_character"] == .5


def test_v2_style_and_era_disclosures_remove_future_global_summaries():
    sections = {"style": {"schema_version": 2, "dialogue_style": "末章人物死后变调", "observations": [
        {"aspect": "视角", "finding": "近距离第三人称", **anchor(1)}, {"aspect": "视角", "finding": "全知", **anchor(20)}]},
        "era_background": {"schema_version": 2, "items": [{"id": "m1", "expression": "合成热梗", "occurrences": [{"character_id": None,"usage":"narration", **anchor(20)}]}]}}
    earlier = project_sections(sections, 1)
    assert "dialogue_style" not in earlier["style"]
    assert len(earlier["style"]["observations"]) == 1
    assert earlier["era_background"]["items"] == []


def language():
    return {"schema_version": 2, "narrative_person": "第三人称", "focalization": "有限", "register": "口语", "colloquialism": "自然",
            "rhetoric": [], "dialogue_style": "简短", "catchphrases": [], "naming": {"patterns": [], "style": "中文", "examples": []},
            "narrative_composition": [], "observations": []}


def candidate(reference, chunk="c1"):
    return {"id": "run", "version_id": "v1", "document_id": "book", "candidates": [{"chunk_id": chunk, "index": 0,
            "start": 0, "end": len(SOURCE), "status": "completed", "result": {"reference": reference}}]}


def test_v2_approval_preserves_legacy_and_does_not_override_stage_order(tmp_path):
    class Documents:
        def get_document(self, document_id): return {"id": document_id}
        def get_version(self, document_id, version_id): return {"text": SOURCE, "chapters": [1,2,3,4]}
    store = ReferenceAnalysisStore(tmp_path, documents=Documents())
    old = store.save_section("book", "style", {"dialogue_ratio": .4}, 0)
    rhythm = {"schema_version": 2, "chapter_analysis": [{"chapter": 1, "climax": "入城", "payoffs": [], "foreshadowing": [], "pacing": "慢", "emotion": "好奇", **anchor()}]}
    rich = {"plot": plot([event()]), "style": language(), "rhythm": rhythm, "opening": {"schema_version": 2, "pattern": "行动", "observations": []}, "era_background": {"schema_version": 2, "items": []}}
    current = store.commit_candidate(candidate(rich), "c1", ["rhythm", "opening", "style", "plot", "era_background"], old["revision"])
    assert current["sections"]["style"]["legacy"] == {"dialogue_ratio": .4}
    assert current["sections"]["rhythm"] == rhythm
    assert current["sections"]["style"]["deterministic_statistics"]["coverage"]["chapters"] == 4
    assert store.commit_candidate(candidate(rich), "c1", ["style"], 0) == current
    # A displayed read/edit roundtrip retains legacy JSON and computed counts.
    changed = store.save_section("book", "style", current["sections"]["style"], current["revision"])
    assert changed["sections"]["style"] == current["sections"]["style"]
    validate_reference({"plot": current["sections"]["plot"], "style": current["sections"]["style"]})


def test_later_setting_stage_reverse_links_characters_without_extra_model_pass():
    joining = state("join", "林青加入守城会", 2, attribute="membership", character_ids=["c1"], event_id="e1")
    setting = {"schema_version": 2, "id": "g1", "title": "守城会", "category": "factions", "content": "守城组织", "states": [joining], **anchor(2)}
    person = character(experiences=[{"event_id": "e1", "changes": [], **anchor(2)}])
    sections = {"plot": plot([event(chapter=2)]), "characters": [person], "settings": [setting]}
    projected = project_sections(sections)
    assert projected["characters"][0]["setting_links"][0]["setting_id"] == "g1"
    assert projected["plot"]["events"][0]["participants"] == ["c1"]
    assert projected["plot"]["events"][0]["setting_ids"] == ["g1"]
    assert project_sections(sections, 1)["characters"][0]["setting_links"] == []


def runtime_character(docs, document_id):
    version = docs.get_version(document_id)
    quote = "林青入城。"
    offset = version["text"].index(quote)
    grounded = {"source_version_id": version["id"], "evidence": quote, "revealed_at": {"chapter": 1, "offset": offset}}
    return character(states=[state(**grounded)], **grounded)


@pytest.mark.parametrize("problem", ["quote", "offset", "chapter", "version", "blank_version", "nested_quote"])
def test_runtime_v2_manual_evidence_is_grounded_and_failure_is_transactional(tmp_path, problem):
    from inkreference.documents import DocumentStore
    docs = DocumentStore(tmp_path)
    doc = docs.upload("book.txt", "第1章 入城\n林青入城。\n第2章 离城\n林青离城。".encode())
    store = ReferenceAnalysisStore(tmp_path, documents=docs)
    person = runtime_character(docs, doc["id"])
    saved = store.save_section(doc["id"], "characters", [person], 0)
    changed = copy.deepcopy(person)
    if problem == "quote": changed["evidence"] = "原文中没有的句子"
    elif problem == "offset": changed["revealed_at"]["offset"] += 1
    elif problem == "chapter": changed["revealed_at"]["chapter"] = 2
    elif problem == "version": changed["source_version_id"] = "missing-version"
    elif problem == "blank_version": changed["source_version_id"] = ""
    else: changed["states"][0]["evidence"] = "不存在的嵌套证据"
    history = store.list_history(doc["id"])
    with pytest.raises(DataError) as error:
        store.save_section(doc["id"], "characters", [changed], saved["revision"])
    assert error.value.status_code == 422
    assert store.get(doc["id"]) == saved
    assert store.list_history(doc["id"]) == history


def test_runtime_v2_evidence_cannot_borrow_another_document_version(tmp_path):
    from inkreference.documents import DocumentStore
    docs = DocumentStore(tmp_path)
    first = docs.upload("one.txt", "第1章 入城\n林青入城。".encode())
    other = docs.upload("two.txt", "第1章 入城\n林青入城。".encode())
    person = runtime_character(docs, other["id"])
    store = ReferenceAnalysisStore(tmp_path, documents=docs)
    with pytest.raises(DataError, match="不属于当前作品"):
        store.save_section(first["id"], "characters", [person], 0)


def test_v2_id_references_prevent_dangling_manual_edits_transactionally(tmp_path):
    store = ReferenceAnalysisStore(tmp_path)
    saved = store.save_section("book", "plot", plot([event()]), 0)
    saved = store.save_section("book", "characters", [character(states=[state(event_id="e1")])], saved["revision"])
    with pytest.raises(DataError, match="未知 ID"):
        store.save_section("book", "plot", plot([]), saved["revision"])
    assert store.get("book") == saved


def test_http_v2_evidence_validation_and_legacy_manual_note_compatibility(tmp_path):
    from fastapi.testclient import TestClient
    from inkreference.api import create_app
    app = create_app(data_dir=tmp_path, static_dir=tmp_path / "absent")
    with TestClient(app, base_url="http://127.0.0.1") as client:
        doc = client.post("/api/studio/documents", files={"file": ("book.txt", "第1章 入城\n林青入城。".encode(), "text/plain")}).json()
        person = runtime_character(app.state.runtime.studio.documents, doc["id"])
        # Interpretation can be edited freely; only claimed original evidence is grounded.
        person["intro"] = "人工对角色的解读，不要求逐字出现在小说中。"
        path = f"/api/studio/documents/{doc['id']}/reference"
        valid = client.put(path + "/characters", json={"data": [person], "expected_revision": 0})
        assert valid.status_code == 200, valid.text
        bad = copy.deepcopy(person)
        bad["states"][0]["revealed_at"]["offset"] = 0
        rejected = client.put(path + "/characters", json={"data": [bad], "expected_revision": 1})
        assert rejected.status_code == 422
        assert client.get(path).json()["revision"] == 1
        legacy = client.put(path + "/settings", json={"data": [{"title": "人工笔记", "content": "无需原文引文"}], "expected_revision": 1})
        assert legacy.status_code == 200, legacy.text


def test_era_real_world_event_mentions_do_not_inflate_character_meme_usage():
    meme = {"character_id": "c1", "context": "说出网络热梗", "usage": "dialogue", **anchor()}
    news = {"character_id": "c2", "context": "讨论真实事件", "usage": "thought", **anchor(2)}
    era = {"items": [{"id": "m1", "kind": "meme", "occurrences": [meme]},
                     {"id": "n1", "kind": "real_world_event", "occurrences": [news]}]}
    stats = era_statistics(era, [character(), character("c2"), character("c3")], character_count=10000)
    assert stats["occurrence_count"] == 2
    assert stats["items"] == [{"id": "m1", "count": 1, "per_10000_chars": 1}, {"id": "n1", "count": 1, "per_10000_chars": 1}]
    assert stats["highest_characters"] == [{"character_id": "c1", "name": "林青", "count": 1}]
    assert stats["average_per_character"] == round(1 / 3, 4)
    assert [item["count"] for item in stats["character_usage"]] == [1, 0, 0]
    without_meme = era_statistics({"items": [era["items"][1]]}, [character("c2"), character("c3")])
    assert without_meme["highest_characters"] == []
    assert without_meme["average_per_character"] == 0
    assert without_meme["occurrence_count"] == 1
