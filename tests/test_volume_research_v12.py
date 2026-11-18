"""Real volume-research graph and HTTP routes with wholly synthetic providers."""
from __future__ import annotations

import hashlib
import json
import socket
import threading
from contextlib import contextmanager

import httpx
import pytest
from fastapi.testclient import TestClient

from inkreference.api import create_app
from inkreference.database import DataError
from inkreference.local_security import target_fingerprint

TITLE="合成分卷作品"
AUTHOR="目录测试作者"
URL="https://volumes.example.test/official"
OTHER_URL="https://volumes.example.test/other"
SOURCE="src_"+hashlib.sha256(URL.encode()).hexdigest()[:16]
OTHER_SOURCE="src_"+hashlib.sha256(OTHER_URL.encode()).hexdigest()[:16]
PRIVATE_BODY="不可以外传的正文甲乙丙丁戊己庚辛壬癸"
PLAN=[{"title":"第一卷 旅途","start_chapter":1,"end_chapter":2},{"title":"第二卷 归来","start_chapter":3,"end_chapter":3}]


def resolver(*args,**kwargs):
    return [(socket.AF_INET,socket.SOCK_STREAM,6,"",("8.8.8.8",443))]


@contextmanager
def workbench(tmp_path,*,author=AUTHOR,source_author=AUTHOR,search_author=None,source_ids=None,model_gate=None,mixed_sources=False,search_gate=None):
    app=create_app(tmp_path)
    calls=[]
    options={"source_ids":source_ids or [SOURCE]}
    def handler(request):
        calls.append(request)
        if request.url.host=="volume-search.example.test":
            if search_gate:
                search_gate[0].set();assert search_gate[1].wait(10),"fixture search gate was not released"
            results=[{"url":URL,"title":f"{TITLE} {search_author or source_author}","content":"官方合成目录"}]
            if mixed_sources:results.append({"url":OTHER_URL,"title":f"{TITLE} 另一作者","content":"另一作者同名作品"})
            return httpx.Response(200,json={"results":results})
        if request.url.host=="volumes.example.test":
            selected_author="另一作者" if request.url.path=="/other" else source_author
            return httpx.Response(200,text=f"<html><p>{TITLE}，作者：{selected_author}。第一卷旅途包含第一、二章，第二卷归来包含第三章。这是公开的合成目录资料，不包含任何用户正文。</p></html>",headers={"Content-Type":"text/html;charset=utf-8"})
        if request.url.path.endswith("chat/completions"):
            if model_gate:
                model_gate[0].set();assert model_gate[1].wait(10),"fixture model gate was not released"
            return httpx.Response(200,json={"choices":[{"message":{"content":json.dumps({"volumes":PLAN,"source_ids":options["source_ids"]},ensure_ascii=False)},"finish_reason":"stop"}],"usage":{"prompt_tokens":111,"completion_tokens":44}})
        raise AssertionError(str(request.url))
    with TestClient(app,base_url="http://127.0.0.1") as client:
        runtime=app.state.runtime
        doc=runtime.studio.documents.upload(TITLE+".txt",f"第1章 出发\n{PRIVATE_BODY}。\n第2章 途中\n正文第二段。\n第3章 归来\n正文第三段。".encode())
        metadata=runtime.studio.documents.get_book_metadata(doc["id"])
        if author:
            runtime.studio.documents.update_book_metadata(doc["id"],{"title":TITLE,"author":author},expected_version_id=doc["latest_version_id"],expected_revision=metadata["metadata_revision"])
        models=runtime.studio.models
        models.transport=httpx.MockTransport(handler)
        models.save_profile({"id":"volume-model","provider":"openai_compatible","model":"synthetic-volume-v1","base_url":"http://127.0.0.1:12345/v1","context_window":32000,"max_output_tokens":2000})
        sources=runtime.studio.research.sources
        sources.transport=httpx.MockTransport(handler);sources.resolver=resolver
        sources.save_settings({"provider":"tavily","endpoint":"https://volume-search.example.test/search","credential_mode":"session","api_key":"synthetic-volume-search-secret"})
        prefix=f"/api/studio/documents/{doc['id']}/versions/{doc['latest_version_id']}/volumes"
        body={"profile_id":"volume-model","expected_model_fingerprint":target_fingerprint(models.snapshot("volume-model")),"expected_search_fingerprint":sources.settings()["target_fingerprint"],"base_revision":0}
        yield {"app":app,"client":client,"runtime":runtime,"doc":doc,"prefix":prefix,"body":body,"calls":calls,"options":options}


def completed(state):
    response=state["client"].post(state["prefix"]+"/research",json=state["body"])
    assert response.status_code==202,response.text
    result=response.json()
    for worker in state["runtime"].studio.volume_research.active_threads():worker.join(10)
    assert not state["runtime"].studio.volume_research.active_threads()
    reply=state["client"].get(state["prefix"]+"/research/"+result["id"])
    assert reply.status_code==200,reply.text
    return reply.json()


def test_volume_research_fetches_real_sources_and_waits_for_explicit_plan_save(tmp_path):
    with workbench(tmp_path) as state:
        client=state["client"];before=client.get(state["prefix"]).json()
        result=completed(state)
        assert result["status"]=="completed",result
        assert result["volumes"]==PLAN
        assert result["source_ids"]==[SOURCE]
        assert result["sources"][0]["id"]==SOURCE and result["sources"][0]["fetch_status"]=="verified"
        assert client.get(state["prefix"]).json()==before
        usage=state["runtime"].studio.models.list_usage(result["id"])
        assert len(usage)==1 and usage[0]["status"]=="success"
        assert sum(request.url.host=="volume-search.example.test" for request in state["calls"])==1
        assert any(request.url.host=="volumes.example.test" for request in state["calls"])
        assert all(PRIVATE_BODY not in request.content.decode("utf-8") and PRIVATE_BODY not in str(request.url) for request in state["calls"])
        saved=client.put(state["prefix"],json={"volumes":result["volumes"],"expected_revision":before["revision"]})
        assert saved.status_code==200 and saved.json()["revision"]==1
        stale=client.put(state["prefix"],json={"volumes":PLAN,"expected_revision":0})
        assert stale.status_code==409


def test_automatic_volume_research_rejects_different_author_before_model_call(tmp_path):
    with workbench(tmp_path,source_author="同名异作者") as state:
        result=completed(state)
        assert result["status"]=="failed" and "作者" in result["error"]
        assert not any(request.url.path.endswith("chat/completions") for request in state["calls"])
        assert state["client"].get(state["prefix"]).json()["revision"]==0


def test_automatic_volume_research_rejects_unknown_source_references(tmp_path):
    with workbench(tmp_path,source_ids=["invented_source"]) as state:
        result=completed(state)
        assert result["status"]=="failed" and "引用" in result["error"]
        assert state["runtime"].studio.models.usage_summary(run_id=result["id"])["calls"]==1
        assert state["client"].get(state["prefix"]).json()["revision"]==0


def test_model_must_cite_an_identity_matching_source_not_only_an_unrelated_page(tmp_path):
    with workbench(tmp_path,mixed_sources=True,source_ids=[OTHER_SOURCE]) as state:
        result=completed(state)
        assert result["status"]=="failed",result
        assert "作者" in result["error"] or "身份" in result["error"]
        assert state["client"].get(state["prefix"]).json()["revision"]==0


def test_missing_author_and_stale_target_fingerprints_do_not_start_network_calls(tmp_path):
    with workbench(tmp_path,author="") as state:
        response=state["client"].post(state["prefix"]+"/research",json=state["body"])
        assert response.status_code==422 and "作者" in response.text
        assert not state["calls"]
    with workbench(tmp_path/"fingerprints") as state:
        for field in ("expected_model_fingerprint","expected_search_fingerprint"):
            response=state["client"].post(state["prefix"]+"/research",json={**state["body"],field:"0"*64})
            assert response.status_code==409
        assert not state["calls"]


@pytest.mark.parametrize("change",["metadata","version","model","search"])
def test_running_volume_task_rejects_stale_metadata_version_or_provider_targets(tmp_path,change):
    entered,released=threading.Event(),threading.Event()
    with workbench(tmp_path,model_gate=(entered,released)) as state:
        response=state["client"].post(state["prefix"]+"/research",json=state["body"])
        assert response.status_code==202,response.text
        job=response.json();runtime=state["runtime"]
        assert entered.wait(5)
        try:
            if change=="metadata":
                old=runtime.studio.documents.get_book_metadata(state["doc"]["id"])
                runtime.studio.documents.update_book_metadata(state["doc"]["id"],{"author":"修改后的作者"},expected_version_id=state["doc"]["latest_version_id"],expected_revision=old["metadata_revision"])
            elif change=="version":
                runtime.studio.documents.replace(state["doc"]["id"],state["doc"]["latest_version_id"],"new.txt","第1章 新版\n新的正文。".encode())
            elif change=="model":
                profile=runtime.studio.models.snapshot("volume-model");profile["model"]="synthetic-volume-v2"
                runtime.studio.models.save_profile(profile,"volume-model")
            else:
                runtime.studio.research.sources.save_settings({"provider":"tavily","endpoint":"https://volume-search.example.test/new-search","credential_mode":"session","api_key":"new-synthetic-search-secret"})
        finally:released.set()
        for worker in runtime.studio.volume_research.active_threads():worker.join(5)
        result=state["client"].get(state["prefix"]+"/research/"+job["id"]).json()
        assert result["status"]=="failed",result
        assert runtime.studio.models.usage_summary(run_id=job["id"])["calls"]==1
        assert state["client"].get(state["prefix"]).json()["revision"]==0


def test_cancelling_inflight_call_keeps_usage_but_never_saves_a_plan(tmp_path):
    entered,released=threading.Event(),threading.Event()
    with workbench(tmp_path,model_gate=(entered,released)) as state:
        response=state["client"].post(state["prefix"]+"/research",json=state["body"]);job=response.json()
        assert response.status_code==202 and entered.wait(5)
        try:
            stopped=state["client"].post(state["prefix"]+"/research/"+job["id"]+"/cancel")
            assert stopped.status_code==200
        finally:released.set()
        for worker in state["runtime"].studio.volume_research.active_threads():worker.join(5)
        result=state["client"].get(state["prefix"]+"/research/"+job["id"]).json()
        assert result["status"]=="cancelled" and result["volumes"] is None
        assert result["usage"] and state["runtime"].studio.models.usage_summary(run_id=job["id"])["calls"]==1
        assert state["client"].get(state["prefix"]).json()["revision"]==0


def test_active_volume_worker_blocks_project_backup_before_runner_initialization(tmp_path):
    entered,released=threading.Event(),threading.Event()
    with workbench(tmp_path,search_gate=(entered,released)) as state:
        response=state["client"].post(state["prefix"]+"/research",json=state["body"])
        assert response.status_code==202 and entered.wait(5)
        try:
            assert state["runtime"].studio._runner is None
            backup=state["client"].get("/api/studio/projects/export")
            assert backup.status_code==409 and "分卷" in backup.text
            assert state["runtime"].studio._runner is None
            assert state["runtime"].studio.volume_research.has_active_runs()
        finally:released.set()
        for worker in state["runtime"].studio.volume_research.active_threads():worker.join(5)
        assert not state["runtime"].studio.volume_research.has_active_runs()


def test_new_volume_routes_do_not_initialize_studio_on_startup(tmp_path):
    destination=tmp_path/"lazy"
    app=create_app(destination)
    assert not destination.exists()
    with TestClient(app,base_url="http://127.0.0.1") as client:
        assert client.get("/api/health").status_code==200
        assert app.state.runtime._studio is None
    assert not (destination/"workbench.sqlite3").exists()


def test_research_job_lookup_and_cancel_are_pinned_to_document_and_version(tmp_path):
    with workbench(tmp_path) as state:
        result=completed(state)
        wrong=state["prefix"].replace(state["doc"]["id"],"another-document")+"/research/"+result["id"]
        assert state["client"].get(wrong).status_code==404
        assert state["client"].post(wrong+"/cancel").status_code==404


def manual_document(runtime):
    doc=runtime.studio.documents.upload(TITLE+".txt",f"第1章 出发\n{PRIVATE_BODY}。\n第2章 途中\n中途正文。\n第3章 归来\n归来正文。".encode())
    old=runtime.studio.documents.get_book_metadata(doc["id"])
    runtime.studio.documents.update_book_metadata(doc["id"],{"author":AUTHOR},expected_version_id=doc["latest_version_id"],expected_revision=old["metadata_revision"])
    return doc,f"/api/studio/documents/{doc['id']}/versions/{doc['latest_version_id']}/volumes"


def manual_reply(prompt):
    return {"protocol":"inkreference.volume.v1","request_id":prompt["request_id"],"title":prompt["title"],"author":prompt["author"],"volumes":PLAN,"sources":[{"url":URL,"title":TITLE+" "+AUTHOR,"excerpt":"外部网页模型提供的来源摘要。","fetch_status":"verified"}]}


def test_semiautomatic_volume_prompt_parse_and_reviewed_put_need_no_providers(tmp_path):
    app=create_app(tmp_path)
    with TestClient(app,base_url="http://127.0.0.1") as client:
        runtime=app.state.runtime;doc,prefix=manual_document(runtime)
        prompt=client.get(prefix+"/prompt").json()
        assert prompt["chapter_count"]==3 and len(prompt["request_id"])==64
        assert prompt["author"]==AUTHOR and prompt["title"]==TITLE
        assert PRIVATE_BODY not in prompt["prompt"]
        assert all("#"+str(number) in prompt["prompt"] for number in range(1,4))
        assert runtime.studio._models is None and runtime.studio._research is None
        before=client.get(prefix).json()
        pasted=client.post(prefix+"/paste",json={"contract_version":2,"content":"```json\n"+json.dumps(manual_reply(prompt),ensure_ascii=False)+"\n```"})
        assert pasted.status_code==200,pasted.text
        assert pasted.json()["volumes"]==PLAN
        assert pasted.json()["sources"][0]["fetch_status"]=="supplied"
        assert client.get(prefix).json()==before
        assert runtime.studio._models is None and runtime.studio._research is None and runtime.studio._volume_research is None
        saved=client.put(prefix,json={"expected_revision":before["revision"],"volumes":pasted.json()["volumes"]})
        assert saved.status_code==200 and saved.json()["revision"]==1
        assert client.put(prefix,json={"expected_revision":0,"volumes":PLAN}).status_code==409
        assert client.get(prefix+"/prompt").json()["request_id"]!=prompt["request_id"]
        assert client.post(prefix+"/paste",json={"contract_version":2,"content":json.dumps(manual_reply(prompt),ensure_ascii=False)}).status_code==409


@pytest.mark.parametrize("field,value",[("protocol","other.protocol"),("request_id","old_request"),("title","另一作品"),("author","另一作者")])
def test_semiautomatic_reply_rejects_wrong_identity_and_keeps_plan_unchanged(tmp_path,field,value):
    app=create_app(tmp_path)
    with TestClient(app,base_url="http://127.0.0.1") as client:
        _,prefix=manual_document(app.state.runtime);prompt=client.get(prefix+"/prompt").json()
        reply=manual_reply(prompt);reply[field]=value
        response=client.post(prefix+"/paste",json={"contract_version":2,"content":json.dumps(reply,ensure_ascii=False)})
        assert response.status_code in {409,422},response.text
        assert client.get(prefix).json()["revision"]==0


def test_semiautomatic_reply_is_invalidated_by_metadata_and_new_version(tmp_path):
    app=create_app(tmp_path)
    with TestClient(app,base_url="http://127.0.0.1") as client:
        runtime=app.state.runtime;doc,prefix=manual_document(runtime);prompt=client.get(prefix+"/prompt").json()
        reply=manual_reply(prompt)
        old=runtime.studio.documents.get_book_metadata(doc["id"])
        runtime.studio.documents.update_book_metadata(doc["id"],{"author":"修改后的作者"},expected_version_id=doc["latest_version_id"],expected_revision=old["metadata_revision"])
        response=client.post(prefix+"/paste",json={"contract_version":2,"content":json.dumps(reply,ensure_ascii=False)})
        assert response.status_code==409,response.text
        assert client.get(prefix).json()["revision"]==0
        newer=runtime.studio.documents.replace(doc["id"],doc["latest_version_id"],TITLE+".txt","第1章 新章\n新版正文。\n第2章 途中\n新版中段。\n第3章 归来\n新版末段。".encode())
        new_prefix=f"/api/studio/documents/{doc['id']}/versions/{newer['id']}/volumes"
        response=client.post(new_prefix+"/paste",json={"contract_version":2,"content":json.dumps(reply,ensure_ascii=False)})
        assert response.status_code==409,response.text
        assert client.get(new_prefix).json()["revision"]==0


def test_running_volume_task_rejects_concurrent_plan_change_before_review(tmp_path):
    entered,released=threading.Event(),threading.Event()
    with workbench(tmp_path,model_gate=(entered,released)) as state:
        response=state["client"].post(state["prefix"]+"/research",json=state["body"])
        assert response.status_code==202 and entered.wait(5)
        job=response.json()
        try:
            saved=state["client"].put(state["prefix"],json={"expected_revision":0,"volumes":[{"title":"用户方案","start_chapter":1,"end_chapter":3}]})
            assert saved.status_code==200
        finally:released.set()
        for worker in state["runtime"].studio.volume_research.active_threads():worker.join(5)
        result=state["client"].get(state["prefix"]+"/research/"+job["id"]).json()
        assert result["status"]=="failed" and "分卷方案" in result["error"]
        assert state["client"].get(state["prefix"]).json()["volumes"]==[{"title":"用户方案","start_chapter":1,"end_chapter":3}]


def test_semiautomatic_prompt_keeps_missing_chapters_and_preface_in_local_order(tmp_path):
    app=create_app(tmp_path)
    with TestClient(app,base_url="http://127.0.0.1") as client:
        docs=app.state.runtime.studio.documents
        doc=docs.upload("缺章作品.txt","序言\n开场前置正文。\n第1章 开始\n第一章正文。\n第3章 终点\n第三章正文。".encode())
        prefix=f"/api/studio/documents/{doc['id']}/versions/{doc['latest_version_id']}/volumes"
        prompt=client.get(prefix+"/prompt").json()
        summary=docs.get_version_summary(doc["id"],doc["latest_version_id"])
        assert summary["chapter_count"]==3  # Legacy indexes omit placeholder items.
        assert prompt["chapter_count"]==4
        assert client.get(prefix+"?coordinate_version=2").json()["chapter_count"]==prompt["chapter_count"]
        assert "[待补充]" in prompt["prompt"]
        assert all("#"+str(number) in prompt["prompt"] for number in range(1,prompt["chapter_count"]+1))
        assert "第一章正文" not in prompt["prompt"] and "开场前置正文" not in prompt["prompt"]
        reply=manual_reply(prompt);reply["volumes"]=[{"title":"全书","start_chapter":1,"end_chapter":prompt["chapter_count"]}]
        parsed=client.post(prefix+"/paste",json={"contract_version":2,"content":json.dumps(reply,ensure_ascii=False)})
        assert parsed.status_code==200,parsed.text
        saved=client.put(prefix,json={"expected_revision":0,"coordinate_version":2,"volumes":parsed.json()["volumes"]})
        assert saved.status_code==200,saved.text


def test_shutdown_marks_inflight_volume_task_cancelled_and_accounts_completed_call(tmp_path):
    entered,released=threading.Event(),threading.Event()
    with workbench(tmp_path,model_gate=(entered,released)) as state:
        response=state["client"].post(state["prefix"]+"/research",json=state["body"])
        assert response.status_code==202 and entered.wait(5)
        job=response.json();service=state["runtime"].studio.volume_research
        try:service.shutdown()
        finally:released.set()
        for worker in service.active_threads():worker.join(5)
        result=service.get(job["id"])
        assert result["status"]=="cancelled" and result["usage"]
        assert state["runtime"].studio.models.usage_summary(run_id=job["id"])["calls"]==1
        assert not service.has_active_runs()


def test_app_lifespan_cancels_active_volume_worker_and_retains_its_accounting(tmp_path):
    entered,released=threading.Event(),threading.Event()
    manager=workbench(tmp_path,model_gate=(entered,released))
    state=manager.__enter__()
    closed=False
    try:
        response=state["client"].post(state["prefix"]+"/research",json=state["body"])
        assert response.status_code==202 and entered.wait(5)
        job=response.json();service=state["runtime"].studio.volume_research
        manager.__exit__(None,None,None);closed=True
        assert service._row(job["id"])["cancel_requested"]==1
        assert service.active_threads()
        released.set()
        for worker in service.active_threads():worker.join(5)
        result=service.get(job["id"])
        assert result["status"]=="cancelled" and result["usage"]
        assert state["runtime"].studio.models.usage_summary(run_id=job["id"])["calls"]==1
    finally:
        released.set()
        if not closed:manager.__exit__(None,None,None)


def test_search_result_title_cannot_replace_identity_in_independently_fetched_text(tmp_path):
    with workbench(tmp_path,source_author="另一作者",search_author=AUTHOR) as state:
        result=completed(state)
        assert result["status"]=="failed",result
        assert not any(request.url.path.endswith("chat/completions") for request in state["calls"])


def test_volume_coordinate_views_preserve_stored_plan_and_bridge_placeholder_only_volume(tmp_path):
    app=create_app(tmp_path)
    with TestClient(app,base_url="http://127.0.0.1") as client:
        docs=app.state.runtime.studio.documents
        doc=docs.upload("缺章作品.txt","序言\n前置文本。\n第1章 开始\n第一章正文。\n第3章 终点\n第三章正文。".encode())
        prefix=f"/api/studio/documents/{doc['id']}/versions/{doc['latest_version_id']}/volumes"
        plan=[{"title":"起始","start_chapter":1,"end_chapter":2},{"title":"缺章专卷","start_chapter":3,"end_chapter":3},{"title":"结尾","start_chapter":4,"end_chapter":4}]
        saved=client.put(prefix,json={"coordinate_version":2,"expected_revision":0,"volumes":plan})
        assert saved.status_code==200,saved.text
        store=app.state.runtime.studio.volumes
        with store._connect() as db:before=db.execute("SELECT plan_json FROM reference_volume_plans").fetchone()[0]
        legacy=client.get(prefix).json();full=client.get(prefix+"?coordinate_version=2").json()
        assert legacy["chapter_count"]==3 and full["chapter_count"]==4
        assert full["volumes"]==plan
        assert legacy["volumes"]==[{"title":"起始","start_chapter":1,"end_chapter":2},{"title":"结尾","start_chapter":3,"end_chapter":3}]
        with store._connect() as db:after=db.execute("SELECT plan_json FROM reference_volume_plans").fetchone()[0]
        assert before==after
        # Reading the new view never rewrites an existing coordinate-1 plan.
        old=[{"title":"旧首卷","start_chapter":1,"end_chapter":1},{"title":"旧次卷","start_chapter":2,"end_chapter":3}]
        saved=client.put(prefix,json={"expected_revision":1,"volumes":old})
        assert saved.status_code==200,saved.text
        with store._connect() as db:before=db.execute("SELECT plan_json FROM reference_volume_plans").fetchone()[0]
        assert client.get(prefix).json()["volumes"]==old
        assert client.get(prefix+"?coordinate_version=2").json()["volumes"]==[{"title":"旧首卷","start_chapter":1,"end_chapter":1},{"title":"旧次卷","start_chapter":2,"end_chapter":4}]
        with store._connect() as db:assert db.execute("SELECT plan_json FROM reference_volume_plans").fetchone()[0]==before


def test_strict_web_volume_reply_rejects_duplicate_identity_keys_and_missing_sources(tmp_path):
    app=create_app(tmp_path)
    with TestClient(app,base_url="http://127.0.0.1") as client:
        _,prefix=manual_document(app.state.runtime);prompt=client.get(prefix+"/prompt").json()
        reply=manual_reply(prompt);reply["sources"]=[]
        assert client.post(prefix+"/paste",json={"contract_version":2,"content":json.dumps(reply,ensure_ascii=False)}).status_code==422
        text=json.dumps(manual_reply(prompt),ensure_ascii=False)
        text=text[:-1]+',"author":"另一作者"}'
        result=client.post(prefix+"/paste",json={"contract_version":2,"content":text})
        assert result.status_code==422,result.text
