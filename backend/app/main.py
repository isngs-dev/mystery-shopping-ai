import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import select

from .config import get_settings
from .db import Base, SessionLocal, engine
from .models import User
from .routers import admin, auth, forms, integrations, media, project_qc, qc, reports, rules
from .security import hash_password
from .services import drive_sync, pipeline, qc_reports
from .services.auto_editor import get_ai_editor
from .services.rules_service import seed_baseline_rules, sync_taxonomy

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
log = logging.getLogger("msai")


def bootstrap() -> None:
    """Create tables if absent (Alembic is the production path), seed baseline rules and the first Super Admin."""
    s = get_settings()
    if s.env == "production" and s.secret_key.startswith("dev-only"):
        raise RuntimeError("Refusing to start in production with the default SECRET_KEY. Set a long random SECRET_KEY.")
    if s.env != "production":
        Base.metadata.create_all(engine)
    db = SessionLocal()
    try:
        seed_baseline_rules(db)
        sync_taxonomy(db)            # every rule is tied to the fixed QC error table; others never run
        if not db.execute(select(User).limit(1)).first() and s.bootstrap_admin_email and s.bootstrap_admin_password:
            db.add(User(email=s.bootstrap_admin_email.lower(), name=s.bootstrap_admin_name, role="super_admin",
                        password_hash=hash_password(s.bootstrap_admin_password)))
            db.commit()
            log.info("Bootstrap Super Admin created: %s", s.bootstrap_admin_email)
        get_ai_editor(db)  # after the first-run check: the AI editor account must not count as "an existing user"
    finally:
        db.close()
    n = pipeline.recover_interrupted_runs()
    if n:
        log.warning("Marked %d interrupted QC run(s) as failed", n)
    from .services import project_qc as project_qc_service

    db = SessionLocal()
    try:
        n = project_qc_service.ensure_coordinator_access(db)
    finally:
        db.close()
    if n:
        log.info("Coordinators were given access to %d project(s)", n)
    n = drive_sync.recover_interrupted_jobs()
    if n:
        log.warning("Closed %d interrupted Drive import(s); the next auto-sync starts at once", n)
    if drive_sync.ensure_default_on():
        log.info("Drive auto-sync switched on (DRIVE_AUTO_SYNC=true)")


@asynccontextmanager
async def lifespan(_: FastAPI):
    bootstrap()
    s = get_settings()
    if s.drive_scheduler_enabled and s.env != "test":
        drive_sync.scheduler.start()
    if s.qc_reports_auto and s.env != "test":
        qc_reports.worker.start()
    yield
    drive_sync.scheduler.stop()
    qc_reports.worker.stop()


def create_app() -> FastAPI:
    s = get_settings()
    app = FastAPI(title=s.app_name, version="1.0.0", lifespan=lifespan, docs_url="/api/docs", openapi_url="/api/openapi.json")
    app.add_middleware(CORSMiddleware, allow_origins=s.cors_origin_list, allow_credentials=True,
                       allow_methods=["*"], allow_headers=["*"])

    @app.middleware("http")
    async def security_headers(request: Request, call_next):
        resp = await call_next(request)
        resp.headers.setdefault("X-Content-Type-Options", "nosniff")
        resp.headers.setdefault("X-Frame-Options", "SAMEORIGIN")
        resp.headers.setdefault("Referrer-Policy", "same-origin")
        return resp

    @app.exception_handler(Exception)
    async def unhandled(_: Request, exc: Exception):
        log.exception("Unhandled error: %s", exc)
        return JSONResponse({"detail": "Unexpected server error. It has been logged."}, status_code=500)

    for r in (auth.router, admin.router, forms.router, qc.router, rules.router, reports.router, integrations.router, media.router, project_qc.router):
        app.include_router(r)

    dist = Path(__file__).resolve().parent.parent.parent / "frontend" / "dist"
    if dist.is_dir():  # serve the built SPA in single-process deployments
        app.mount("/assets", StaticFiles(directory=dist / "assets"), name="assets")

        @app.get("/{path:path}", include_in_schema=False)
        def spa(path: str):
            if path.startswith("api/"):
                return JSONResponse({"detail": "Not found"}, status_code=404)
            f = dist / path
            return FileResponse(f if f.is_file() else dist / "index.html")

    return app


app = create_app()
