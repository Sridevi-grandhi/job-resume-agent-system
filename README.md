# AI Job Search and Resume Optimization System

A full-stack application that helps users manage a job-search workflow from one dashboard. Users can create an account, save job preferences, upload a resume, discover jobs, calculate resume-to-job match scores, generate optimized resumes, and create job-summary emails. A scheduler can also run the complete workflow automatically.

## Features

- User signup and login
- Job-search preferences, including schedule and timezone
- PDF, DOCX, and TXT resume parsing
- Job discovery based on saved preferences
- Keyword-based resume and job matching
- Job-specific resume optimization
- Job-summary email generation and history
- Manual and scheduled end-to-end workflows
- Interactive FastAPI documentation

> [!NOTE]
> The current job-search agent returns mock job data, and the email feature generates and stores an email summary rather than sending it through an external mail provider.

## Tech Stack

- **Frontend:** React 19, Vite 8
- **Backend:** FastAPI, SQLAlchemy, APScheduler
- **Database:** PostgreSQL 17
- **Document processing:** PyMuPDF, python-docx
- **Infrastructure:** Docker Compose for PostgreSQL

## Project Structure

```text
job-resume-agent-system/
|-- backend/
|   |-- app/
|   |   |-- agents/       # Search, matching, optimization, and email logic
|   |   |-- models/       # SQLAlchemy database models
|   |   |-- routes/       # FastAPI endpoints
|   |   |-- scheduler/    # Automated daily workflow
|   |   |-- utils/        # Resume parsing and file generation
|   |   `-- main.py       # FastAPI application
|   `-- requirements.txt
|-- frontend/
|   |-- src/              # React application and API client
|   `-- package.json
|-- docker-compose.yml
`-- README.md
```

## Prerequisites

Install the following before starting:

- Python 3.11 or newer
- Node.js 20 or newer
- Docker Desktop (recommended for PostgreSQL), or a local PostgreSQL installation

## Getting Started

### 1. Start PostgreSQL

From the project root:

```bash
docker compose up -d
```

This starts PostgreSQL on port `5432` with the development credentials defined in `docker-compose.yml`.

### 2. Configure the backend

Create `backend/.env`:

```env
DATABASE_URL=postgresql+psycopg://postgres:postgres@localhost:5432/job_resume_agent_db
```

Create and activate a virtual environment, then install the dependencies.

Windows PowerShell:

```powershell
cd backend
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

macOS/Linux:

```bash
cd backend
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

Start the API from the `backend` directory:

```bash
uvicorn app.main:app --reload
```

Initialize the database tables once the backend is running:

```bash
curl -X POST http://127.0.0.1:8000/init-db
```

You can also open `http://127.0.0.1:8000/docs` and run `POST /init-db` from Swagger UI.

### 3. Start the frontend

Open another terminal:

```bash
cd frontend
npm install
npm run dev
```

Open `http://localhost:5173` in your browser.

## Application Workflow

1. Create an account or log in.
2. Save a desired job title, location, skills, work mode, and schedule.
3. Upload a resume in PDF, DOCX, or TXT format.
4. Search for jobs matching the saved preferences.
5. Calculate match scores between the resume and available jobs.
6. Generate an optimized resume for a selected job.
7. Generate a summary email, or run the entire workflow at once.

## API Overview

The API runs at `http://127.0.0.1:8000` by default.

| Area | Endpoints |
|---|---|
| Health | `GET /health`, `GET /db-health` |
| Database | `POST /init-db` |
| Authentication | `POST /api/auth/signup`, `POST /api/auth/login` |
| Preferences | `POST /api/preferences`, `GET /api/preferences/{user_id}` |
| Resumes | Upload, retrieve latest, optimize, and list optimized resumes under `/api/resume` |
| Jobs | Search and list jobs under `/api/jobs` |
| Matching | Calculate and retrieve matches under `/api/match` |
| Email summaries | Generate and retrieve logs under `/api/email` |
| Workflow | `POST /api/scheduler/run/{user_id}` |

Full request and response schemas are available at:

- Swagger UI: `http://127.0.0.1:8000/docs`
- ReDoc: `http://127.0.0.1:8000/redoc`

## Development Commands

Run these commands from `frontend/`:

```bash
npm run dev      # Start the development server
npm run build    # Create a production build
npm run lint     # Run ESLint
npm run preview  # Preview the production build
```

Run the backend with automatic reload from `backend/`:

```bash
uvicorn app.main:app --reload
```

## Configuration Notes

- The frontend API URL is currently set to `http://127.0.0.1:8000` in `frontend/src/api.js`.
- Backend CORS currently allows `http://localhost:5173` and `http://127.0.0.1:5173`.
- Uploaded resumes and generated files are stored under the backend application directory. Do not commit files containing personal information.
- The scheduler starts and stops with the FastAPI application.

## Troubleshooting

- **`DATABASE_URL is missing`:** confirm that `backend/.env` exists and contains the connection string shown above.
- **Database connection fails:** verify the PostgreSQL container with `docker compose ps`, and ensure port `5432` is available.
- **Frontend cannot reach backend:** confirm that Uvicorn is running on port `8000` and Vite is running on port `5173`.
- **Tables do not exist:** call `POST /init-db` after starting the API.
- **Resume upload fails:** use a supported PDF, DOCX, or TXT file and confirm that the backend can write to its upload directory.

## Security and Production Considerations

The included credentials and configuration are intended for local development. Before deploying, use strong secrets, restrict CORS, move database credentials into a secure secret store, add proper authentication tokens/session management, validate uploaded files, and serve the application over HTTPS.
