from fastapi import APIRouter, FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import text

from app import __version__
from app.api.v1 import (
    analytics,
    areas,
    audit_logs,
    auth,
    campaigns,
    collectors,
    devices,
    exports,
    flags,
    forms,
    integrations,
    live,
    routes,
    sync,
    teams,
    users,
    visit_points,
)
from app.core.config import get_settings
from app.core.logging import scrub_event, setup_logging
from app.core.middleware import GzipRequestMiddleware
from app.db.session import engine

settings = get_settings()
setup_logging(settings.log_json)
if settings.sentry_dsn:
    import sentry_sdk

    sentry_sdk.init(
        dsn=settings.sentry_dsn,
        environment=settings.env,
        release=f"ragoh-api@{__version__}+{settings.git_sha}",
        traces_sample_rate=0.0,
        before_send=scrub_event,  # type: ignore[arg-type]  # never send bodies, headers or users (locations, answers)
    )

app = FastAPI(
    title="RAGOH API",
    version=__version__,
    description="Field supervision, survey and route-tracking API.",
    docs_url="/docs",
    openapi_url="/openapi.json",
)
app.add_middleware(GzipRequestMiddleware)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

api = APIRouter(prefix="/api/v1")
for module in (
    auth,
    users,
    areas,
    campaigns,
    teams,
    visit_points,
    forms,
    sync,
    collectors,
    flags,
    routes,
    live,
    analytics,
    exports,
    devices,
    audit_logs,
    integrations,
):
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
