from contextlib import asynccontextmanager
import logging
import os

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.database import Base, engine, test_database_connection

from app.models.user_model import User
from app.models.user_preference_model import UserPreference
from app.models.resume_model import Resume
from app.models.job_model import Job
from app.models.match_model import MatchScore
from app.models.optimized_resume_model import OptimizedResume
from app.models.email_log_model import EmailLog
from app.models.ats_optimization_workflow_model import AtsOptimizationWorkflow  # noqa: F401
from app.models.skill_confirmation_model import JobRequirementAnalysis, UserConfirmedSkill  # noqa: F401
from app.models.password_reset_model import PasswordResetToken  # noqa: F401

from app.routes.auth_routes import router as auth_router
from app.routes.preference_routes import router as preference_router
from app.routes.resume_routes import router as resume_router
from app.routes.job_routes import router as job_router
from app.routes.match_routes import router as match_router
from app.routes.email_routes import router as email_router
from app.routes.scheduler_routes import router as scheduler_router
from app.routes.ats_optimizer_routes import router as ats_optimizer_router
from app.routes.ats_optimizer_routes import compat_router as ats_compat_router
from app.routes.optimizer_edit_routes import router as optimizer_edit_router

from app.scheduler.daily_scheduler import start_daily_scheduler, stop_daily_scheduler
from app.services.llm_client import LLMClient
from app.services.schema_upgrades import ensure_ats_schema


logger = logging.getLogger("uvicorn.error")


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("APP_STARTUP_BEGIN")
    try:
        Base.metadata.create_all(bind=engine)
        logger.info("DATABASE_INIT_OK")
    except Exception:
        logger.exception("DATABASE_INIT_FAILED")
        raise

    try:
        ensure_ats_schema()
        logger.info("SCHEMA_UPGRADE_OK")
    except Exception:
        # Schema upgrade must not leave the API permanently hung/unavailable.
        # Log and continue — routes that need new columns will surface clear errors.
        logger.exception("SCHEMA_UPGRADE_FAILED")

    try:
        start_daily_scheduler()
        logger.info("SCHEDULER_START_OK")
    except Exception:
        logger.exception("SCHEDULER_START_FAILED")

    try:
        # Config-only — never probe Ollama/network during startup.
        llm = LLMClient()
        logger.info(
            "LLM_CONFIG_LOADED configured=%s provider=%s model=%s service=%s "
            "base_url=%s timeout_s=%s generation_budget_s=%s",
            llm.enabled,
            llm.provider,
            llm.model,
            llm.service_name or "n/a",
            llm.config.get("base_url"),
            llm.config.get("timeout"),
            llm.config.get("generation_timeout_seconds"),
        )
    except Exception:
        logger.exception("LLM_CONFIG_LOAD_FAILED")

    logger.info("ROUTERS_REGISTERED")
    logger.info("APP_STARTUP_COMPLETE")
    yield
    stop_daily_scheduler()


app = FastAPI(
    title="AI Job Search and Resume Optimization API",
    description="Backend API for job search, resume optimization, matching, email, and scheduler workflow agents",
    version="1.0.0",
    lifespan=lifespan
)

cors_origins = [
    origin.strip()
    for origin in os.getenv(
        "CORS_ORIGINS",
        "http://localhost:5173,http://127.0.0.1:5173,"
        "http://localhost:5174,http://127.0.0.1:5174,"
        "http://localhost:5175,http://127.0.0.1:5175,"
        "http://localhost:5176,http://127.0.0.1:5176",
    ).split(",")
    if origin.strip()
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth_router)
app.include_router(preference_router)
app.include_router(resume_router)
app.include_router(job_router)
app.include_router(match_router)
app.include_router(ats_optimizer_router)
app.include_router(ats_compat_router)
app.include_router(optimizer_edit_router)
app.include_router(email_router)
app.include_router(scheduler_router)


@app.exception_handler(StarletteHTTPException)
async def http_exception_handler(request: Request, exc: StarletteHTTPException):
    return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail})


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception):
    """Always return a valid JSON response — never close the socket empty."""
    logger.exception("Unhandled error path=%s", request.url.path)
    return JSONResponse(
        status_code=500,
        content={
            "detail": "Internal server error",
            "error_code": "INTERNAL_ERROR",
        },
    )


@app.get("/")
def home():
    return {
        "message": "Welcome to AI Job Search and Resume Optimization API"
    }


def _llm_config_snapshot(*, probe: bool = False) -> dict:
    """LLM config for health endpoints — never exposes secrets."""
    try:
        llm_client = LLMClient()
        if probe:
            return llm_client.llm_status(probe=True)
        return {
            "configured": llm_client.enabled,
            "provider": llm_client.provider,
            "model": llm_client.model,
            "base_url": llm_client.config.get("base_url") or "",
            "service": llm_client.service_name,
            "timeout_seconds": llm_client.config.get("timeout"),
            "reachable": None,
            "model_found": None,
        }
    except Exception as error:
        return {
            "configured": False,
            "provider": "none",
            "model": "deterministic-fallback",
            "base_url": "",
            "reachable": None,
            "error": type(error).__name__,
        }


@app.get("/health")
def health_check():
    """Lightweight liveness check — never blocks on ATS/LLM/job-search work.

    Returns HTTP 200 when the process is up and DB responds.
    Returns HTTP 503 JSON when DB is unavailable (never an empty socket close).
    """
    response = {
        "status": "Backend is running",
        "llm": _llm_config_snapshot(probe=False),
    }
    try:
        result = test_database_connection()
    except Exception as error:
        logger.exception("health DB check failed")
        return JSONResponse(
            status_code=503,
            content={
                "status": "degraded",
                "database": "not connected",
                "error_code": "DATABASE_UNAVAILABLE",
                "detail": type(error).__name__,
                "llm": response["llm"],
            },
        )

    if result != 1:
        return JSONResponse(
            status_code=503,
            content={
                "status": "degraded",
                "database": "not connected",
                "error_code": "DATABASE_UNAVAILABLE",
                "llm": response["llm"],
            },
        )

    response["database"] = "connected"
    return response


@app.get("/health/llm")
def llm_health_check():
    """Optional LLM reachability probe for diagnostics (no secrets)."""
    status = _llm_config_snapshot(probe=True)
    http_status = 200
    if status.get("configured") and not status.get("reachable"):
        http_status = 503
    return JSONResponse(
        status_code=http_status,
        content={
            "status": "ok" if http_status == 200 else "degraded",
            "llm": status,
        },
    )


@app.get("/db-health")
def database_health_check():
    try:
        result = test_database_connection()
    except Exception as error:
        logger.exception("db-health failed")
        return JSONResponse(
            status_code=503,
            content={
                "database": "not connected",
                "error_code": "DATABASE_UNAVAILABLE",
                "detail": type(error).__name__,
            },
        )

    if result == 1:
        return {"database": "connected"}

    return JSONResponse(
        status_code=503,
        content={"database": "not connected", "error_code": "DATABASE_UNAVAILABLE"},
    )


@app.post("/init-db")
def init_database():
    Base.metadata.create_all(bind=engine)

    return {
        "message": "Database tables created successfully"
    }
