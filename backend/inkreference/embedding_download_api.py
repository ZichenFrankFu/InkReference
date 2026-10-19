"""Lazy local embedding download HTTP API."""
from fastapi import APIRouter
from pydantic import BaseModel, ConfigDict, Field


class DownloadCreate(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    model: str = Field(min_length=1, max_length=220)
    profile_id: str | None = Field(default=None, min_length=1, max_length=100)
    base_url: str | None = Field(default=None, max_length=2048)
    expected_target_fingerprint: str = Field(min_length=64, max_length=64)


def create_embedding_download_router(getter):
    router = APIRouter(prefix='/api/studio/embedding-downloads')
    def service(): return getter() if callable(getter) else getter

    @router.get('/targets')
    def target(model: str, profile_id: str | None = None, base_url: str | None = None):
        return service().target(model, profile_id, base_url)

    @router.get('/jobs')
    def jobs(): return service().list()

    @router.post('/jobs', status_code=201)
    def create(body: DownloadCreate): return service().create(**body.model_dump())

    @router.get('/jobs/{identity}')
    def job(identity: str): return service().get(identity)

    @router.post('/jobs/{identity}/cancel')
    def cancel(identity: str): return service().cancel(identity)

    @router.post('/jobs/{identity}/retry')
    def retry(identity: str): return service().retry(identity)

    @router.post('/jobs/{identity}/register')
    def register(identity: str): return service().register(identity)

    return router
