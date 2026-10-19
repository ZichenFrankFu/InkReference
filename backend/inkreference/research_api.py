"""Lazy HTTP surface for reviewed web-based catalog updates."""
from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, Query
from pydantic import BaseModel, ConfigDict, Field


class ResearchBody(BaseModel):
    model_config=ConfigDict(extra="forbid",strict=True)


class SearchSettings(ResearchBody):
    provider:str
    endpoint:str=""
    credential_mode:str="session"
    key_env:str=""
    api_key:str|None=None


class ResearchCreate(ResearchBody):
    title:str=Field(min_length=1,max_length=500)
    author:str=Field(min_length=1,max_length=500)
    model_profile_id:str
    embedding_profile_id:str
    embedding_model:str|None=None
    message:str=Field(default="",max_length=4000)
    target_work_id:str|None=None
    remote_consent:bool=False
    expected_fingerprints:dict[str,str]|None=None
    start:bool=True
    mode:Literal["automatic"]="automatic"


class ResearchChat(ResearchBody):
    message:str=Field(min_length=1,max_length=4000)
    expected_revision:int=Field(ge=1)


class ResearchDraft(ResearchBody):
    draft:dict[str,Any]
    expected_revision:int=Field(ge=1)


class ResearchApproval(ResearchBody):
    expected_revision:int=Field(ge=1)
    expected_catalog_revision:str
    decisions:dict[str,Any]


class ComparisonCreate(ResearchBody):
    title:str=Field(min_length=1,max_length=500)
    author:str=Field(min_length=1,max_length=500)
    target_work_id:str|None=None


class ComparisonAttach(ResearchBody):
    job_id:str=Field(min_length=1,max_length=100)
    expected_revision:int=Field(ge=1)
    label:str|None=Field(default=None,max_length=100)


class ComparisonCompose(ResearchBody):
    source_job_id:str=Field(min_length=1,max_length=100)
    expected_revision:int=Field(ge=1)


class SemiPrompt(ResearchBody):
    title:str=Field(min_length=1,max_length=500)
    author:str=Field(min_length=1,max_length=500)
    message:str=Field(default="",max_length=4000)
    target_work_id:str|None=None


class SemiReply(ResearchBody):
    reply:str=Field(min_length=1,max_length=300000)
    expected_revision:int=Field(ge=1)
    verify_sources:bool=False
    embedding_profile_id:str|None=None
    embedding_model:str|None=None
    remote_consent:bool=False
    expected_embedding_fingerprint:str|None=None
    prepared_id:str|None=None


def create_research_router(service_or_getter,workbench_getter=None):
    """Accept a lazy getter so app startup never opens the research stores."""
    router=APIRouter(prefix="/api/studio/research")
    def service():return service_or_getter() if callable(service_or_getter) else service_or_getter

    @router.get("/settings")
    def settings():return service().sources.settings()

    @router.put("/settings")
    def save_settings(body:SearchSettings):return service().sources.save_settings(body.model_dump(exclude_unset=True))

    @router.get("/targets")
    def targets(model_profile_id:str,embedding_profile_id:str,embedding_model:str|None=None):
        return service().targets(model_profile_id,embedding_profile_id,embedding_model)

    @router.post("/comparisons")
    def create_comparison(body:ComparisonCreate):return service().create_comparison(**body.model_dump())

    @router.get("/comparisons")
    def comparisons(title:str|None=None,author:str|None=None):return service().list_comparisons(title=title,author=author)

    @router.get("/comparisons/{identifier}")
    def comparison(identifier:str):return service().get_comparison(identifier)

    @router.post("/comparisons/{identifier}/jobs")
    def comparison_attach(identifier:str,body:ComparisonAttach):return service().add_comparison_job(identifier,**body.model_dump())

    @router.post("/comparisons/{identifier}/compose")
    def comparison_compose(identifier:str,body:ComparisonCompose):return service().compose_comparison(identifier,**body.model_dump())

    @router.post("/semi/prompt")
    def semi_prompt(body:SemiPrompt):return service().create_semi(**body.model_dump())

    @router.post("/jobs/{job_id}/reply")
    def semi_reply(job_id:str,body:SemiReply):
        params=body.model_dump(exclude={"prepared_id"})
        if body.prepared_id:
            if workbench_getter is None:
                from .database import DataError
                raise DataError(409,"提示词审核服务未配置")
            workbench=workbench_getter()
            context=workbench.validate_for_confirmation("catalog",body.prepared_id,reply=body.reply)
            if context["job_id"]!=job_id:
                from .database import DataError
                raise DataError(409,"回复任务与本次提示词不符")
            params["prompt_review"]=workbench.catalog_confirmation(body.prepared_id,remote_consent=body.remote_consent,expected_embedding_fingerprint=body.expected_embedding_fingerprint)
            result=service().reply(job_id,**params)
            workbench.control_preparation(body.prepared_id,"complete")
            return result
        return service().reply(job_id,**params)

    @router.get("/embedding-target")
    def embedding_target_info(embedding_profile_id:str,embedding_model:str|None=None):
        from .semantic_search import embedding_target
        profile=service().models.snapshot(embedding_profile_id)
        return {"profile_id":profile["id"],"model":embedding_model or profile["model"],"endpoint":profile["base_url"],"connection_mode":profile["connection_mode"],"fingerprint":embedding_target(profile,embedding_model)}

    @router.get("/jobs")
    def jobs():return service().list()

    @router.post("/jobs")
    def create(body:ResearchCreate):return service().create(**body.model_dump())

    @router.get("/jobs/{job_id}")
    def get(job_id:str):return service().get(job_id)

    @router.post("/jobs/{job_id}/chat")
    def chat(job_id:str,body:ResearchChat):return service().chat(job_id,**body.model_dump())

    @router.put("/jobs/{job_id}/draft")
    def draft(job_id:str,body:ResearchDraft):return service().save_draft(job_id,**body.model_dump())

    @router.post("/jobs/{job_id}/start")
    @router.post("/jobs/{job_id}/resume")
    def start(job_id:str):return service().start(job_id)

    @router.post("/jobs/{job_id}/cancel")
    def cancel(job_id:str):return service().cancel(job_id)

    @router.post("/jobs/{job_id}/approve")
    def approve(job_id:str,body:ResearchApproval):return service().approve(job_id,**body.model_dump())

    return router
