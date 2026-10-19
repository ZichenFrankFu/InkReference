"""Cross-module boundaries: selection, scoped transport, and model download usage."""
from __future__ import annotations

import json
import threading

import httpx
from fastapi.testclient import TestClient

from inkreference.api import create_app
from inkreference.embedding_downloads import EmbeddingDownloads
from inkreference.models import ModelService
from test_workflow_v12 import stage_result


class PullSuccess(httpx.SyncByteStream):
    def __iter__(self):
        yield b'{"status":"success"}\n'


def extraction_comparison(client,app):
    runtime=app.state.runtime
    source="第1章 入城\n甲把信交给乙。\n第2章 禁区\n范围外独有句不可外传。\n"
    doc=runtime.studio.documents.upload("作用域作品.txt",source.encode())
    requests=[]
    def handler(request):
        payload=json.loads(request.content);prompt=payload["messages"][-1]["content"]
        stage=prompt.split("固定阶段：",1)[1].split("（",1)[0]
        body=prompt.split("<source_text>\n",1)[1].rsplit("\n</source_text>",1)[0]
        requests.append({"model":payload["model"],"payload":payload,"body":body})
        result=stage_result(stage,body)
        if stage=="plot":result["plot"]["logline"]=payload["model"]+" 的梗概。"
        return httpx.Response(200,json={"choices":[{"message":{"content":json.dumps(result,ensure_ascii=False)},"finish_reason":"stop"}],"usage":{"prompt_tokens":25,"completion_tokens":15}})
    runtime.studio.models.transport=httpx.MockTransport(handler)
    for model in ("variant-a","variant-b"):
        runtime.studio.models.save_profile({"id":model,"provider":"openai_compatible","model":model,"base_url":"http://127.0.0.1:12345/v1","context_window":160000,"max_output_tokens":16000})
    response=client.post("/api/studio/jobs/comparisons",json={"document_id":doc["id"],"workflow_version":"4.0.0","chapter_ranges":[{"start_chapter":1,"end_chapter":1}],"variants":[{"label":"A","profile_id":"variant-a"},{"label":"B","profile_id":"variant-b"}]})
    assert response.status_code==201,response.text
    group=response.json()
    runs=[]
    for variant in group["variants"]:
        run=runtime.studio.runner.execute(variant["run_id"])
        assert run["status"]=="needs_review",run["error"]
        runs.append(run)
    assert len(requests)==8
    assert {request["model"] for request in requests}=={"variant-a","variant-b"}
    assert all("范围外独有句不可外传" not in json.dumps(request["payload"],ensure_ascii=False) for request in requests)
    assert runs[0]["candidates"][0]["result"]["reference"]["plot"]["logline"]!=runs[1]["candidates"][0]["result"]["reference"]["plot"]["logline"]
    return doc,group,runs


def commit(client,doc,group,run):
    state=client.get(f"/api/studio/documents/{doc['id']}/reference").json()
    return client.post(f"/api/studio/jobs/{run['id']}/chunks/{run['candidates'][0]['chunk_id']}/commit",json={"sections":["plot"],"expected_revision":state["revision"],"comparison_id":group["id"]})


def test_extraction_ab_unselected_candidate_cannot_cross_http_commit_boundary(tmp_path):
    app=create_app(tmp_path)
    with TestClient(app,base_url="http://127.0.0.1") as client:
        doc,group,runs=extraction_comparison(client,app)
        selection=client.post(f"/api/studio/jobs/comparisons/{group['id']}/select",json={"run_id":runs[0]["id"],"expected_revision":group["revision"]})
        assert selection.status_code==200,selection.text
        before=client.get(f"/api/studio/documents/{doc['id']}/reference").json()
        rejected=commit(client,doc,group,runs[1])
        assert rejected.status_code==409,rejected.text
        compatibility=client.post(f"/api/studio/jobs/{runs[1]['id']}/chunks/{runs[1]['candidates'][0]['chunk_id']}/commit",json={"sections":["plot"],"expected_revision":before["revision"]})
        assert compatibility.status_code==409,compatibility.text
        assert client.get(f"/api/studio/documents/{doc['id']}/reference").json()==before
        accepted=commit(client,doc,group,runs[0])
        assert accepted.status_code==200,accepted.text
        assert accepted.json()["sections"]["plot"]["logline"]=="variant-a 的梗概。"


def test_extraction_ab_selection_cannot_switch_after_one_variant_is_committed(tmp_path):
    app=create_app(tmp_path)
    with TestClient(app,base_url="http://127.0.0.1") as client:
        doc,group,runs=extraction_comparison(client,app)
        selected=client.post(f"/api/studio/jobs/comparisons/{group['id']}/select",json={"run_id":runs[0]["id"],"expected_revision":group["revision"]})
        assert selected.status_code==200,selected.text
        accepted=commit(client,doc,group,runs[0])
        assert accepted.status_code==200,accepted.text
        current=client.get(f"/api/studio/jobs/comparisons/{group['id']}").json()
        changed=client.post(f"/api/studio/jobs/comparisons/{group['id']}/select",json={"run_id":runs[1]["id"],"expected_revision":current["revision"]})
        assert changed.status_code==409,changed.text
        assert client.get(f"/api/studio/documents/{doc['id']}/reference").json()["sections"]["plot"]["logline"]=="variant-a 的梗概。"


def test_download_probe_cancel_keeps_received_actual_token_usage(tmp_path):
    entered,released=threading.Event(),threading.Event()
    def handler(request):
        if request.url.path.endswith("/pull"):return httpx.Response(200,stream=PullSuccess())
        assert request.url.path.endswith("/embed")
        entered.set();assert released.wait(5)
        return httpx.Response(200,json={"embeddings":[[1.0,0.5]],"prompt_eval_count":17})
    models=ModelService(tmp_path,transport=httpx.MockTransport(handler));service=EmbeddingDownloads(tmp_path,models)
    target=service.target("synthetic-embed")
    job=service.create("synthetic-embed",expected_target_fingerprint=target["fingerprint"])
    try:
        assert entered.wait(5)
        service.cancel(job["id"])
    finally:released.set()
    for worker in service.active_threads():worker.join(5)
    assert service.get(job["id"])["status"]=="cancelled"
    assert models.list_profiles()==[]
    usage=models.list_usage("embedding-download-"+job["id"])
    assert len(usage)==1 and usage[0]["usage"]["input_tokens"]==17
    assert usage[0]["operation"]=="embedding_capability"


def test_concurrent_ab_select_and_commit_cannot_both_adopt_different_variants(tmp_path):
    app=create_app(tmp_path)
    with TestClient(app,base_url="http://127.0.0.1") as client:
        doc,group,runs=extraction_comparison(client,app)
        selected=client.post(f"/api/studio/jobs/comparisons/{group['id']}/select",json={"run_id":runs[0]["id"],"expected_revision":group["revision"]})
        assert selected.status_code==200,selected.text
        group=selected.json();barrier=threading.Barrier(3);results=[]
        def save():
            barrier.wait(5)
            results.append(("commit",commit(client,doc,group,runs[0])))
        def choose():
            barrier.wait(5)
            results.append(("select",client.post(f"/api/studio/jobs/comparisons/{group['id']}/select",json={"run_id":runs[1]["id"],"expected_revision":group["revision"]})))
        workers=[threading.Thread(target=save),threading.Thread(target=choose)]
        for worker in workers:worker.start()
        barrier.wait(5)
        for worker in workers:worker.join(10)
        assert all(not worker.is_alive() for worker in workers)
        assert len(results)==2 and sorted(reply.status_code for _,reply in results)==[200,409],[(kind,reply.status_code,reply.text) for kind,reply in results]
        adopted=client.get(f"/api/studio/documents/{doc['id']}/reference").json()
        final=client.get(f"/api/studio/jobs/comparisons/{group['id']}").json()
        by_kind=dict(results)
        if by_kind["commit"].status_code==200:
            assert adopted["sections"]["plot"]["logline"]=="variant-a 的梗概。"
            assert final["selected_run_id"]==runs[0]["id"]
        else:
            assert not adopted["commits"] and final["selected_run_id"]==runs[1]["id"]
