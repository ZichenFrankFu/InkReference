"""Metadata-only task center routes; controls never resume model execution."""
from __future__ import annotations

import threading
from typing import Annotated, Literal

from fastapi import APIRouter, Query

from .task_hub import TaskHub


def install_task_routes(application, runtime):
    router = APIRouter(prefix="/api/studio", tags=["tasks"])
    lock = threading.RLock()
    service = None

    def tasks():
        nonlocal service
        with lock:
            if service is None:
                service = TaskHub(runtime, repair_getter=lambda: application.state.quick_repair())
            return service

    application.state.task_hub = tasks

    @router.get("/tasks")
    def listing(q: Annotated[str, Query(max_length=500)] = "", kind: str = "", status: str = "",
                offset: Annotated[int, Query(ge=0)] = 0, limit: Annotated[int, Query(ge=1, le=100)] = 30):
        return tasks().list(q=q, kind=kind, status=status, offset=offset, limit=limit)

    @router.get("/tasks/{kind}/{identifier}")
    def detail(kind: str, identifier: str):
        return tasks().get(kind, identifier)

    @router.post("/tasks/{kind}/{identifier}/{action}")
    def control(kind: str, identifier: str, action: Literal["pause", "resume", "terminate"]):
        return tasks().control(kind, identifier, action)

    application.include_router(router)
