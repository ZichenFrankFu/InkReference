"""Read-only capability registry for the shared dynamic interaction shell."""
from fastapi import APIRouter

from .dynamic_functions import operation_definition, operation_registry


def create_dynamic_router():
    router = APIRouter(prefix="/api/studio/dynamic-functions")

    @router.get("")
    def registry():
        return operation_registry()

    @router.get("/{operation_id}")
    def definition(operation_id: str):
        return operation_definition(operation_id)

    return router
