"""Catalog intake uses invented works and HTTP fixtures, never live providers."""
from __future__ import annotations

import copy
import json

import httpx
import pytest

from inkreference.database import DataError, DataStore
from inkreference.models import ModelService
from inkreference.research import ResearchService
from inkreference.semantic_search import embedding_target
from test_research_v11 import URL, SOURCE, draft, make_service, public_resolver


def offline_service(tmp_path):
    requests=[]
    def forbidden(request):
        requests.append(request)
        raise AssertionError("offline intake attempted a provider request")
    models=ModelService(tmp_path,transport=httpx.MockTransport(forbidden))
    catalog=DataStore(tmp_path/"inkreference.sqlite3")
    catalog.create_record("works",{"作品ID":"W1","作品":"合成小说","作者":"测试作者","类别":"中文小说","剧情梗概":"原有梗概"})
    catalog.create_record("works",{"作品ID":"W2","作品":"合成小说","作者":"另一作者","类别":"中文小说"})
    catalog.create_record("cliches",{"clichéID":"C1","cliché内容":"弱小者帮助他人换来伙伴"})
    service=ResearchService(tmp_path,models,catalog_snapshot=catalog.catalog_snapshot,catalog_commit=catalog.apply_research,transport=httpx.MockTransport(forbidden),resolver=public_resolver)
    return service,catalog,requests


def response(job,*,candidate=None,suggestions=None):
    return {"protocol":"inkreference.catalog.v1","request_id":job["id"],"sources":[{"id":SOURCE,"url":URL,"title":"合成小说 测试作者","excerpt":"合成小说，作者测试作者。主角旅人身着蓝衣，救援陌生人获得同伴。"}],"draft":copy.deepcopy(candidate or draft()),"suggested_matches":suggestions or {"work_id":"W1","cliches":{"t1":"C1"}}}


def paste(service,job,value=None,**options):
    return service.reply(job["id"],reply=json.dumps(value or response(job),ensure_ascii=False),expected_revision=job["revision"],**options)


def approval(job,*,manual=True,action="reuse"):
    return {"expected_revision":job["revision"],"expected_catalog_revision":job["catalog_revision"],"decisions":{"work_id":"W1","manual_review_confirmed":manual,"characters":{"hero":{"action":"new"}},"cliches":{"t1":{"action":action,**({"id":"C1"} if action=="reuse" else {})}}}}


def test_semi_prompt_and_paste_need_no_model_search_or_embedding(tmp_path):
    service,catalog,requests=offline_service(tmp_path);before=catalog.catalog_snapshot()
    job=service.create_semi(title="合成小说",author="测试作者")
    assert job["mode"]=="semi" and job["status"]=="awaiting_reply"
    assert job["work_matches"]==[{"id":"W1","title":"合成小说","author":"测试作者","category":"中文小说","publication_date":""}]
    assert "inkreference.catalog.v1" in job["prompt"] and job["id"] in job["prompt"]
    assert "W1" in job["prompt"] and "C1" in job["prompt"]
    pasted="```json\n"+json.dumps(response(job),ensure_ascii=False)+"\n```"
    job=service.reply(job["id"],reply=pasted,expected_revision=job["revision"])
    assert job["status"]=="needs_review" and job["dedup_mode"]=="manual"
    assert job["sources"][0]["fetch_status"]=="supplied" and job["sources"][0]["fetched_at"] is None
    match=job["semantic_matches"][0]
    assert match["method"]=="manual" and match["matches"][0]["similarity"] is None
    assert match["matches"][0]["match_kind"]=="suggested"
    assert job["usage"]["calls"]==0 and not requests
    assert catalog.catalog_snapshot()==before
    with pytest.raises(DataError,match="人工核对"):
        service.approve(job["id"],**approval(job,manual=False))
    job=service.approve(job["id"],**approval(job))
    assert job["status"]=="approved" and job["commit_result"]["work_id"]=="W1"
    assert catalog.stats()["works"]==2 and catalog.stats()["cliches"]==1 and catalog.stats()["characters"]==1
    assert catalog.get_record("works","W1")["values"]["剧情梗概"]==draft()["work"]["synopsis"]
    assert not requests


@pytest.mark.parametrize("change",["wrong_request","wrong_protocol","unknown_source","wrong_author","invented_cliche","duplicate_source","claimed_verified","body_injection"])
def test_semi_rejects_conflicting_references_and_unknown_fields_atomically(tmp_path,change):
    service,catalog,requests=offline_service(tmp_path);before=catalog.catalog_snapshot()
    job=service.create_semi(title="合成小说",author="测试作者");value=response(job)
    if change=="wrong_request":value["request_id"]="another_job"
    if change=="wrong_protocol":value["protocol"]="execute-code"
    if change=="unknown_source":value["draft"]["characters"][0]["source_ids"]=["hallucinated"]
    if change=="wrong_author":value["draft"]["work"]["author"]="另一作者"
    if change=="invented_cliche":value["suggested_matches"]["cliches"]["t1"]="C_missing"
    if change=="duplicate_source":value["sources"].append(copy.deepcopy(value["sources"][0]))
    if change=="claimed_verified":value["sources"][0]["fetch_status"]="verified"
    if change=="body_injection":value["draft"]["work"]["body"]="不得写入的小说正文"
    with pytest.raises(DataError):paste(service,job,value)
    assert catalog.catalog_snapshot()==before and service.get(job["id"])["status"]=="awaiting_reply"
    assert not requests


@pytest.mark.parametrize("url",["file:///etc/passwd","javascript:alert(1)","http://127.0.0.1/x","http://[::1]/x","https://localhost/x","https://example.local/x","https://user:pass@example.test/x"])
def test_supplied_sources_reject_local_or_executable_urls_without_network(tmp_path,url):
    service,_,requests=offline_service(tmp_path);job=service.create_semi(title="合成小说",author="测试作者")
    value=response(job);value["sources"][0]["url"]=url
    with pytest.raises(DataError,match="公开HTTP"):
        paste(service,job,value)
    assert not requests


def test_json_duplicate_keys_and_non_finite_constants_rejected(tmp_path):
    service,_,requests=offline_service(tmp_path);job=service.create_semi(title="合成小说",author="测试作者")
    for reply in ('{"protocol":1,"protocol":2}', '{"value":NaN}', '说明文字 {"value":1}'):
        with pytest.raises(DataError):service.reply(job["id"],reply=reply,expected_revision=job["revision"])
    assert not requests


def test_semi_draft_edits_recheck_catalog_and_manual_reuse_without_vector_claim(tmp_path):
    service,catalog,requests=offline_service(tmp_path);job=service.create_semi(title="合成小说",author="测试作者")
    job=paste(service,job);edited=copy.deepcopy(job["draft"]);edited["work"]["synopsis"]="用户修订梗概"
    job=service.save_draft(job["id"],edited,job["revision"])
    assert job["status"]=="needs_review" and job["draft"]["work"]["synopsis"]=="用户修订梗概"
    before=catalog.catalog_snapshot();bad=approval(job);bad["decisions"]["cliches"]["t1"]["id"]="missing"
    with pytest.raises(DataError):service.approve(job["id"],**bad)
    assert catalog.catalog_snapshot()==before and service.get(job["id"])["status"]=="needs_review"
    assert not requests


def test_duplicate_exact_cliche_cannot_be_added_from_offline_reply(tmp_path):
    service,catalog,_=offline_service(tmp_path);job=service.create_semi(title="合成小说",author="测试作者")
    candidate=draft();candidate["cliches"][0]["content"]="弱小者帮助他人换来伙伴"
    job=paste(service,job,response(job,candidate=candidate))
    assert job["semantic_matches"][0]["recommendation"]=="reuse"
    params=approval(job,action="new");params["decisions"]["cliches"]["t1"]["acknowledge_similar"]=True
    before=catalog.catalog_snapshot()
    with pytest.raises(DataError,match="相同桥段"):service.approve(job["id"],**params)
    assert catalog.catalog_snapshot()==before


def test_identity_false_cannot_approve_even_with_manual_confirmation(tmp_path):
    service,catalog,_=offline_service(tmp_path);job=service.create_semi(title="合成小说",author="测试作者")
    value=response(job);value["draft"]["identity"]["confirmed"]=False
    job=paste(service,job,value);before=catalog.catalog_snapshot()
    with pytest.raises(DataError,match="身份尚不明确"):service.approve(job["id"],**approval(job))
    assert catalog.catalog_snapshot()==before


def test_pasted_source_excerpt_must_contain_both_title_and_author(tmp_path):
    service,_,_=offline_service(tmp_path);job=service.create_semi(title="合成小说",author="测试作者")
    value=response(job);value["sources"][0]["title"]="合成小说";value["sources"][0]["excerpt"]="没有作者的网页摘要"
    with pytest.raises(DataError,match="作品名与作者"):paste(service,job,value)


def test_semi_source_verification_really_fetches_and_preserves_external_source_ids(tmp_path):
    service,create,calls,_,catalog=make_service(tmp_path)
    catalog["works"]=[{"id":"W1","values":{"作品":"合成小说","作者":"测试作者"}}]
    job=service.create_semi(title="合成小说",author="测试作者")
    value=response(job);value["sources"][0]["id"]="web_1"
    for item in [value["draft"]["work"],*value["draft"]["characters"],*value["draft"]["cliches"]]:item["source_ids"]=["web_1"]
    value["draft"]["work"]["discussion_heat"]["source_ids"]=["web_1"]
    job=paste(service,job,value,verify_sources=True)
    for thread in service.active_threads():thread.join(5)
    job=service.get(job["id"])
    assert job["status"]=="needs_review",job
    assert job["sources"][0]["id"]=="web_1" and job["sources"][0]["fetch_status"]=="verified"
    assert any(r.url.host=="catalog.example.test" for r in calls)
    assert not any(r.url.host=="search.example.test" or r.url.path.endswith("chat/completions") for r in calls)
    assert job["dedup_mode"]=="manual" and job["usage"]["calls"]==0


def test_optional_semi_embeddings_are_real_and_require_target_consent(tmp_path):
    service,_,calls,_,catalog=make_service(tmp_path)
    catalog["works"]=[{"id":"W1","values":{"作品":"合成小说","作者":"测试作者"}}]
    job=service.create_semi(title="合成小说",author="测试作者")
    with pytest.raises(DataError,match="向量模型"):
        paste(service,job,embedding_profile_id="e")
    fp=embedding_target(service.models.snapshot("e"))
    job=paste(service,job,embedding_profile_id="e",remote_consent=True,expected_embedding_fingerprint=fp)
    for thread in service.active_threads():thread.join(5)
    job=service.get(job["id"])
    assert job["status"]=="needs_review" and job["dedup_mode"]=="vector"
    assert job["semantic_matches"][0]["matches"][0]["similarity"]>0.9
    assert sum(r.url.path.endswith("embeddings") for r in calls)==1
    assert not any(r.url.path.endswith("chat/completions") or r.url.host=="search.example.test" for r in calls)


def automatic_fixture(tmp_path,resolution):
    service,create,calls,commits,catalog=make_service(tmp_path)
    catalog["works"]=[{"id":"W1","values":{"作品":"合成小说","作者":"测试作者","类别":"中文小说","发布日期":"2020"}},{"id":"OTHER","values":{"作品":"合成小说","作者":"另一作者"}}]
    original=service.models.transport
    def handler(request):
        if request.url.path.endswith("chat/completions") and "作品目录核对员" in json.loads(request.content)["messages"][0]["content"]:
            calls.append(request)
            return httpx.Response(200,json={"choices":[{"message":{"content":json.dumps(resolution,ensure_ascii=False)},"finish_reason":"stop"}],"usage":{"prompt_tokens":100,"completion_tokens":30}})
        return original.handle_request(request)
    service.models.transport=httpx.MockTransport(handler)
    return service,create,calls,commits,catalog


def test_automatic_model_resolution_selects_existing_author_and_waits_for_review(tmp_path):
    service,create,calls,commits,_=automatic_fixture(tmp_path,{"action":"update","work_id":"W1","reason":"同名同作者且唯一版本"})
    job=service.start(create(mode="automatic")["id"],background=False)
    assert job["status"]=="needs_review",job
    assert job["work_resolution"]["work_id"]=="W1" and job["target_work_id"]=="W1"
    assert job["progress"]["total"]==6 and job["usage"]["calls"]==3
    assert not commits
    model_calls=[r for r in calls if r.url.path.endswith("chat/completions")]
    assert len(model_calls)==2
    identity=json.loads(model_calls[0].content)["messages"][1]["content"]
    assert '"id":"W1"' in identity and '"id":"OTHER"' not in identity


def test_automatic_wrong_author_id_is_rejected_before_search_or_catalog_write(tmp_path):
    service,create,calls,commits,_=automatic_fixture(tmp_path,{"action":"update","work_id":"OTHER","reason":"错误地合并同名作品"})
    job=service.start(create(mode="automatic")["id"],background=False)
    assert job["status"]=="failed" and "作者" in job["error"]
    assert not any(r.url.host=="search.example.test" for r in calls) and not commits


def test_automatic_multiple_editions_always_require_manual_selection(tmp_path):
    service,create,_,commits,catalog=automatic_fixture(tmp_path,{"action":"update","work_id":"W1","reason":"模型猜测版本"})
    catalog["works"].append({"id":"W3","values":{"作品":"合成小说","作者":"测试作者","类别":"电影","发布日期":"2025"}})
    job=service.start(create(mode="automatic")["id"],background=False)
    assert job["status"]=="needs_review" and job["work_resolution"]["action"]=="review"
    assert job["target_work_id"] is None and len(job["work_matches"])==2 and not commits


def test_semiautomatic_http_routes_remain_lazy_and_need_no_model_settings(tmp_path,monkeypatch):
    from fastapi import FastAPI
    from fastapi.responses import JSONResponse
    from fastapi.testclient import TestClient
    from inkreference.research_api import create_research_router
    service,catalog,requests=offline_service(tmp_path)
    def forbidden(*args,**kwargs):raise AssertionError("semi read provider settings")
    monkeypatch.setattr(service.sources,"settings",forbidden)
    monkeypatch.setattr(service.models,"snapshot",forbidden)
    opened=[]
    def getter():opened.append(True);return service
    app=FastAPI();app.include_router(create_research_router(getter))
    @app.exception_handler(DataError)
    def error_handler(request,exc):return JSONResponse({"detail":exc.detail},status_code=exc.status_code)
    assert not opened
    with TestClient(app) as client:
        created=client.post("/api/studio/research/semi/prompt",json={"title":"合成小说","author":"测试作者"})
        assert created.status_code==200,created.text
        job=created.json()
        parsed=client.post(f"/api/studio/research/jobs/{job['id']}/reply",json={"reply":json.dumps(response(job),ensure_ascii=False),"expected_revision":job["revision"]})
        assert parsed.status_code==200,parsed.text
        job=parsed.json()
        summaries=client.get("/api/studio/research/jobs").json()["items"]
        assert summaries[0]["mode"]=="semi" and summaries[0]["target_work_id"] is None
        assert "draft" not in summaries[0] and "sources" not in summaries[0]
        approved=client.post(f"/api/studio/research/jobs/{job['id']}/approve",json=approval(job))
        assert approved.status_code==200,approved.text
        assert approved.json()["status"]=="approved"
    assert not requests


def test_http_automatic_creation_defaults_to_new_model_identity_workflow(tmp_path,monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from inkreference.research_api import create_research_router
    service,_,_=offline_service(tmp_path);seen=[]
    def create(**values):seen.append(values);return {"mode":values["mode"]}
    monkeypatch.setattr(service,"create",create)
    app=FastAPI();app.include_router(create_research_router(service))
    with TestClient(app) as client:
        result=client.post("/api/studio/research/jobs",json={"title":"合成小说","author":"测试作者","model_profile_id":"m","embedding_profile_id":"e"})
        assert result.status_code==200 and result.json()["mode"]=="automatic"
        invalid=client.post("/api/studio/research/jobs",json={"title":"合成小说","author":"测试作者","model_profile_id":"m","embedding_profile_id":"e","mode":"legacy"})
        assert invalid.status_code==422
    assert len(seen)==1


def test_offline_review_can_resume_after_pause_without_provider_calls(tmp_path):
    service,_,requests=offline_service(tmp_path);job=service.create_semi(title="合成小说",author="测试作者")
    job=paste(service,job);service.cancel(job["id"],pause=True)
    job=service.start(job["id"],background=False)
    assert job["status"]=="needs_review" and not requests


def test_semi_reuses_bound_character_and_preserves_original_source_snapshots(tmp_path):
    from inkreference.catalog_links import CatalogLinks
    from inkreference.character_bridge_v11 import CharacterBridge
    from inkreference.character_contract import normalize_character
    from inkreference.documents import DocumentStore
    from inkreference.reference_analysis import ReferenceAnalysisStore
    service,catalog,requests=offline_service(tmp_path)
    docs=DocumentStore(tmp_path);reference=ReferenceAnalysisStore(tmp_path,documents=docs)
    book=docs.upload("source.txt","第1章 旅途\n旅人从学徒成为医师。".encode())
    CatalogLinks(catalog,docs,lambda:reference).link(book["id"],expected_work_id=None,expected_version_id=book["latest_version_id"],work_id="W1")
    original=normalize_character({"schema_version":3,"id":"extracted-hero","name":"旅人","aliases":["行者"],"role_tag":"主角","temperament":"利他","visual_design":"蓝色衣服","initial_state":{"identity":"学徒"},"snapshots":[{"id":"phase","narrative_range":{"start":20,"end":None},"story_time":"次年","event_ids":[],"identity":"医师","ability":"急救","personality":"沉稳","appearance":"白发","visual_design":"白衣","relationships":[],"origin":"manual"}]})
    state=reference.save_section(book["id"],"characters",[original],0)
    bridge=CharacterBridge(catalog,docs,lambda:reference)
    reference.catalog_sync=bridge.sync_bound_from_reference_connection
    catalog.character_sync=bridge.sync_identity_from_catalog_connection
    bridge.import_character(book["id"],"extracted-hero",character_id="global-hero",expected_reference_revision=state["revision"],expected_catalog_revision=catalog.catalog_snapshot()["revision"])
    before=bridge.contexts("global-hero")["contexts"][0];source_before=reference.get(book["id"])
    job=service.create_semi(title="合成小说",author="测试作者",target_work_id="W1")
    job=paste(service,job)
    assert job["character_matches"][0]["matches"][0]["id"]=="global-hero"
    params=approval(job);params["decisions"]["characters"]["hero"]={"action":"reuse","id":"global-hero"}
    service.approve(job["id"],**params)
    assert bridge.contexts("global-hero")["contexts"][0]==before
    assert reference.get(book["id"])==source_before and not requests


def test_automatic_identity_requires_fetched_text_not_search_result_title(tmp_path):
    service,create,_,commits,_=make_service(tmp_path)
    original=service.sources.transport
    def handler(request):
        if request.url.host=="catalog.example.test":
            return httpx.Response(200,text="<html><p>合成小说，另一作者。不同作品的资料，不应被搜索标题的错误作者覆盖。这里有足够长的公开介绍，但作者并不符合查询。</p></html>",headers={"Content-Type":"text/html;charset=utf-8"})
        return original.handle_request(request)
    service.sources.transport=httpx.MockTransport(handler)
    job=service.start(create()["id"],background=False)
    assert job["status"]=="failed" and "作者" in job["error"] and not commits


def comparison_pair(service):
    a=service.create_semi(title="合成小说",author="测试作者",target_work_id="W1")
    group=service.create_comparison(title="合成小说",author="测试作者",target_work_id="W1")
    group=service.add_comparison_job(group["id"],job_id=a["id"],expected_revision=group["revision"],label="网页模型 A")
    a=paste(service,a)
    b=service.create_semi(title="合成小说",author="测试作者",target_work_id="W1")
    group=service.add_comparison_job(group["id"],job_id=b["id"],expected_revision=group["revision"],label="网页模型 B")
    reply=response(b);reply["draft"]["work"]["synopsis"]="模型 B 的独立梗概。"
    reply["draft"]["work"]["discussion_heat"]["min"]=6;reply["draft"]["work"]["discussion_heat"]["max"]=8
    b=paste(service,b,reply)
    return service.get_comparison(group["id"]),a,b


def test_comparison_keeps_original_results_and_requires_an_independent_review_job(tmp_path):
    service,catalog,requests=offline_service(tmp_path);before=catalog.catalog_snapshot()
    group,a,b=comparison_pair(service)
    assert group["status"]=="open" and len(group["jobs"])==2
    assert {job["comparison_label"] for job in group["jobs"]}=={"网页模型 A","网页模型 B"}
    assert all(job["usage"]["calls"]==0 and job["score_scenarios"] for job in group["jobs"])
    with pytest.raises(DataError,match="先采纳"):
        service.save_draft(a["id"],a["draft"],a["revision"])
    with pytest.raises(DataError,match="先采纳"):
        service.approve(b["id"],**approval(b))
    review=service.compose_comparison(group["id"],source_job_id=a["id"],expected_revision=group["revision"])
    assert review["id"] not in {a["id"],b["id"]}
    assert review["comparison_role"]=="review" and review["source_job_id"]==a["id"]
    assert review["mode"]=="semi" and review["manual_review_required"]
    edited=copy.deepcopy(review["draft"]);edited["work"]["synopsis"]=b["draft"]["work"]["synopsis"]+"用户核对后合并。"
    review=service.save_draft(review["id"],edited,review["revision"])
    assert service.get(a["id"])["draft"]==a["draft"] and service.get(b["id"])["draft"]==b["draft"]
    assert catalog.catalog_snapshot()==before and not requests
    approved=service.approve(review["id"],**approval(review))
    assert approved["status"]=="approved"
    final=service.get_comparison(group["id"])
    assert final["status"]=="approved" and final["selected_job_id"]==review["id"]
    assert final["commit_result"]["work_id"]=="W1"
    assert catalog.get_record("works","W1")["values"]["剧情梗概"]==edited["work"]["synopsis"]
    assert service.get(a["id"])["draft"]==a["draft"] and service.get(b["id"])["draft"]==b["draft"]
    with pytest.raises(DataError):service.compose_comparison(group["id"],source_job_id=b["id"],expected_revision=final["revision"])
    assert catalog.stats()["cliches"]==1 and catalog.stats()["characters"]==1


def test_comparison_projected_scores_are_catalog_formula_not_model_generated_scores(tmp_path):
    service,catalog,_=offline_service(tmp_path);group,a,b=comparison_pair(service)
    scenario=group["jobs"][0]["score_scenarios"][0]
    assert scenario["assumption"]=="update" and scenario["work_id"]=="W1"
    assert scenario["proposed_work_heat"]==2.5 and scenario["missing_heat"]
    assert scenario["new_cliche_score"]=={"mid":71.4286,"min":66.6667,"max":75.0}
    expected=scenario["existing_matches"][0]["projected_score"]["mid"]
    review=service.compose_comparison(group["id"],source_job_id=a["id"],expected_revision=group["revision"])
    service.approve(review["id"],**approval(review))
    assert catalog.get_record("cliches","C1")["computed"]["cliche_score"]==expected


def test_comparison_revision_identity_and_group_membership_conflicts_are_rejected(tmp_path):
    service,catalog,_=offline_service(tmp_path);group,a,b=comparison_pair(service)
    before=catalog.catalog_snapshot()
    with pytest.raises(DataError,match="对照已修改"):
        service.add_comparison_job(group["id"],job_id=a["id"],expected_revision=1)
    other=service.create_comparison(title="另一作品",author="测试作者")
    with pytest.raises(DataError,match="作品名或作者"):
        service.add_comparison_job(other["id"],job_id=a["id"],expected_revision=other["revision"])
    same=service.create_comparison(title="合成小说",author="测试作者")
    with pytest.raises(DataError,match="其他对照"):
        service.add_comparison_job(same["id"],job_id=a["id"],expected_revision=same["revision"])
    with pytest.raises(DataError):service.compose_comparison(group["id"],source_job_id=a["id"],expected_revision=1)
    assert catalog.catalog_snapshot()==before


def test_only_latest_selected_review_can_commit_and_failed_commit_keeps_group_reviewable(tmp_path):
    service,catalog,_=offline_service(tmp_path);group,a,b=comparison_pair(service)
    first=service.compose_comparison(group["id"],source_job_id=a["id"],expected_revision=group["revision"])
    group=service.get_comparison(group["id"])
    selected=service.compose_comparison(group["id"],source_job_id=b["id"],expected_revision=group["revision"])
    before=catalog.catalog_snapshot()
    with pytest.raises(DataError,match="当前采纳"):
        service.approve(first["id"],**approval(first))
    assert catalog.catalog_snapshot()==before
    bad=approval(selected);bad["decisions"]["cliches"]["t1"]["id"]="missing"
    with pytest.raises(DataError):service.approve(selected["id"],**bad)
    group=service.get_comparison(group["id"])
    assert group["status"]=="reviewing" and group["selected_job_id"]==selected["id"]
    assert catalog.catalog_snapshot()==before
    selected=service.get(selected["id"])
    assert service.approve(selected["id"],**approval(selected))["status"]=="approved"


def test_comparison_labels_and_resume_metadata_persist_in_job_summaries(tmp_path):
    service,_,_=offline_service(tmp_path);group,a,b=comparison_pair(service)
    renamed=service.add_comparison_job(group["id"],job_id=a["id"],expected_revision=group["revision"],label="自选网页模型")
    assert renamed["revision"]==group["revision"]+1
    summary=next(row for row in service.list()["items"] if row["id"]==a["id"])
    assert summary["comparison_id"]==group["id"] and summary["comparison_label"]=="自选网页模型"
    assert summary["comparison_role"]=="candidate" and "draft" not in summary
    assert service.list_comparisons(title="合成小说",author="测试作者")["items"][0]["id"]==group["id"]


def test_comparison_source_id_collisions_do_not_overwrite_either_original_source(tmp_path):
    service,_,_=offline_service(tmp_path)
    a=service.create_semi(title="合成小说",author="测试作者",target_work_id="W1");a=paste(service,a)
    b=service.create_semi(title="合成小说",author="测试作者",target_work_id="W1")
    value=response(b);value["sources"][0]["url"]="https://catalog.example.test/other-source";b=paste(service,b,value)
    group=service.create_comparison(title="合成小说",author="测试作者",target_work_id="W1")
    for job in (a,b):group=service.add_comparison_job(group["id"],job_id=job["id"],expected_revision=group["revision"])
    review=service.compose_comparison(group["id"],source_job_id=a["id"],expected_revision=group["revision"])
    assert len(review["sources"])==2 and len({source["id"] for source in review["sources"]})==2
    assert {source["url"] for source in review["sources"]}=={URL,"https://catalog.example.test/other-source"}
    assert service.get(a["id"])["sources"]==a["sources"] and service.get(b["id"])["sources"]==b["sources"]


def test_comparison_api_supports_labelled_external_versions_without_api_token_claims(tmp_path):
    from fastapi import FastAPI
    from fastapi.responses import JSONResponse
    from fastapi.testclient import TestClient
    from inkreference.research_api import create_research_router
    service,_,_=offline_service(tmp_path);app=FastAPI();app.include_router(create_research_router(service))
    @app.exception_handler(DataError)
    def handler(request,exc):return JSONResponse({"detail":exc.detail},status_code=exc.status_code)
    with TestClient(app) as client:
        group=client.post("/api/studio/research/comparisons",json={"title":"合成小说","author":"测试作者","target_work_id":"W1"}).json()
        job=client.post("/api/studio/research/semi/prompt",json={"title":"合成小说","author":"测试作者"}).json()
        group=client.post(f"/api/studio/research/comparisons/{group['id']}/jobs",json={"job_id":job["id"],"expected_revision":group["revision"],"label":"用户填写网页模型"}).json()
        assert group["jobs"][0]["output_metering"]["kind"]=="external_unmetered"
        assert group["jobs"][0]["output_metering"]["output_tokens"] is None
        parsed=client.post(f"/api/studio/research/jobs/{job['id']}/reply",json={"expected_revision":job["revision"],"reply":json.dumps(response(job),ensure_ascii=False)})
        assert parsed.status_code==200,parsed.text
        group=client.get(f"/api/studio/research/comparisons/{group['id']}").json()
        selected=client.post(f"/api/studio/research/comparisons/{group['id']}/compose",json={"source_job_id":job["id"],"expected_revision":group["revision"]})
        assert selected.status_code==200,selected.text
        assert selected.json()["comparison_label"]=="用户填写网页模型"
        assert client.get("/api/studio/research/comparisons?title=合成小说&author=测试作者").json()["items"][0]["id"]==group["id"]


def test_comparison_accepts_running_api_candidate_and_keeps_its_actual_app_usage(tmp_path):
    service,create,_,_,_=make_service(tmp_path)
    job=create();group=service.create_comparison(title="合成小说",author="测试作者")
    group=service.add_comparison_job(group["id"],job_id=job["id"],expected_revision=group["revision"],label="APP模型 A")
    job=service.start(job["id"],background=False)
    assert job["status"]=="needs_review" and job["comparison_id"]==group["id"]
    group=service.get_comparison(group["id"])
    assert group["jobs"][0]["usage"]["calls"]==2
    assert group["jobs"][0]["output_metering"]["kind"]=="app_api"
    assert group["jobs"][0]["score_scenarios"]


def test_comparison_groups_bound_payload_fanout_to_four_versions(tmp_path):
    service,_,_=offline_service(tmp_path);group=service.create_comparison(title="合成小说",author="测试作者")
    for index in range(4):
        job=service.create_semi(title="合成小说",author="测试作者")
        group=service.add_comparison_job(group["id"],job_id=job["id"],expected_revision=group["revision"],label=f"版本{index}")
    extra=service.create_semi(title="合成小说",author="测试作者")
    with pytest.raises(DataError,match="最多保留4"):
        service.add_comparison_job(group["id"],job_id=extra["id"],expected_revision=group["revision"])
    assert len(service.get_comparison(group["id"])["jobs"])==4


def test_comparison_commit_lock_blocks_other_tab_from_reselecting_mid_transaction(tmp_path):
    import threading
    service,catalog,_=offline_service(tmp_path);group,a,b=comparison_pair(service)
    review=service.compose_comparison(group["id"],source_job_id=a["id"],expected_revision=group["revision"])
    entered,released=threading.Event(),threading.Event();original_commit=service.catalog_commit;results=[]
    def commit(*args):
        entered.set();assert released.wait(5)
        return original_commit(*args)
    service.catalog_commit=commit
    def run():
        try:results.append(service.approve(review["id"],**approval(review)))
        except Exception as error:results.append(error)
    worker=threading.Thread(target=run);worker.start()
    assert entered.wait(5)
    try:
        current=service.get_comparison(group["id"])
        assert current["status"]=="approving"
        with pytest.raises(DataError):service.compose_comparison(group["id"],source_job_id=b["id"],expected_revision=current["revision"])
        with pytest.raises(DataError):service.approve(review["id"],**approval(review))
    finally:released.set();worker.join(5)
    assert len(results)==1 and isinstance(results[0],dict) and results[0]["status"]=="approved",results
    assert catalog.stats()["cliches"]==1 and catalog.stats()["characters"]==1
