# Official company career sources

An opt-in extension to **Updated Job Search**. JSearch, Adzuna, Apify Dice,
the existing company connectors, routes, scheduler and matching services are
retained. Original Job Search, ATS optimization, authentication and email are
not changed. No company is fetched until an operator configures it.

## Configuration

New settings are documented in `backend/.env.example` only. No real `.env`
has been edited, scheduler enabled, container restarted or live refresh run.

```dotenv
COMPANY_CAREER_SOURCES=[]
COMPANY_CAREER_PLAYWRIGHT_ENABLED=false
```

`COMPANY_CAREER_SOURCES` is a JSON array (maximum 20 entries). Each entry has
`company_name`, `career_url`, optional `provider` (default `auto`), and
optional boolean `enabled` (default `true`). For example, replace the fictional
employer/URL below with a verified public company board before enabling:

```json
[
  {
    "company_name": "Example Employer",
    "career_url": "https://careers.example.com/jobs",
    "provider": "auto",
    "enabled": true
  }
]
```

Allowed provider values: `auto`, `greenhouse`, `lever`, `workday`, `ashby`,
`smartrecruiters`, `icims`, `successfactors`, `custom`. Supply an employer's
actual board URL, not a job aggregator or login page. Explicit API providers
must agree with the detected board; tenant names are not guessed from arbitrary
custom URL paths. The configured company/board relationship is operator-owned
configuration and must be accurate.

Each company is registered independently using the existing `CompanySource`
and `JobSource` models. Removing/disabling configuration disables its managed
registry entry without deleting jobs or other configured companies. An invalid
configuration logs a fixed diagnostic code and does not initiate collection.

## Collection and fallbacks

| Detected platform | First choice | Fallback |
| --- | --- | --- |
| Greenhouse | Public Job Board API, including content | Permitted HTML if API endpoint is absent |
| Lever (including EU) | Public postings API | Permitted HTML if endpoint is absent |
| Ashby | Public job-board API | Permitted HTML if endpoint is absent |
| SmartRecruiters | Public postings list and detail endpoints | Permitted HTML if endpoint is absent |
| Workday | Career website's read-only CXS JSON search/detail contract | Permitted HTML on absent/changed contract |
| iCIMS / SAP SuccessFactors | Public structured/static HTML | Optional guarded browser rendering |
| Custom company website | JobPosting JSON-LD, explicit job cards and bounded same-site detail links | Optional guarded browser rendering |

Workday CXS is a website contract, **not a guaranteed public integration API**.
iCIMS and SuccessFactors detection does not imply universal scraping support:
site-specific structures or access restrictions may yield no usable jobs.
The pre-existing standalone Workday connector/config-status behavior is not
changed; configured official Workday boards have their own `company_direct:*`
source records.

Detecting an explicit ATS link/iframe on a custom career page selects that
board's collector. Access denial, robots refusal, authentication/challenges,
429, timeout and other provider errors never trigger an attempt to bypass the
restriction. No login, CAPTCHA solving or automatic application submission.

### Optional browser support

No new dependency is needed for public API/static HTML collection. Requests
and BeautifulSoup were already installed. Playwright is deliberately optional
and disabled by default. For an environment where an operator wants it, install
the optional requirements and Chromium in that environment, then opt in:

```sh
python -m pip install -r backend/requirements-career-browser.txt
python -m playwright install chromium
```

These installation commands were **not executed**. Linux/container deployments
also require Chromium's platform libraries; the existing Dockerfile is untouched.
The browser waits for job content only and closes on success or exception.
Service workers, WebSockets, downloads, cross-origin resources and form posts
are blocked. Permitted same-origin GET resources are fulfilled through the
guarded HTTP client rather than letting Chromium make unchecked requests.
Pages requiring blocked resources fail conservatively, not by relaxing safety.

## Existing processing reused

1. Existing manual refresh/scheduler builds the existing adapters plus configured
   company adapters. Each company has its own failure and source-run boundary.
2. The collector uses the existing normalized-job structure. Missing fields stay
   absent/`Not specified`; department is not fabricated as a skill, unknown
   employment type is not fabricated as Full-time, and salary units/currencies
   are not inferred. Unstructured compensation is preserved as metadata.
3. Existing current-user relevance filtering, change detection and persistence
   process the normalized jobs. The existing per-user resume/skill matcher runs
   when the API builds matches; there is no second matching algorithm.
4. Official/aggregator duplicates use a specific URL plus company/title/location,
   or one unambiguous cross-source company/title/location match. Distinct official
   requisitions sharing a title or generic board URL remain separate. Official
   source metadata wins while row identity, first-seen history, source provenance
   and already verified destinations are preserved.
5. Official apply links enter **only** `unverified_application_url`. Existing
   `enrich_job`, candidate verification and availability checks decide whether
   an exact, compatible, active destination qualifies as Apply. Changed candidates
   are rechecked; verification failures do not delete the job or its official
   listing. Unrelated ATS tenants cannot borrow the configured board's identity.
6. The existing discovery API/action labels and card controls remain. Official
   cards add a provenance label and `View Listing` link. Save keeps the official
   listing if no verified direct destination exists, then uses the existing saved
   job workflow. Apply still follows the existing backend-controlled navigation.

The existing enrichment budget and cooldown still apply. Finding an official
listing does **not** promise every result receives verification/Apply immediately.
Company/title/URL/specificity/availability checks are not weakened. Existing
JSearch, Adzuna and Dice discovery/enrichment behavior is retained.

## Schema, source identity and freshness

The only new database column is nullable `discovered_jobs.career_metadata TEXT`,
added through the existing idempotent additive schema setup. Existing records
remain valid with NULL. No new job model, destructive migration or production
database migration was run during implementation.

Existing fields provide company/title/location, work mode/employment type,
description/requirements/skills, external/source IDs, source/apply URLs,
posted time, first-seen and last-seen timestamps. JSON metadata preserves
platform, official board/listing, department, preferred skills and compensation.

Canonical source kind is `company_direct`; `source_platform` identifies the ATS.
Internally, `source` is `company_direct:<stable company+URL hash>` so one
company's missing jobs/outage never changes another company's freshness. API
payloads expose the canonical `source_kind` and human-readable source label.

The existing scheduler marks missing jobs INACTIVE after the configured
`INACTIVE_AFTER_MISSED_POLLS` threshold (default 3), never deletes them. A company
must have a successful **complete** inventory before a missing refresh counts:

- Untruncated successful API inventories qualify.
- HTML qualifies only with an explicit structured ItemList total matching all
  successfully collected listings (including explicit zero).
- Truncated crawls, unknown HTML inventories, failed/malformed responses, blocked
  pages and user-filtered manual refreshes do not establish authoritative absence.

Reappearing official jobs become active again. An old aggregator duplicate
cannot reactivate an inactive official posting. INACTIVE/CLOSED/confirmed-expired
jobs are omitted from normal results; historical rows remain.

## Request limits and diagnostics

Per company/refresh: at most 20 jobs, 30 HTTP requests including robots checks,
2 MiB per response, connect/read timeout 5/12 seconds, four bounded redirect
steps, and a 60-second request-dispatch budget. No automatic retries. Collection
is serial (browser concurrency one), so no unbounded fan-out. A request in flight
may finish after the dispatch deadline within its request timeout.

Connections and per-refresh response/robots caches are reused. Minimum request
spacing is 250 ms, respecting larger robots crawl delays; delays above ten
seconds fail conservatively. HTTPS/public-IP validation runs before every
request/hop. Ambient proxy credentials/cookies are not used. Logs contain source
hashes and fixed codes or exception class names, not response bodies, credentials
or full request URLs. Existing source-run diagnostics report counts/outcomes.

## Validation

New tests mock all provider HTTP, DNS and browser interactions. Regression
validation also uses an outer network guard. On Windows only the standard
library's local `socketpair` connection is exempted for asyncio/TestClient;
external network/DNS and unmocked provider HTTP remain blocked.

Tests cover detection, API contracts, HTML parsing, optional browser behavior,
security/bounds, missing fields, source registration/isolation, additive schema,
official URL preference, bidirectional deduplication, strict verification,
freshness, matching reuse and frontend controls. Live websites and a real
Chromium session were not exercised; provider markup/contracts may change.

Validation results for this change:

- Backend job-search regressions: **577 passed, 0 failed, 1 warning, 20 subtests
  passed** across 22 test files (includes 68 new official-career tests).
- Frontend: **319 passed** across 15 files, including 7 new card tests.
- Frontend production build: **PASS**.
- The full backend suite outside the job-search regression selection was not run.
- The backend warning is the existing Starlette/httpx TestClient deprecation.
  Frontend tests also emitted jsdom's `requestSubmit` not-implemented notice,
  without a test failure.

## File manifest

Added:

```text
backend/app/job_sources/career_sources/__init__.py
backend/app/job_sources/career_sources/adapter.py
backend/app/job_sources/career_sources/ashby.py
backend/app/job_sources/career_sources/base.py
backend/app/job_sources/career_sources/detector.py
backend/app/job_sources/career_sources/generic_html.py
backend/app/job_sources/career_sources/greenhouse.py
backend/app/job_sources/career_sources/http.py
backend/app/job_sources/career_sources/icims.py
backend/app/job_sources/career_sources/lever.py
backend/app/job_sources/career_sources/persistence.py
backend/app/job_sources/career_sources/playwright_fallback.py
backend/app/job_sources/career_sources/smartrecruiters.py
backend/app/job_sources/career_sources/successfactors.py
backend/app/job_sources/career_sources/workday.py
backend/requirements-career-browser.txt
backend/tests/test_company_career_sources.py
backend/tests/test_company_career_pipeline.py
frontend/src/test/company-career-card.test.jsx
docs/company-career-sources.md
```

Modified:

```text
backend/.env.example
backend/app/models/discovered_job_model.py
backend/app/services/job_discovery_schema.py
backend/app/services/job_source_registry_service.py
backend/app/services/updated_job_discovery_service.py
backend/app/services/job_change_detection_service.py
backend/app/routes/job_discovery_routes.py
frontend/src/components/DiscoveredJobCard.jsx
```

Normal pytest cache/temp files and frontend build output are validation artifacts,
not additional source changes. No Git commands were run.

## Public contract references

- [Greenhouse public Job Board API](https://docs.greenhouse.io/job-board.html)
- [Lever public postings API](https://github.com/lever/postings-api)
- [Ashby public job posting API](https://developers.ashbyhq.com/docs/public-job-posting-api)
- [SmartRecruiters posting endpoints](https://developers.smartrecruiters.com/docs/endpoints)
- [Playwright network routing](https://playwright.dev/python/docs/network)
