# CareerBoost AI

CareerBoost AI is a React/Vite, FastAPI, PostgreSQL application for job discovery, resume matching and optimization, selected jobs, email summaries, and scheduled daily workflows.

## Docker architecture

The primary Compose stack runs:

- `postgres`: PostgreSQL 17 with a health check and the persistent `postgres_data` volume.
- `backend`: one production Uvicorn worker on port 8000. It creates missing tables, applies the existing additive schema upgrades, and starts the existing APScheduler through FastAPI's lifespan.
- `frontend`: a Vite production build served by Nginx on port 5173, including SPA route fallback.

Uploaded resumes are persisted in the `resume_uploads` volume mounted at `/app/app/uploads`, which is the parent of the application's actual `/app/app/uploads/resumes` storage directory.

## Prerequisites

- Docker Desktop
- Docker Compose (included with current Docker Desktop releases)

Python, Node.js, a virtual environment, and a host PostgreSQL installation are not required for the Docker workflow.

## First startup (Windows PowerShell)

```powershell
cd D:\workspace\job-resume-agent-system
Copy-Item .env.example .env
docker compose up --build
```

The repository includes a safe local `.env`; copying the example is useful when setting up a fresh checkout. Put real LLM or SMTP credentials only in `.env`, which Git ignores.

Open:

- Frontend: http://localhost:5173
- Backend API: http://localhost:8000
- Swagger UI: http://localhost:8000/docs
- ReDoc: http://localhost:8000/redoc
- Health: http://localhost:8000/health
- Database from the host: `localhost:5432`

## Environment variables

| Variable | Default/purpose |
|---|---|
| `POSTGRES_DB` | `job_resume_agent_db` |
| `POSTGRES_USER` | `postgres` |
| `POSTGRES_PASSWORD` | Local database password; change outside local development |
| `POSTGRES_PORT` | Host database port, default `5432` |
| `DATABASE_URL` | Backend SQLAlchemy URL; its Docker hostname must remain `postgres` |
| `BACKEND_PORT` | Host API port, default `8000` |
| `FRONTEND_PORT` | Host frontend port, default `5173` |
| `VITE_API_BASE_URL` | Browser-visible API URL, default `http://localhost:8000` |
| `CORS_ORIGINS` | Comma-separated allowed browser origins |
| `APP_TIMEZONE` | Scheduler log/default application timezone, default `America/Chicago` |
| `AUTO_ANALYZE_SAVED_JOBS` | Existing automatic analysis switch |
| `MAX_OPTIMIZATIONS_PER_RUN` | Existing ATS workload cap |
| `LLM_*` | Optional backend-only LLM configuration |
| `SMTP_*` | Optional backend-only SMTP configuration |

`VITE_API_BASE_URL` is compiled into the public frontend bundle, so it must never contain a secret. LLM, SMTP, and database credentials are passed only to backend/database containers.

## Useful commands

Run these from the project root:

```powershell
# Start and build
docker compose up --build

# Start in the background
docker compose up --build -d

# Status and logs
docker compose ps
docker compose logs -f
docker compose logs -f backend
docker compose logs -f frontend
docker compose logs -f postgres

# Stop while preserving all data
docker compose down

# Restart
docker compose restart

# Rebuild only the backend
docker compose build backend
docker compose up -d backend

# Enter containers
docker compose exec backend sh
docker compose exec postgres psql -U $env:POSTGRES_USER -d $env:POSTGRES_DB

# Clean rebuild without deleting data
docker compose down
docker compose build --no-cache
docker compose up -d
```

If the PowerShell environment variables are not loaded, use the defaults directly:

```powershell
docker compose exec postgres psql -U postgres -d job_resume_agent_db
```

## Verification

```powershell
docker compose build
docker compose up -d
docker compose ps
Invoke-RestMethod http://localhost:8000/health
docker compose logs backend
docker compose restart
docker compose ps
```

Then verify the frontend, Swagger UI, signup/login, resume upload, job searches, selected jobs, and optimizer in the browser. The backend logs should contain one scheduler startup message. The production container intentionally uses one Uvicorn worker and no reload process, so the in-process scheduler starts once.

To verify persistence, create a user and upload a resume, run `docker compose down`, start again with `docker compose up -d`, and confirm both remain available. Ordinary `down`, restart, and rebuild operations preserve the named volumes.

> **Destructive reset:** `docker compose down -v` permanently deletes both the PostgreSQL data volume and uploaded-resume volume. Use it only when intentionally resetting all application data and files.

## Local non-container development

The existing backend can still be run from `backend/` using its local environment and `python -m uvicorn app.main:app --reload`. The frontend can still be run from `frontend/` with `npm install` and `npm run dev`. For host development, `backend/.env` should use `localhost` in `DATABASE_URL`; inside Compose, the root `.env` uses the `postgres` service hostname.

## Security notes

The defaults are intended for local development. For deployment, use strong secrets, avoid exposing PostgreSQL publicly, restrict CORS to the deployed frontend, use HTTPS, and store secrets in the target platform's secret manager. Neither `.env` nor uploaded personal documents are committed or copied into images.
