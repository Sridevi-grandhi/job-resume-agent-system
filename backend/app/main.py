from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.database import Base, engine, test_database_connection

from app.models.user_model import User
from app.models.user_preference_model import UserPreference
from app.models.resume_model import Resume
from app.models.job_model import Job
from app.models.match_model import MatchScore
from app.models.optimized_resume_model import OptimizedResume
from app.models.email_log_model import EmailLog

from app.routes.auth_routes import router as auth_router
from app.routes.preference_routes import router as preference_router
from app.routes.resume_routes import router as resume_router
from app.routes.job_routes import router as job_router
from app.routes.match_routes import router as match_router
from app.routes.email_routes import router as email_router
from app.routes.scheduler_routes import router as scheduler_router

from app.scheduler.daily_scheduler import start_daily_scheduler, stop_daily_scheduler


@asynccontextmanager
async def lifespan(app: FastAPI):
    start_daily_scheduler()
    yield
    stop_daily_scheduler()


app = FastAPI(
    title="AI Job Search and Resume Optimization API",
    description="Backend API for job search, resume optimization, matching, email, and scheduler workflow agents",
    version="1.0.0",
    lifespan=lifespan
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:5173",
        "http://127.0.0.1:5173"
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth_router)
app.include_router(preference_router)
app.include_router(resume_router)
app.include_router(job_router)
app.include_router(match_router)
app.include_router(email_router)
app.include_router(scheduler_router)


@app.get("/")
def home():
    return {
        "message": "Welcome to AI Job Search and Resume Optimization API"
    }


@app.get("/health")
def health_check():
    return {
        "status": "Backend is running"
    }


@app.get("/db-health")
def database_health_check():
    result = test_database_connection()

    if result == 1:
        return {
            "database": "connected"
        }

    return {
        "database": "not connected"
    }


@app.post("/init-db")
def init_database():
    Base.metadata.create_all(bind=engine)

    return {
        "message": "Database tables created successfully"
    }