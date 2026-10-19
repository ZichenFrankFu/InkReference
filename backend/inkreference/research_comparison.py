"""Durable model comparisons; original drafts stay separate from reviewed adoption."""
from __future__ import annotations

import copy
import json
import uuid

from . import catalog_schema_v11 as catalog_schema
from .database import DataError
from .models import _now


def _json(value):
    return json.dumps(value,ensure_ascii=False,separators=(",",":"),allow_nan=False)


def _values(row):
    return row.get("catalog_values") or row.get("values",{})


class ResearchComparisonMixin:
    def _init_comparisons(self):
        with self.models._connect() as db:
            db.execute("CREATE TABLE IF NOT EXISTS research_comparisons (id TEXT PRIMARY KEY,revision INTEGER NOT NULL,title TEXT NOT NULL,author TEXT NOT NULL,target_work_id TEXT,status TEXT NOT NULL,selected_job_id TEXT,commit_json TEXT,created_at TEXT NOT NULL,updated_at TEXT NOT NULL)")
            db.execute("CREATE TABLE IF NOT EXISTS research_comparison_members (comparison_id TEXT NOT NULL,job_id TEXT NOT NULL UNIQUE,role TEXT NOT NULL,source_job_id TEXT,comparison_label TEXT NOT NULL DEFAULT '',PRIMARY KEY(comparison_id,job_id))")
            if "comparison_label" not in {row[1] for row in db.execute("PRAGMA table_info(research_comparison_members)")}:
                db.execute("ALTER TABLE research_comparison_members ADD COLUMN comparison_label TEXT NOT NULL DEFAULT ''")
            db.execute("UPDATE research_comparisons SET status='reviewing',revision=revision+1,updated_at=? WHERE status='approving'",(_now(),))

    def _comparison_member(self,job_id,db=None):
        if db is None:
            with self.models._connect() as connection:return self._comparison_member(job_id,connection)
        row=db.execute("SELECT comparison_id,role AS comparison_role,source_job_id,comparison_label FROM research_comparison_members WHERE job_id=?",(job_id,)).fetchone()
        return dict(row) if row else {}

    def _comparison_row(self,identifier,db=None):
        if db is None:
            with self.models._connect() as connection:return self._comparison_row(identifier,connection)
        row=db.execute("SELECT * FROM research_comparisons WHERE id=?",(identifier,)).fetchone()
        if row is None:raise DataError(404,"模型对照不存在")
        return dict(row)

    def create_comparison(self,*,title,author,target_work_id=None):
        from .research import _matching_works,_text
        title=_text(title,"作品名",500);author=_text(author,"作者",500)
        catalog=self.catalog_snapshot()
        if target_work_id is not None and target_work_id not in {w["id"] for w in _matching_works(catalog,title,author)}:
            raise DataError(422,"对照目标的作品名或作者不符")
        identifier="compare-"+uuid.uuid4().hex;now=_now()
        with self.models._connect() as db:
            db.execute("INSERT INTO research_comparisons VALUES (?,1,?,?,?,'open',NULL,NULL,?,?)",(identifier,title,author,target_work_id,now,now))
        return self.get_comparison(identifier)

    def add_comparison_job(self,identifier,*,job_id,expected_revision,label=None):
        from .research import _norm,_text
        if label is not None:label=_text(label,"版本名称",100,True)
        with self.models._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            group=self._comparison_row(identifier,db)
            if group["revision"]!=expected_revision:raise DataError(409,"模型对照已修改，请刷新")
            if group["status"] not in {"open","reviewing"}:raise DataError(409,"已采纳的对照不能再添加版本")
            row=self._row(job_id,db);payload=json.loads(row["payload_json"])
            if row["status"] in {"approved","approving"}:raise DataError(409,"已入库的任务不能作为对照版本")
            if _norm(payload["title"])!=_norm(group["title"]) or _norm(payload["author"])!=_norm(group["author"]):raise DataError(422,"对照版本的作品名或作者不符")
            if group["target_work_id"] and payload.get("target_work_id") not in {None,group["target_work_id"]}:raise DataError(422,"对照版本指向不同作品版本")
            member=self._comparison_member(job_id,db)
            if member:
                if member["comparison_id"]==identifier and member["comparison_role"]=="candidate":
                    if label is not None:
                        db.execute("UPDATE research_comparison_members SET comparison_label=? WHERE comparison_id=? AND job_id=?",(label,identifier,job_id))
                        db.execute("UPDATE research_comparisons SET revision=revision+1,updated_at=? WHERE id=?",(_now(),identifier))
                        db.commit()
                    return self.get_comparison(identifier)
                raise DataError(409,"该任务已属于其他对照或审核版本")
            count=db.execute("SELECT COUNT(*) FROM research_comparison_members WHERE comparison_id=? AND role='candidate'",(identifier,)).fetchone()[0]
            if count>=4:raise DataError(422,"每组最多保留4个模型版本")
            db.execute("INSERT INTO research_comparison_members(comparison_id,job_id,role,source_job_id,comparison_label) VALUES (?,?,'candidate',NULL,?)",(identifier,job_id,label or ""))
            db.execute("UPDATE research_comparisons SET revision=revision+1,updated_at=? WHERE id=?",(_now(),identifier))
        return self.get_comparison(identifier)

    def list_comparisons(self,*,title=None,author=None):
        from .research import _norm
        with self.models._connect() as db:
            rows=[dict(row) for row in db.execute("SELECT id,revision,title,author,target_work_id,status,selected_job_id,created_at,updated_at FROM research_comparisons ORDER BY updated_at DESC LIMIT 100")]
        return {"items":[r for r in rows if (title is None or _norm(title)==_norm(r["title"])) and (author is None or _norm(author)==_norm(r["author"]))]}

    def get_comparison(self,identifier):
        group=self._comparison_row(identifier)
        with self.models._connect() as db:
            members=[dict(row) for row in db.execute("SELECT job_id,role,source_job_id FROM research_comparison_members WHERE comparison_id=? ORDER BY rowid",(identifier,))]
        catalog=self.catalog_snapshot();jobs=[]
        for member in members:
            if member["role"]!="candidate":continue
            job=self.get(member["job_id"])
            job["score_scenarios"]=self._score_scenarios(job,catalog)
            jobs.append(job)
        selected=self.get(group["selected_job_id"]) if group["selected_job_id"] else None
        if selected:selected["score_scenarios"]=self._score_scenarios(selected,catalog)
        return {k:v for k,v in group.items() if k!="commit_json"} | {"jobs":jobs,"selected_job":selected,"commit_result":json.loads(group["commit_json"]) if group["commit_json"] else None,"catalog_revision":catalog["revision"]}

    def _score_scenarios(self,job,catalog):
        from .research import _matching_works
        draft=job.get("draft")
        if not isinstance(draft,dict):return []
        work=draft["work"]
        values={"讨论热度":_json(work.get("discussion_heat")),"商业规模":_json(work.get("market_scale"))}
        heat,missing=catalog_schema.work_heat(values)
        def bound(key,end):
            value=work.get(key)
            return value.get(end,1) if isinstance(value,dict) else 1
        low=(bound("discussion_heat","min")+bound("market_scale","min"))/2
        high=(bound("discussion_heat","max")+bound("market_scale","max"))/2
        matches=_matching_works(catalog,job["title"],job["author"])
        target=job.get("target_work_id") or (matches[0]["id"] if len(matches)==1 else None)
        # Multiple editions cannot be silently scored as a known update.
        assumption="update" if target else "new_edition" if matches else "new_work"
        weights={row["id"]:catalog_schema.work_heat(_values(row))[0] for row in catalog["works"]}
        current_total=sum(weights.values());other_total=current_total-weights.get(target,0)
        def score(existing_links,value):
            other_hit=sum(weight for identifier,weight in weights.items() if identifier!=target and identifier in existing_links)
            denominator=other_total+value
            return 100*(other_hit+value)/denominator if denominator else 0
        output=[]
        semantics={item["candidate_id"]:item for item in job.get("semantic_matches") or []}
        existing={row["id"]:row for row in catalog["cliches"]}
        for candidate in draft["cliches"]:
            options=[]
            for match in semantics.get(candidate["candidate_id"],{}).get("matches",[]):
                row=existing.get(match["id"])
                if row is None:continue
                links=_values(row).get("关联作品",[])
                if isinstance(links,str):
                    try:links=json.loads(links)
                    except ValueError:links=[]
                linked={link if isinstance(link,str) else link.get("作品ID") for link in links if isinstance(link,(str,dict))} if isinstance(links,list) else set()
                current=100*sum(weights.get(identifier,0) for identifier in linked)/current_total if current_total else 0
                options.append({"id":row["id"],"current_score":round(current,4),"projected_score":{"mid":round(score(linked,heat),4),"min":round(score(linked,low),4),"max":round(score(linked,high),4)}})
            output.append({"candidate_id":candidate["candidate_id"],"new_cliche_score":{"mid":round(score(set(),heat),4),"min":round(score(set(),low),4),"max":round(score(set(),high),4)},"existing_matches":options,"formula":catalog_schema.SCORE_FORMULA,"catalog_revision":catalog["revision"],"assumption":assumption,"work_id":target,"proposed_work_heat":heat,"missing_heat":missing})
        return output

    def compose_comparison(self,identifier,*,source_job_id,expected_revision):
        from .research import _character_matches,_matching_works
        with self.models._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            group=self._comparison_row(identifier,db)
            if group["revision"]!=expected_revision:raise DataError(409,"模型对照已修改，请刷新后采纳")
            if group["status"] not in {"open","reviewing"}:raise DataError(409,"该组已经采纳或正在入库")
            member=self._comparison_member(source_job_id,db)
            if member.get("comparison_id")!=identifier or member.get("comparison_role")!="candidate":raise DataError(422,"请选择该组中的原始模型版本")
            source=self._row(source_job_id,db);p=json.loads(source["payload_json"])
            if source["status"]!="needs_review" or p.get("draft") is None:raise DataError(409,"请等待该版本完成资料整理")
            self.validate_draft(p["draft"],p)
            if not p["draft"]["identity"]["confirmed"]:raise DataError(422,"该版本尚未确认作品身份")
            job_id=uuid.uuid4().hex;now=_now();p=copy.deepcopy(p)
            p["output_metering"]=p.get("output_metering") or {"kind":"external_unmetered" if p.get("mode")=="semi" else "app_api","input_tokens":None,"output_tokens":None,"source_job_id":source_job_id}
            p.update(mode="semi",manual_review_required=True,error=None,commit_result=None,source_job_id=source_job_id)
            p["profiles"]={"embedding":p["profiles"]["embedding"]} if p.get("profiles",{}).get("embedding") else {}
            p["targets"]={"embedding":p["targets"]["embedding"]} if p.get("targets",{}).get("embedding") else {}
            p["verify_sources"]=False
            p["completed"]=["identify","search","fetch","draft","dedup"]
            p["progress"]={"completed":5,"total":5,"label":"采纳版本待审核"}
            p["messages"]=[{"role":"user","content":"采纳对照版本，等待最终审核。","created_at":now}]
            # Other versions remain immutable; their sources are available for
            # explicit manual edits without silently merging model assertions.
            used={s["id"]:(s["url"],s.get("excerpt"),s.get("fetch_status")) for s in p["sources"]}
            for row in db.execute("SELECT r.id,r.payload_json FROM research_jobs r JOIN research_comparison_members m ON m.job_id=r.id WHERE m.comparison_id=? AND m.role='candidate' AND r.id!=?",(identifier,source_job_id)):
                other=json.loads(row["payload_json"])
                for source in other.get("sources",[]):
                    if source["id"] in used and used[source["id"]]==(source["url"],source.get("excerpt"),source.get("fetch_status")):continue
                    source=copy.deepcopy(source)
                    if source["id"] in used:source["id"]="cmp_"+row["id"][:8]+"_"+source["id"]
                    source["comparison_source_job_id"]=row["id"]
                    used[source["id"]]=(source["url"],source.get("excerpt"),source.get("fetch_status"));p["sources"].append(source)
            source_catalog_revision=p.get("catalog_revision")
            catalog=self.catalog_snapshot();p["catalog_revision"]=catalog["revision"]
            p["work_matches"]=_matching_works(catalog,p["title"],p["author"])
            p["target_work_id"]=group["target_work_id"] or p.get("target_work_id")
            work_id=p["target_work_id"] or (p["work_matches"][0]["id"] if len(p["work_matches"])==1 else None)
            p["character_matches"]=_character_matches(p["draft"]["characters"],catalog,work_id)
            # Reuse semantic suggestions only while the indexed catalog is
            # unchanged; otherwise require a fresh manual review instead.
            if source_catalog_revision!=catalog["revision"]:
                p["dedup_mode"]="manual";p["semantic_matches"]=self._manual_matches(p,catalog)
            db.execute("INSERT INTO research_jobs(id,revision,status,stage,cancel_requested,stop_kind,payload_json,created_at,updated_at,title,author) VALUES (?,1,'needs_review','review',0,'cancelled',?,?,?,?,?)",(job_id,_json(p),now,now,p["title"],p["author"]))
            db.execute("INSERT INTO research_comparison_members(comparison_id,job_id,role,source_job_id,comparison_label) VALUES (?,?,'review',?,?)",(identifier,job_id,source_job_id,member.get("comparison_label", "")))
            db.execute("UPDATE research_comparisons SET status='reviewing',selected_job_id=?,revision=revision+1,updated_at=? WHERE id=?",(job_id,now,identifier))
        return self.get(job_id)

    def _claim_comparison(self,job_id):
        with self.models._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            member=self._comparison_member(job_id,db)
            if not member:return None
            group=self._comparison_row(member["comparison_id"],db)
            if member["comparison_role"]!="review" or group["selected_job_id"]!=job_id or group["status"]!="reviewing":raise DataError(409,"请仅入库当前采纳的审核版本")
            db.execute("UPDATE research_comparisons SET status='approving',revision=revision+1,updated_at=? WHERE id=?",(_now(),group["id"]))
            return group["id"]

    def _finish_comparison(self,identifier,*,result=None):
        if not identifier:return
        with self.models._connect() as db:
            db.execute("UPDATE research_comparisons SET status=?,commit_json=?,revision=revision+1,updated_at=? WHERE id=?",("approved" if result is not None else "reviewing",_json(result) if result is not None else None,_now(),identifier))
