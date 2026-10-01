from contextlib import asynccontextmanager
import logging
import os

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.database import Base, engine, test_database_connection

from app.models.user_model import User
from app.models.user_preference_model import UserPreference
from app.models.resume_model import Resume
from app.models.job_model import Job
from app.models.match_model import MatchScore
from app.models.optimized_resume_model import OptimizedResume
from app.models.email_log_model import EmailLog
from app.models.summary_delivery_model import SummaryDeliveryAttempt  # noqa: F401
from app.models.email_summary_preference_model import EmailSummaryPreference  # noqa: F401
from app.models.scheduled_daily_summary_model import ScheduledDailySummary  # noqa: F401
from app.models.ats_optimization_workflow_model import AtsOptimizationWorkflow  # noqa: F401
from app.models.skill_confirmation_model import JobRequirementAnalysis, UserConfirmedSkill  # noqa: F401

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
from app.routes.job_discovery_routes import router as job_discovery_router

from app.scheduler.daily_scheduler import start_daily_scheduler, stop_daily_scheduler
from app.scheduler.updated_job_scheduler_service import schedule_updated_job_poll, stop_updated_job_poll
from app.scheduler.gmail_auto_scan_scheduler import schedule_gmail_auto_scans
from app.services.llm_client import LLMClient
from app.services.schema_upgrades import ensure_ats_schema
from app.services.job_discovery_schema import ensure_job_discovery_schema
from app.services.email_summary_schema import ensure_email_summary_schema

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    Base.metadata.create_all(bind=engine)
    ensure_ats_schema()
    ensure_job_discovery_schema()
    ensure_email_summary_schema()
    start_daily_scheduler()
    schedule_updated_job_poll()
    schedule_gmail_auto_scans()
    yield
    stop_updated_job_poll()
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
        "http://localhost:5173,http://127.0.0.1:5173",
    ).split(",")
    if origin.strip()
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    # Content-Disposition isn't in the CORS "safelisted" response headers by
    # default — without this, the frontend's resume-download fetch() can't
    # read the server-generated filename (falls back to a generic name).
    expose_headers=["Content-Disposition", "X-Document-Version"],
)

app.include_router(auth_router)
app.include_router(preference_router)
app.include_router(resume_router)
app.include_router(job_router)
app.include_router(match_router)
app.include_router(ats_optimizer_router)
app.include_router(ats_compat_router)
app.include_router(optimizer_edit_router)
app.include_router(job_discovery_router)
app.include_router(email_router)
app.include_router(scheduler_router)


@app.get("/")
def home():
    return {
        "message": "Welcome to AI Job Search and Resume Optimization API"
    }


@app.get("/health")
def health_check():
    llm_client = LLMClient()
    llm_status = llm_client.llm_config_status()  # config-only — never probes the LLM provider

    try:
        db_connected = test_database_connection() == 1
    except Exception:
        db_connected = False

    if not db_connected:
        return JSONResponse(status_code=503, content={
            "status": "Backend is running",
            "database": "not connected",
            "error_code": "DATABASE_UNAVAILABLE",
            "llm": llm_status,
        })

    return {
        "status": "Backend is running",
        "database": "connected",
        "llm": llm_status,
    }


@app.get("/db-health")
def database_health_check():
    try:
        result = test_database_connection()
    except Exception:
        result = 0

    if result == 1:
        return {"database": "connected"}

    return JSONResponse(status_code=503, content={"database": "not connected", "error_code": "DATABASE_UNAVAILABLE"})


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    """Last-resort safety net: an unexpected exception must never surface a
    stack trace, exception message, or non-JSON response to the client —
    only a safe, generic body. Full diagnostic detail (exception class,
    message, request path) goes to the server log only."""
    logger.error("UNHANDLED_EXCEPTION path=%s", getattr(request.url, "path", "?"), exc_info=exc)
    return JSONResponse(status_code=500, content={"detail": "Internal server error", "error_code": "INTERNAL_ERROR"})


@app.post("/init-db")
def init_database():
    Base.metadata.create_all(bind=engine)

    return {
        "message": "Database tables created successfully"
    }
