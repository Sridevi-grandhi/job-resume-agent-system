# CareerBoost AI

A full-stack platform for job search, resume optimization, ATS-style matching, and job-email tracking.

CareerBoost AI is a React single-page app backed by a FastAPI API and PostgreSQL. A signed-in user can:

- save job-search preferences
- upload a resume
- generate search links (Job Search) or browse individual postings pulled from configured providers (Updated Job Search)
- save jobs and compare them to their resume
- run a five-step ATS optimizer that confirms evidence before generating a resume
- review, edit, and download the generated resume
- optionally connect Gmail (read-only) to track job-related email and application status

> **About scores.** Every match percentage in CareerBoost is an **internal estimate** calculated by this application. None of them is a score returned by an employer's applicant tracking system, and optimization does not guarantee a higher score.

---

## Contents

- [Feature Overview](#feature-overview)
- [Application Flow](#application-flow)
- [Authentication](#authentication)
- [Preferences and Resume Management](#preferences-and-resume-management)
- [Job Search (search links)](#job-search-search-links)
- [Updated Job Search (individual postings)](#updated-job-search-individual-postings)
- [Selected Jobs](#selected-jobs)
- [ATS Optimizer & Match](#ats-optimizer--match)
- [Match Scores Explained](#match-scores-explained)
- [Email Summary and Gmail](#email-summary-and-gmail)
- [Workflow and Scheduling](#workflow-and-scheduling)
- [Architecture](#architecture)
- [Technology Stack](#technology-stack)
- [Project Structure](#project-structure)
- [Environment Configuration](#environment-configuration)
- [Running with Docker](#running-with-docker)
- [Running Locally](#running-locally)
- [Testing](#testing)
- [Security and Privacy](#security-and-privacy)
- [Deployment Status](#deployment-status)
- [API Reference](#api-reference)
- [Troubleshooting](#troubleshooting)

---

## Feature Overview

| Area | Route | What it does |
| --- | --- | --- |
| Authentication | `/login`, `/signup`, `/forgot-password`, `/reset-password` | Email/password accounts and password reset |
| Dashboard | `/dashboard` | Summary of saved jobs and top resume matches |
| Preferences | `/preferences` | Title, location, experience, skills, work mode, job type, schedule |
| Resume | `/resume` | Upload and parse a PDF, DOCX, or TXT resume |
| Job Search | `/jobs` | Builds search links to external job boards from preferences |
| Updated Job Search | `/updated-jobs` | Individual postings retrieved from configured providers |
| Selected Jobs | `/selected-jobs` | Saved jobs with original and optimized match percentages |
| ATS Optimizer & Match | `/ats-analyzer`, `/optimizer/:jobId` | Five-step analyze → confirm → generate → edit → download wizard |
| Email Summary | `/emails` | Gmail (read-only) scan, application pipeline, event actions |
| Workflow | `/workflow` | Runs the per-user backend workflow on demand |
| Settings | `/settings` | Profile name, Gmail integration, auto-scan, notifications, password change link |

---

## Application Flow

```text
User
  → Authentication
  → Preferences
  → Resume upload
  → Job Search (links) and/or Updated Job Search (postings)
  → Selected Jobs
  → ATS Optimizer (analyze → confirm → review evidence → generate)
  → Generated resume (edit, download)
  → Application (on the employer / provider site)
  → Email Summary / application tracking
```

```mermaid
flowchart TD
    A[Signup / Login] --> B[Preferences]
    B --> C[Resume upload]
    C --> D1[Job Search<br/>search links]
    C --> D2[Updated Job Search<br/>individual postings]
    D2 --> E[Selected Jobs]
    D1 -. manual entry / URL import .-> E
    E --> F[ATS Optimizer]
    F --> G[Generated resume<br/>edit and download]
    G --> H[Apply on employer or provider site]
    H --> I[Email Summary<br/>application tracking]
```

---

## Authentication

- Signup and login with email and password.
- Passwords are hashed with **bcrypt**.
- Forgot password / reset password: reset tokens are hashed, expire (`PASSWORD_RESET_TOKEN_EXPIRE_MINUTES`, default 45), and are single-use. Reset links are emailed through SMTP when it is configured.
- `SHOW_DEV_RESET_LINK=true` returns the reset link in the API response, but only when `APP_ENV` is a development environment. It must stay `false` in production.
- Settings lets a user change their display name (current password required) and links to the reset flow to change a password.
- Logout clears client-side session state.

> **Development-grade auth.** CareerBoost does **not** use JWTs or server-side sessions. After login the SPA stores the user object in `localStorage` and sends `user_id` in API paths, and the backend trusts that path parameter. This works for local development and demos, but it is not a production authentication boundary.

---

## Preferences and Resume Management

**Preferences** are stored per user and drive discovery, relevance filtering, and scheduling: job title, location, experience, skills, work mode (Remote / Hybrid / Onsite), job type (Full-time / Part-time / Contract / Internship / Temporary), schedule time, and timezone.

**Resumes**

- Accepted formats: **PDF, DOCX, TXT**. Text is extracted on the server (PyMuPDF for PDF, python-docx for DOCX).
- The most recently uploaded resume is the active one. Scores are always calculated against it.
- Uploaded files are stored on disk (the `resume_uploads` Docker volume). Generated and edited resume text is stored in PostgreSQL.
- Generated resumes can be edited in the optimizer workspace. Edits autosave after a short delay, and a pending save is flushed before any download.
- The editor has an **AI Improve / Try Again** action for a single bullet. It uses its own LLM setting (`BULLET_REWRITE_LLM_*`) and falls back to a deterministic rewrite when no model is configured.

---

## Job Search (search links)

The **Job Search** page (`/jobs`) builds **search URLs** for LinkedIn, Indeed, Dice, Google Jobs, and ZipRecruiter from the user's saved preferences.

- These rows are links to search-result pages. CareerBoost does not scrape or call an API for these boards on this page.
- They are **not individual postings** and cannot be analyzed in the ATS Optimizer.
- To optimize for a specific posting found this way, add it through Selected Jobs (manual entry or URL import).

---

## Updated Job Search (individual postings)

The **Updated Job Search** page (`/updated-jobs`) lists real, individual postings retrieved by provider adapters and stored in PostgreSQL. Each card shows a per-user match badge, a details drawer, **Save** (to Selected Jobs), an entry into the ATS Optimizer, and an application action (see [Application actions](#application-actions)).

Searches are dynamic. Queries are built from each user's saved job title, location, and preferences, so the same pipeline serves an SDET / QA Automation search, a Java Full Stack search, a DevOps search, or a Data Engineer search without role-specific code paths.

### Providers

Each provider is independent. **A provider that is not configured contributes no jobs.** CareerBoost never invents employers or postings to fill a gap.

| Provider | Active when | Notes |
| --- | --- | --- |
| **JSearch (OpenWeb Ninja)** | `UPDATED_JOB_PRIMARY_PROVIDER=jsearch` (the default) **and** `JSEARCH_API_KEY` is set | Primary broad provider. Bounded by `JSEARCH_MAX_QUERIES_PER_POLL`, `JSEARCH_MAX_PAGES_PER_QUERY`, `JSEARCH_MAX_RETRIES`. Also used for a bounded *direct-apply lookup* (see below). |
| **Adzuna** | `ADZUNA_APP_ID` **and** `ADZUNA_APP_KEY` are set | Additional broad source. `ADZUNA_MAX_PAGES_PER_QUERY` (1–3). Adzuna results are listing-only (no employer apply URL). |
| **Apify Dice** | `APIFY_DICE_ENABLED=true` **and** `APIFY_API_TOKEN` **and** `APIFY_DICE_ACTOR_ID` are set | Opt-in. Details in [Apify Dice](#apify-dice). |
| **Greenhouse** | Companies listed in `GREENHOUSE_COMPANY_SOURCES` (`Company Name:board-token`, comma-separated) | Public Job Board API. |
| **Lever** | Companies listed in `LEVER_COMPANY_SOURCES` (`Company Name:site-slug`, comma-separated) | Public Postings API. |
| **Remotive** | `UPDATED_JOB_REMOTIVE_ENABLED=true` | Off by default. |
| **Official company career pages** | Entries in `COMPANY_CAREER_SOURCES` (JSON array, max 20) | Opt-in. Supports Greenhouse, Lever, Workday, Ashby, SmartRecruiters, iCIMS, SuccessFactors, and custom pages. The optional Playwright fallback needs `COMPANY_CAREER_PLAYWRIGHT_ENABLED=true` and a separate install. See [docs/company-career-sources.md](docs/company-career-sources.md). |
| Workday connector (standalone) | Demo mode only | Workday has no uniform public API, so this connector never retrieves live jobs in normal operation. |
| Generic career feed | `GENERIC_FEED_ENABLED=true` **and** `UPDATED_JOB_DEMO_MODE=true` | Mock/demo slot only. |

Demo/mock jobs exist only when `UPDATED_JOB_DEMO_MODE=true` and are excluded from normal operation.

### Pipeline

Both the scheduled poll and the manual **Refresh** button call the same poll cycle. Only one poll runs at a time, and a failure in one provider does not stop the others.

```text
User preferences (job title, location, work mode, job type, experience, skills)
  → Provider discovery        (each configured adapter; bounded requests)
  → Provider normalization    (common job shape; HTML stripped)
  → Role relevance + preference filtering
                              (title relevance, location / work mode,
                               job type, experience)
  → Cross-provider dedup      (source-neutral fingerprint, so the same
                               listing from JSearch and Adzuna collapses)
  → Persistence + change detection (new / updated / closed)
  → Resume ↔ job matching     (per user, against the latest resume)
  → Direct-application resolution
  → Enrichment / verification (when applicable; budgeted per cycle)
  → User action               (Save, Optimize, Apply / View / Closed)
```

- **Polling:** the tracker runs every `UPDATED_JOB_POLL_INTERVAL_MINUTES` (default 15) when `UPDATED_JOB_TRACKER_ENABLED=true`. This is scheduled polling, not a real-time feed.
- **Closure:** a job missing from a provider's full inventory for `INACTIVE_AFTER_MISSED_POLLS` polls (default 3) is marked inactive. User-scoped manual refreshes never close jobs.
- **Per-source cap:** `JOB_DISCOVERY_MAX_JOBS_PER_SOURCE` (default 100).
- A manual refresh for a user without a saved job title does nothing. It never falls back to another user's preferences.

### Application actions

The backend resolves one action per job, and the frontend renders it without deriving its own. The resolver asks three separate questions:

1. **Type:** is the stored URL an employer/ATS destination or an aggregator listing?
2. **Specificity:** does it point at one specific posting, or a generic careers/search page?
3. **Availability:** is that posting confirmed open (`ACTIVE`), confirmed closed (`EXPIRED`), or `UNKNOWN`?

| Internal result | Label shown in Updated Job Search | Meaning |
| --- | --- | --- |
| `Apply` | **Apply on Company Site** | A verified employer/ATS URL for this specific posting that is confirmed active |
| `Open Employer Site` | **Open Career Page** | A real employer page that is not proven to be this specific posting, or whose availability is not confirmed |
| `View Listing` / provider fallback | **Apply on {Provider}** (for example Dice or Adzuna) | An aggregator/provider listing, not a confirmed employer destination |
| `Job Closed` | **Job Closed** | The specific posting was confirmed expired, so the button is disabled |
| `Unavailable` | **Unavailable** | No usable URL |

When a direct employer destination is blocked for the user (for example by a 403 or bot challenge), a secondary **View Source** link may point back to the listing where the job was found.

### Verification of application URLs

Third-party apply URLs are **not trusted blindly.** A provider-supplied apply link is stored as an *unverified application candidate*. It is only promoted to a direct **Apply** destination after it passes the shared verification pipeline:

1. **URL safety:** scheme/host checks and rejection of unsafe destinations.
2. **Destination classification:** employer/ATS domain vs aggregator.
3. **Specificity:** a single job page, not a generic careers or search page.
4. **Job identity:** same employer, a strong title match, and a compatible location.
5. **Availability:** a bounded HTTP check that the page is open (cached for `JOB_AVAILABILITY_TTL_MINUTES`, default 360).

Candidates can come from:

- the provider itself (for example a Dice `applyUrl`)
- the Greenhouse/Lever enrichment provider
- a configured official company board
- a bounded **JSearch direct-apply lookup** (`JSEARCH_LOOKUP_MAX_PER_CYCLE`; 7-day cooldown per job; stops for the cycle on HTTP 429)
- an optional broad-search enrichment provider (`JOB_ENRICHMENT_BROAD_PROVIDER`, off by default)

A candidate that fails any gate is rejected. Enrichment is failure-isolated: if it fails, the job keeps its safe listing link.

### Apify Dice

- **Provider name:** `apify_dice` (broad provider). It calls Apify's synchronous `run-sync-get-dataset-items` endpoint for the configured Dice actor.
- **Enablement:** it needs the explicit flag `APIFY_DICE_ENABLED=true` **plus** both `APIFY_API_TOKEN` and `APIFY_DICE_ACTOR_ID`. Credentials alone do not enable it.
- **Dynamic queries:** one query per active preference job title, with location when one is set. It never runs the actor's default query when no title is saved.
- **Bounded:** at most 20 listings per poll, one page per query, a 120-second actor timeout, and no automatic retry of a paid run on timeout.
- **Listing:** the Dice listing URL is stored as the job's source URL.
- **`applyUrl`:** only when `applyType` is `APPLY_TO_URL` is the `applyUrl` stored, and then only as an **unverified application candidate**. It goes through the same verification pipeline above and is never shown as a direct Apply without passing it.
- **`APPLY_TO_EMAIL` / Easy Apply:** no apply URL is stored, and shared enrichment is skipped. The job remains a Dice listing, and it is still eligible for the bounded JSearch direct-apply lookup.
- **Errors:** only safe, documented error codes are logged. The token and provider exception text are never logged.

---

## Selected Jobs

Selected Jobs (`/selected-jobs`) holds the jobs a user intends to work on.

- **Save from Updated Job Search.** The posting's description and resolved destination are copied in.
- **Manual entry** of title, company, description, and URL.
- **URL import** over HTTP(S), with safety checks: only `http`/`https`, host validation, redirects followed manually (max 4), a 2 MB response cap, and rejection of login or search-result pages.
- **Remove** a job.
- **Original Resume Match:** the latest *original* score of the current resume against this job.
- **Optimized ATS Match:** shown when an ATS run has produced an optimized score.
- **Open in ATS Optimizer.**

When `AUTO_ANALYZE_SAVED_JOBS=true` (the default), saving an analyzable job starts the ATS analysis workflow in the background.

---

## ATS Optimizer & Match

A five-step wizard at `/ats-analyzer` (or `/optimizer/:jobId` to deep-link a job).

1. **Select Job.** Choose a saved job with a real job description. Search-link rows are not analyzable.
2. **Analyze Requirements.** Extract requirements from the job description and compare them against the latest resume.
3. **Confirm Skills.** For each unresolved requirement, confirm it with structured evidence, mark it as not possessed, or skip it.
4. **Review Evidence.** Review verified resume skills, user confirmations, and **Remaining Gaps**. Gaps do not block generation.
5. **Generate, Review & Download.** Generation runs in the background while the page polls status. The page then shows original vs optimized scores and an in-browser editor with autosave, and offers downloads.

**Rules enforced by the workflow**

- The original score always compares the uploaded resume with the job description.
- Generation uses verified resume evidence plus valid user-confirmed evidence only.
- Unsupported requirements stay visible as Remaining Gaps. They are never written into the resume as experience.
- **Eligibility:** optimization requires an original score of at least 65% and acceptable role alignment. Otherwise the workflow records `INELIGIBLE_LOW_SCORE` or `INELIGIBLE_ROLE_ALIGNMENT` instead of generating.
- Generated content passes a final validator for fabricated employers, dates, certifications, technologies, and timeline consistency. If the LLM is unavailable or fails, a **deterministic generator** is used.
- An optimized score is not guaranteed to be higher.

**Workflow statuses:** `PENDING` → `ANALYZING_ORIGINAL` → `GENERATING_RESUME` → `VALIDATING_RESUME` → `ANALYZING_OPTIMIZED` → `COMPLETED`. Terminal alternatives are `FAILED`, `VALIDATION_FAILED`, `INELIGIBLE_LOW_SCORE`, and `INELIGIBLE_ROLE_ALIGNMENT`.

**Downloads**

- **DOCX** and **PDF** of the latest edited/final text, available after final validation passes. PDF is converted from DOCX by LibreOffice, which is installed in the backend image.
- **TXT**, from both the editor and the workflow result endpoint.

---

## Match Scores Explained

These percentages are separate fields and should not be mixed. **All are CareerBoost estimates, not employer ATS scores.**

| Where you see it | What it is | Source |
| --- | --- | --- |
| Dashboard *Resume Match* / *Top Job Matches* | Latest **original** resume-vs-job score for each saved job | `match_scores` (original version), `GET /api/match/{user_id}` |
| Selected Jobs *Original Resume Match* | Same original score, per saved job | `original_match_percentage` |
| Selected Jobs *Optimized* | Score of the generated resume for that job | ATS workflow |
| ATS *Original* | Uploaded resume vs job, within the optimizer | Workflow `original_match_score` |
| ATS *Optimized* / *current edited* | Generated or edited resume text vs job; rescored after edits | Workflow / editor rescore |
| Updated Job Search match badge | Discovered posting vs current resume, using the same original-resume scorer, cached per user and resume | `job_matches` (separate from `match_scores`) |

The Updated Job Search badge can include an optional LLM-written explanation (`JOB_MATCH_LLM_*`, falling back to `LLM_*`). The numeric score itself is deterministic.

An optimized score is never shown as the Dashboard Resume Match. Scores computed against an older resume are not treated as current.

---

## Email Summary and Gmail

Gmail OAuth requests **`https://www.googleapis.com/auth/gmail.readonly` only**. CareerBoost never requests `gmail.compose` or `gmail.send`, and it never sends mail from or creates drafts in the user's Gmail account.

When Gmail is configured, Email Summary (`/emails`) and Settings support:

- **Connect / disconnect** Gmail.
- **Scan job emails.** The first scan is a bounded search of recent mail (`GMAIL_SCAN_MAX_MESSAGES`, default 25, over `GMAIL_INITIAL_SCAN_DAYS`, default 30). Later scans are incremental.
- **Classification** into Job Opportunities, Recruiter Messages, Application Updates, and Interview Invitations. Users choose which types to scan in Settings.
- **Application pipeline** with status history and manual status updates.
- **Confirm / Ignore.** Scanned events start as needing confirmation and never change an application's status until the user confirms.
- **Event details**, **mark read**, and **star.** These flags are stored in CareerBoost only and do not change anything in Gmail.
- **Automatic scans** (optional, per user): every 1, 6, or 12 hours, or daily. They use the same scan pipeline with per-user overlap protection.
- **Notification preferences:** job alerts, application updates, and interview alerts.
- **Daily summary:** a generated summary is logged in-app. Delivery by email happens only when the user enables it **and** SMTP is configured. It is sent through the app's SMTP server, not through Gmail.

**Not part of the current product:** Recruiter Draft / Suggested Reply generation. Leftover pieces remain (a `recruiter_drafts` table, `RECRUITER_DRAFT_*` / `REDIS_URL` variables in `.env.example`, and a profile-gated `draft_worker` Compose service), but no active route or UI uses them.

Setup guide: [docs/email-summary-gmail-setup.md](docs/email-summary-gmail-setup.md).

---

## Workflow and Scheduling

The backend runs one in-process **APScheduler** instance that handles:

| Job | Trigger |
| --- | --- |
| Per-user daily workflow | Each user's saved schedule time and timezone |
| Updated Job Search poll | Every `UPDATED_JOB_POLL_INTERVAL_MINUTES` when the tracker is enabled |
| Gmail auto-scan | Per-user interval, only for users who enabled it |
| Daily summary delivery | Only for users who enabled delivery |

The **Workflow** page calls `POST /api/scheduler/run/{user_id}` to run the per-user workflow immediately. The workflow loads preferences and the latest resume, generates Job Search links, runs the ATS workflow on analyzable saved jobs (capped by `MAX_OPTIMIZATIONS_PER_RUN`, default 3), collects match scores, and logs a job-summary email record.

Uvicorn runs with a **single worker** so scheduled jobs are not duplicated.

---

## Architecture

```text
Browser
   │
React SPA (Vite build, served by Nginx in Docker)
   │  REST / JSON + multipart uploads   (VITE_API_BASE_URL)
   ▼
FastAPI on Uvicorn (1 worker) ── APScheduler (in-process)
   │  SQLAlchemy 2 / psycopg 3
   ▼
PostgreSQL 17            + resume files on disk (volume: resume_uploads)

Optional external integrations (configured by environment):
   • Job providers: JSearch, Adzuna, Apify (Dice), Greenhouse, Lever,
     Remotive, official career pages
   • LLM: OpenAI, Anthropic, or any OpenAI-compatible endpoint (e.g. local Ollama)
   • Gmail API (read-only OAuth)
   • SMTP (password reset, optional daily summary delivery)
```

- Database tables are created at startup (`create_all`), followed by additive schema-upgrade helpers. The project does not use Alembic.
- The backend health check (`GET /health`) depends only on PostgreSQL. It reports LLM configuration status without calling the LLM.
- **Redis** is defined in `docker-compose.yml` and starts with the default stack, but the active application **does not require it**. It is left over from the removed Recruiter Draft worker.

---

## Technology Stack

| Layer | Technology | Verified in |
| --- | --- | --- |
| Frontend | React 19, React Router 7, JavaScript (JSX) | `frontend/package.json` |
| Frontend build | Vite 8 | `frontend/package.json` |
| Frontend serving | Node 22 (build stage), Nginx 1.27 | `frontend/Dockerfile` |
| Backend | Python 3.12, FastAPI, Uvicorn, Pydantic 2 | `backend/Dockerfile`, `backend/requirements.txt` |
| Database | PostgreSQL 17, SQLAlchemy 2, psycopg 3 | `docker-compose.yml`, `backend/requirements.txt` |
| Documents | PyMuPDF, python-docx, LibreOffice Writer (DOCX→PDF) | `backend/requirements.txt`, `backend/Dockerfile` |
| HTML parsing | BeautifulSoup, lxml | `backend/requirements.txt` |
| Security | bcrypt (passwords), cryptography/Fernet (Gmail tokens) | `backend/requirements.txt` |
| Scheduling | APScheduler | `backend/app/scheduler/` |
| LLM | In-house HTTP client: `openai`, `anthropic`, `openai_compatible` (Ollama), or `none` | `backend/app/services/llm_client.py` |
| Email | Gmail API (read-only) via HTTP; SMTP via `smtplib` | `backend/app/services/` |
| Testing | pytest; Vitest + Testing Library; Playwright | `backend/pytest.ini`, `frontend/package.json` |
| Infrastructure | Docker, Docker Compose | `docker-compose.yml` |

The application source is JavaScript, with no TypeScript. There is no CI workflow in this repository. pytest is not included in the production backend image.

---

## Project Structure

```text
job-resume-agent-system/
├── backend/
│   ├── app/
│   │   ├── agents/          # job search links, URL import, resume, matching, email
│   │   ├── job_sources/     # provider adapters (+ career_sources/ for official boards)
│   │   ├── models/          # SQLAlchemy models
│   │   ├── routes/          # FastAPI routers
│   │   ├── scheduler/       # APScheduler jobs
│   │   ├── services/        # scoring, ATS workflow, enrichment, Gmail, documents
│   │   └── main.py
│   ├── tests/
│   ├── Dockerfile
│   ├── pytest.ini
│   ├── requirements.txt
│   └── .env.example
├── frontend/
│   ├── src/                 # App.jsx, pages/, components/, context/, api.js
│   ├── e2e/                 # Playwright specs
│   ├── Dockerfile
│   ├── nginx.conf
│   └── package.json
├── docs/
│   ├── company-career-sources.md
│   └── email-summary-gmail-setup.md
├── docker-compose.yml
├── .env.example
└── README.md
```

---

## Environment Configuration

There are two environment files. **Never commit real values.** Both are ignored by `.gitignore`.

| File | Used by |
| --- | --- |
| `.env` (from `.env.example`) | Docker Compose variable substitution (ports, DB, LLM, SMTP, Gmail, CORS, `VITE_API_BASE_URL`) |
| `backend/.env` (from `backend/.env.example`) | Loaded by the API process, and also passed to the Compose backend via `env_file`. It holds the Updated Job Search provider settings. |

In Docker, explicit `environment:` entries in `docker-compose.yml` take precedence over `backend/.env`. `VITE_API_BASE_URL` is compiled into the frontend bundle at build time, so it must never contain secrets.

**Placeholders only:**

```dotenv
# --- Core ---
POSTGRES_DB=job_resume_agent_db
POSTGRES_USER=postgres
POSTGRES_PASSWORD=your-password
DATABASE_URL=postgresql+psycopg://postgres:your-password@postgres:5432/job_resume_agent_db
BACKEND_PORT=8000
FRONTEND_PORT=5173
VITE_API_BASE_URL=http://localhost:8000
CORS_ORIGINS=http://localhost:5173,http://127.0.0.1:5173
APP_ENV=development
FRONTEND_BASE_URL=http://localhost:5173
SHOW_DEV_RESET_LINK=false

# --- LLM (full resume generation) ---
LLM_PROVIDER=none                 # openai | anthropic | openai_compatible | none
LLM_API_KEY=YOUR_API_KEY
LLM_MODEL=
LLM_BASE_URL=

# --- Bullet "Try Again" (independent of LLM_PROVIDER) ---
BULLET_REWRITE_LLM_PROVIDER=openai_compatible
BULLET_REWRITE_LLM_BASE_URL=http://localhost:11434/v1
BULLET_REWRITE_LLM_MODEL=

# --- Updated Job Search ---
UPDATED_JOB_TRACKER_ENABLED=true
UPDATED_JOB_POLL_INTERVAL_MINUTES=15
UPDATED_JOB_PRIMARY_PROVIDER=jsearch
JSEARCH_API_KEY=YOUR_API_KEY
ADZUNA_APP_ID=your-app-id
ADZUNA_APP_KEY=YOUR_API_KEY
APIFY_DICE_ENABLED=false
APIFY_API_TOKEN=YOUR_API_TOKEN
APIFY_DICE_ACTOR_ID=owner~actor-name
GREENHOUSE_COMPANY_SOURCES=
LEVER_COMPANY_SOURCES=
UPDATED_JOB_REMOTIVE_ENABLED=false
COMPANY_CAREER_SOURCES=[]
UPDATED_JOB_DEMO_MODE=false

# --- SMTP (password reset, optional daily summary) ---
SMTP_HOST=
SMTP_PORT=
SMTP_USERNAME=
SMTP_PASSWORD=your-password
SMTP_FROM_EMAIL=

# --- Gmail (read-only) ---
GOOGLE_CLIENT_ID=your-client-id
GOOGLE_CLIENT_SECRET=your-client-secret
GOOGLE_REDIRECT_URI=http://localhost:8000/api/email/gmail/callback
EMAIL_TOKEN_ENCRYPTION_KEY=your-fernet-key
```

`ADZUNA_*` and `APIFY_*` are read by the code but are not yet listed in either `.env.example`. Add them to `backend/.env` if you use those providers. Optional tuning variables (`JSEARCH_MAX_*`, `JOB_DISCOVERY_MAX_JOBS_PER_SOURCE`, `GMAIL_SCAN_*`, `ATS_*` timeouts, `JOB_MATCH_LLM_*`, and others) are documented with comments in `.env.example` and `backend/.env.example`.

---

## Running with Docker

Recommended. From the repository root:

```powershell
Copy-Item .env.example .env
Copy-Item backend/.env.example backend/.env   # optional; needed for provider keys
docker compose up --build
```

| Surface | Default URL |
| --- | --- |
| Frontend | http://localhost:5173 |
| Backend API | http://localhost:8000 |
| OpenAPI docs | http://localhost:8000/docs |
| Health | http://localhost:8000/health |

The default services are `postgres`, `redis` (not required by the active app), `backend`, and `frontend`. `draft_worker` starts only with `--profile unused-recruiter-drafts`, and its module no longer exists.

```powershell
docker compose ps
docker compose logs -f backend
docker compose down        # keeps volumes
docker compose down -v     # deletes Postgres data and uploaded resumes
```

**Port remapping.** If host port 8000 is already in use, pick another port and change all three settings together:

- `BACKEND_PORT` (for example `8001`)
- `VITE_API_BASE_URL` (for example `http://localhost:8001`)
- `GOOGLE_REDIRECT_URI`, plus the matching redirect URI in Google Cloud

Then rebuild the frontend image (`docker compose up --build`) so the baked API origin matches.

**Ollama from Docker.** Use `http://host.docker.internal:11434/v1` as the base URL. The backend container maps `host.docker.internal` to the host.

---

## Running Locally

Host processes work if you provide PostgreSQL and the environment files.

**Requirements:** Python 3.12, Node.js 22+, PostgreSQL 17 (or the Compose `postgres` service with its port published). Ollama is optional.

### Backend

```powershell
cd backend
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
python -m uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

Point `DATABASE_URL` in `backend/.env` at a host-reachable database (for example `localhost:5432`, not the Compose hostname `postgres`). PDF export requires LibreOffice on the host.

### Frontend

```powershell
cd frontend
npm install
npm run dev
```

Vite serves the app at http://localhost:5173. Set `VITE_API_BASE_URL` to the backend origin.

### Optional: local LLM with Ollama

Run Ollama with an installed model, then set:

```dotenv
LLM_PROVIDER=openai_compatible
LLM_BASE_URL=http://localhost:11434/v1
LLM_API_KEY=ollama
LLM_MODEL=<your-installed-model>
```

If the LLM is unset or unreachable, ATS generation uses the deterministic fallback.

---

## Testing

Run the full regression suite before deployment. Test counts change as development continues, so don't treat a pass count as a product claim.

**Backend** uses a SQLite test database with `LLM_PROVIDER` forced to `none`. Provider HTTP (JSearch, Adzuna, Apify, Remotive, URL import) is mocked, and no live provider or Gmail calls are made.

```powershell
cd backend
pip install pytest httpx
python -m pytest
```

**Frontend:**

```powershell
cd frontend
npm test            # Vitest
npm run lint
npm run build
npm run test:e2e    # Playwright, against a mocked API fixture
```

`npm run test:coverage` is also available.

---

## Security and Privacy

- Passwords are bcrypt-hashed. Reset tokens are hashed, expiring, and single-use.
- API keys, OAuth secrets, SMTP passwords, database credentials, and the Fernet key belong in environment files, never in source control or the frontend bundle. **Never commit `.env`.**
- Gmail access is **read-only**. OAuth tokens are encrypted at rest with Fernet (`EMAIL_TOKEN_ENCRYPTION_KEY`). Rotating the key invalidates stored tokens, and users must reconnect.
- Provider errors are logged as safe codes or class names. Exception text that could contain credentials is not persisted.
- Outbound URLs (URL import, availability checks, provider apply links) go through safety checks before use.
- Uploaded resumes and mailbox metadata are sensitive personal data.

**Known limitation:** the API trusts `user_id` in the request path, and the SPA keeps the user in `localStorage`. There is no JWT or server session. This is acceptable for local development, but real token- or session-based authentication is required before any public deployment.

CareerBoost has no SOC 2, HIPAA, or GDPR certification.

---

## Deployment Status

CareerBoost AI has been **validated locally** with Docker Compose and browser-based end-to-end testing. It is **not deployed** as a hosted production service. Production deployment configuration is the next stage.

### Production Deployment Considerations

- **Authentication:** replace the `user_id`-in-path model with server-verified sessions or tokens.
- **TLS:** serve both frontend and API over HTTPS.
- **Config:** set `APP_ENV=production`, keep `SHOW_DEV_RESET_LINK=false`, and use production-only secrets that differ from development.
- **Origins:** set `CORS_ORIGINS`, `FRONTEND_BASE_URL`, and `VITE_API_BASE_URL` to the real origins.
- **Database:** use a managed or backed-up PostgreSQL instance.
- **Storage:** provide persistent storage for uploaded resumes and generated documents.
- **Gmail:** register the production OAuth redirect URI and complete Google verification if needed. While the Google Cloud app is in Testing, only listed test users can connect.
- **Scheduler:** keep a single Uvicorn worker, or move APScheduler into a dedicated process so jobs are not duplicated.
- **Operations:** plan backups, log aggregation, and secret rotation.

---

## API Reference

With the backend running, interactive docs are available at `/docs` (Swagger UI) and `/redoc`.

| Prefix | Purpose |
| --- | --- |
| `/api/auth` | Signup, login, profile, forgot/reset password |
| `/api/preferences` | User preferences |
| `/api/resume` | Upload, latest resume, optimized resumes |
| `/api/jobs` | Search links, selected jobs, URL import |
| `/api/job-discovery` | Updated Job Search: jobs, refresh, status, sources, save, apply |
| `/api/match` | Resume-match calculation and listing |
| `/api/ats-optimizer` (alias `/api/ats`) | Requirements, evidence, eligibility, generate, status, results |
| `/api/optimizer` | Edit, rescore, bullet improve, DOCX/PDF/TXT download |
| `/api/email` | Gmail connect/scan, applications, events, summary preferences and logs |
| `/api/scheduler` | Run the per-user workflow |

Health endpoints: `GET /health` and `GET /db-health`.

---

## Troubleshooting

**Backend not reachable**
- Check `BACKEND_PORT` (default 8000) and `docker compose logs -f backend`.
- `GET /health` returns 503 with `DATABASE_UNAVAILABLE` if PostgreSQL is down.

**Frontend cannot call the API**
- `VITE_API_BASE_URL` is baked in at build time. Rebuild after changing it.
- `CORS_ORIGINS` must include the UI origin.

**Deterministic output instead of LLM output**
- Check the `llm` block of `GET /health` (it never includes the API key).
- Verify the provider, model, and base URL. From Docker, use `host.docker.internal` instead of `localhost`.
- If you use Ollama, confirm the model is installed.

**Updated Job Search is empty**
- Configure at least one live provider (for example `JSEARCH_API_KEY`) and save a job title in Preferences.
- Wait for the next poll or click **Refresh**. Unconfigured providers contribute zero jobs by design.
- Apify Dice also requires `APIFY_DICE_ENABLED=true`.

**Gmail connect fails**
- Check the client ID/secret, the read-only scope, an exact redirect-URI match, and `EMAIL_TOKEN_ENCRYPTION_KEY`.
- If the Google Cloud app is in Testing, the account must be a listed test user.

---

## Contributing

1. Work on a focused branch and keep changes scoped.
2. Run backend `python -m pytest` and frontend `npm test`, `npm run lint`, and `npm run build`.
3. Submit for review.

## License

This repository has no `LICENSE` file. Do not assume redistribution rights.
