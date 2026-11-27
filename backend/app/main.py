import logging

from fastapi import APIRouter, FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import text

from app import __version__
from app.api.v1 import areas, auth, campaigns, collectors, forms, sync, teams, users, visit_points
from app.core.config import get_settings
from app.db.session import engine

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")

settings = get_settings()

app = FastAPI(
    title="RAGOH API",
    version=__version__,
    description="Field supervision, survey and route-tracking API.",
    docs_url="/docs",
    openapi_url="/openapi.json",
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

api = APIRouter(prefix="/api/v1")
for module in (auth, users, areas, campaigns, teams, visit_points, forms, sync, collectors):
    api.include_router(module.router)
app.include_router(api)


@app.get("/health", tags=["system"])
def health() -> dict[str, str]:
    with engine.connect() as conn:
        conn.execute(text("SELECT 1"))
    return {"status": "ok"}


@app.get("/version", tags=["system"])
def version() -> dict[str, str]:
    return {"version": __version__, "git_sha": settings.git_sha, "env": settings.env}
