"""Real provider embeddings with durable, fingerprinted vector reuse.

Lexical matching is deliberately not a substitute for semantic search. Missing
or malformed vectors fail the review preparation rather than allowing a new
trope to slip past deduplication.
"""
from __future__ import annotations

import hashlib
import json
import math
import time
import uuid
from typing import Any

import httpx

from .database import DataError
from .diagnostics import logged_operation, record_exception
from .local_security import target_fingerprint
from .models import ModelService, _cost, _now, _usage


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), allow_nan=False)


def embedding_target(profile: dict, model: str | None = None) -> str:
    return target_fingerprint({**profile, "model": model or profile["model"]})


def cosine(left: list[float], right: list[float]) -> float:
    if len(left) != len(right) or not left:
        raise DataError(502, "向量维度不一致，请检查 embedding 模型")
    scale_left,scale_right=max(map(abs,left)),max(map(abs,right))
    if not scale_left or not scale_right:
        raise DataError(502, "Embedding 服务返回零向量")
    a=[v/scale_left for v in left];b=[v/scale_right for v in right]
    aa,bb=sum(v*v for v in a),sum(v*v for v in b)
    return max(-1.0, min(1.0, sum(x*y for x,y in zip(a,b)) / math.sqrt(aa*bb)))


class SemanticIndex:
    def __init__(self, models: ModelService):
        self.models = models
        with models._connect() as db:
            db.execute("CREATE TABLE IF NOT EXISTS research_vectors (fingerprint TEXT NOT NULL, content_hash TEXT NOT NULL, vector_json TEXT NOT NULL, created_at TEXT NOT NULL, PRIMARY KEY(fingerprint,content_hash))")

    @staticmethod
    def _vector(value: Any) -> list[float]:
        if not isinstance(value, list) or not 1 <= len(value) <= 65536 or any(isinstance(v,bool) or not isinstance(v,(int,float)) or not math.isfinite(v) for v in value):
            raise DataError(502, "Embedding 响应不是有效的有限数值向量")
        result = [float(v) for v in value]
        if not any(result):
            raise DataError(502, "Embedding 服务返回零向量")
        return result

    @logged_operation("embedding", "embed", task_arg="run_id", lifecycle=False)
    def embed(self, texts: list[str], profile: dict, *, model: str | None, run_id: str, check_cancel=lambda: None) -> list[list[float]]:
        if not texts:
            return []
        name = model or profile["model"]
        if not isinstance(name,str) or not name.strip() or len(name)>500:
            raise DataError(422, "请填写 embedding 模型名称")
        if any(not isinstance(t,str) or not t.strip() or len(t)>10000 for t in texts):
            raise DataError(422, "待匹配桥段必须为 1 至 10000 字的文本")
        # Resolve against the original saved profile before applying the model
        # name override. Credentials remain bound to the selected endpoint.
        if target_fingerprint(self.models.snapshot(profile["id"])) != target_fingerprint(profile):
            raise DataError(409, "Embedding 连接已变更，请重新建立联网任务")
        key = self.models._key(profile["id"], profile)
        fp = embedding_target(profile,name)
        hashes = [hashlib.sha256(t.encode()).hexdigest() for t in texts]
        vectors: dict[str,list[float]] = {}
        with self.models._connect() as db:
            for digest in set(hashes):
                row = db.execute("SELECT vector_json FROM research_vectors WHERE fingerprint=? AND content_hash=?",(fp,digest)).fetchone()
                if row:
                    vectors[digest] = self._vector(json.loads(row[0]))
        missing = list(dict.fromkeys(h for h in hashes if h not in vectors))
        by_hash = dict(zip(hashes,texts))
        for start in range(0,len(missing),32):
            check_cancel()
            batch = missing[start:start+32]
            inputs = [by_hash[d] for d in batch]
            estimate = sum(len(t.encode()) for t in inputs)
            usage = _usage(None,0,estimate)
            billing_profile = {**profile,"model":name}
            record = {"id":uuid.uuid4().hex,"run_id":run_id,"chunk_id":"semantic-dedup","profile_id":profile["id"],"provider":profile["provider"],"model":name,"attempt":1,"status":"pending","http_status":None,"started_at":_now(),"duration_ms":None,"usage":usage,"cost":_cost(billing_profile,usage),"error":None,"profile_snapshot":billing_profile,"request_sha256":hashlib.sha256(_json(inputs).encode()).hexdigest(),"operation":"embedding"}
            self.models._write_call(record)
            tick = time.monotonic()
            try:
                if profile["provider"] == "ollama":
                    path = "/embed" if profile["base_url"].endswith("/api") else "/api/embed"
                    payload = {"model":name,"input":inputs,"truncate":False}
                else:
                    path, payload = "/embeddings", {"model":name,"input":inputs,"encoding_format":"float"}
                headers = {"Authorization":"Bearer "+key} if key else {}
                with httpx.Client(transport=self.models.transport,timeout=profile["timeout_seconds"],follow_redirects=False,trust_env=False) as client:
                    response = client.post(profile["base_url"]+path,json=payload,headers=headers)
                record["http_status"] = response.status_code
                if not 200 <= response.status_code < 300:
                    raise DataError(502,f"Embedding 服务返回 HTTP {response.status_code}")
                if len(response.content)>20_000_000:
                    raise DataError(502,"Embedding 响应超过大小限制")
                data = response.json()
                if profile["provider"]=="ollama":
                    received = data.get("embeddings")
                    incoming = data.get("prompt_eval_count")
                else:
                    rows = data.get("data")
                    if not isinstance(rows,list) or sorted(r.get("index",-1) for r in rows)!=list(range(len(inputs))):
                        raise DataError(502,"Embedding 响应缺少输入对应的向量")
                    received = [r.get("embedding") for r in sorted(rows,key=lambda r:r["index"])]
                    incoming = (data.get("usage") or {}).get("prompt_tokens")
                if not isinstance(received,list) or len(received)!=len(inputs):
                    raise DataError(502,"Embedding 响应数量与输入不一致")
                checked = [self._vector(v) for v in received]
                if len({len(v) for v in checked})!=1:
                    raise DataError(502,"Embedding 响应向量维度不一致")
                incoming = incoming if type(incoming) is int and incoming>=0 else None
                record["usage"] = _usage(incoming,0,estimate)
                record["cost"] = _cost(billing_profile,record["usage"])
                record["status"] = "success"
                with self.models._connect() as db:
                    for digest,vector in zip(batch,checked):
                        vectors[digest]=vector
                        db.execute("INSERT OR REPLACE INTO research_vectors VALUES (?,?,?,?)",(fp,digest,_json(vector),_now()))
                    # Cache is reproducible and bounded; rows already used by
                    # this call remain in memory for the returned comparison.
                    db.execute("DELETE FROM research_vectors WHERE rowid IN (SELECT rowid FROM research_vectors ORDER BY created_at DESC LIMIT -1 OFFSET 50000)")
            except DataError as error:
                record_exception("embedding","embed",error,task_id=run_id,http_status=record["http_status"])
                record["status"],record["error"]="error","invalid_or_failed_embedding"
                raise
            except Exception as error:
                record_exception("embedding","embed",error,task_id=run_id,http_status=record["http_status"])
                record["status"],record["error"]="error","embedding_transport_or_format"
                raise DataError(502,"Embedding 请求失败，请检查模型连接及向量接口") from None
            finally:
                record["duration_ms"] = round((time.monotonic()-tick)*1000,3)
                self.models._write_call(record)
        result = [vectors[h] for h in hashes]
        if len({len(v) for v in result})!=1:
            raise DataError(409,"Embedding 模型的向量维度已改变，请切换明确的模型版本")
        return result

    def match(self,candidates:list[dict],existing:list[dict],profile:dict,*,model:str|None,run_id:str,check_cancel=lambda:None)->list[dict]:
        if not candidates:
            return []
        texts=[c["content"] for c in candidates]+[e["values"].get("cliché内容","") for e in existing]
        vectors=self.embed(texts,profile,model=model,run_id=run_id,check_cancel=check_cancel)
        count=len(candidates)
        output=[]
        for i,candidate in enumerate(candidates):
            matches=sorted(({"id":e["id"],"content":texts[count+j],"similarity":round(cosine(vectors[i],vectors[count+j]),6)} for j,e in enumerate(existing)),key=lambda x:(-x["similarity"],x["id"]))[:5]
            best=matches[0] if matches else None
            # In-draft matches prevent two new synonymous candidates from
            # bypassing the catalog index in a single approval transaction.
            peers=[{"candidate_id":candidates[j]["candidate_id"],"similarity":round(cosine(vectors[i],vectors[j]),6)} for j in range(i) if cosine(vectors[i],vectors[j])>=0.78]
            output.append({"candidate_id":candidate["candidate_id"],"matches":matches,"candidate_matches":peers,"recommendation":"reuse" if best and best["similarity"]>=0.9 else "review" if (best and best["similarity"]>=0.78) or peers else "new","thresholds":{"reuse":0.9,"review":0.78},"fingerprint":embedding_target(profile,model)})
        return output
