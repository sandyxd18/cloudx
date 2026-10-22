"""FastAPI side of the catalog client: a peer that cannot be resolved is a 502 with the reason, not a bare 500."""
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from .catalog import CatalogError


def install_catalog_error_handler(app: FastAPI) -> None:
    @app.exception_handler(CatalogError)
    async def _catalog_unavailable(_: Request, exc: CatalogError):
        return JSONResponse(status_code=502, content={"detail": str(exc)})
