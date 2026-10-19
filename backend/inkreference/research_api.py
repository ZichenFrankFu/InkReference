"""Lazy HTTP surface for reviewed web-based catalog updates."""
from __future__ import annotations

from typing import Any

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


def create_research_router(service_or_getter):
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
