import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { renderRoute } from "./render.jsx";
import { makeApp } from "./mockApp";
import UpdatedJobSearchPage from "../pages/UpdatedJobSearchPage";

let app;
vi.mock("../context/AppContext", () => ({ useApp: () => app }));

const discoveredJob = {
  discovered_job_id: 101,
  title: "Senior QA Automation Engineer",
  company: "Example Employer",
  company_logo_url: null,
  location: "Remote",
  work_mode: "Remote",
  job_type: "Full-time",
  description: "Own quality for our platform using Selenium and Java.",
  skills: "Selenium, Java, API Testing",
  source: "greenhouse",
  source_mode: "live",
  direct_url: "https://boards.greenhouse.io/example-employer/jobs/1",
  application_url: "https://boards.greenhouse.io/example-employer/jobs/1",
  status: "ACTIVE",
  first_seen_at: new Date().toISOString(),
  last_seen_at: new Date().toISOString(),
  posted_at: "2026-08-27T00:00:00Z",
  detected_label: "Recently Detected",
  match: {
    match_score: 84,
    fitting_skills: ["Java", "Selenium", "API Testing"],
    missing_skills: ["Playwright"],
    verdict: "Strong QA automation alignment with a gap in Playwright.",
    role_alignment: "STRONG",
    confidence: "HIGH",
  },
  needs_resume: false,
};

function jsonResponse(body) {
  return Promise.resolve(new Response(JSON.stringify(body), { status: 200, headers: { "Content-Type": "application/json" } }));
}

function mockFetchWith({ jobsBody, hasResume = true, applyResponse } = {}) {
  globalThis.fetch = vi.fn((url, options = {}) => {
    const href = String(url);
    if (href.includes("/api/job-discovery/jobs/")) {
      return jsonResponse(jobsBody ?? {
        jobs: [discoveredJob],
        summary: { recently_detected: 1, strong_matches: 1, companies_monitored: 1, sources_checked: 4, last_updated: new Date().toISOString() },
        sources: [{ source_name: "greenhouse", enabled: true, healthy: true, mode: "live" }],
        has_resume: hasResume,
        has_preferences: true,
      });
    }
    if (href.includes("/api/job-discovery/refresh/")) {
      return jsonResponse({ already_running: false, total_new: 1, total_updated: 0, sources: [] });
    }
    if (href.includes("/api/job-discovery/save/") && options.method === "POST") {
      return jsonResponse({ message: "Job saved to Selected Jobs.", already_saved: false, job_id: 555, workflow_status: "PENDING" });
    }
    if (href.includes("/api/job-discovery/apply/") && options.method === "POST") {
      const body = JSON.parse(options.body);
      const job = [
        discoveredJob,
        ...(jobsBody?.jobs || []),
      ].find((candidate) => candidate.discovered_job_id === body.discovered_job_id);
      return jsonResponse(applyResponse ?? { application_url: job?.application_url, status: "APPLY_STARTED" });
    }
    return jsonResponse({});
  });
}

// Apply is backend-verified-then-navigate (Step 6): a blank tab is opened
// synchronously inside the click handler, then pointed at the backend's
// resolved destination once it responds — never a plain <a href> anymore.
// Faithful to real browser behavior (per spec/MDN): window.open() returns
// null whenever the features string contains "noopener"/"noreferrer" — the
// exact root cause of the "stuck at about:blank" bug (the pre-open call
// used to pass those flags and so lost its own handle to the tab). A mock
// that always returns a tab object regardless of arguments would never
// have caught that regression.
function mockWindowOpen() {
  const tab = { location: { href: "" }, opener: {}, closed: false, close: vi.fn(function close() { this.closed = true; }) };
  const openSpy = vi.fn((url, target, features) => {
    if (features && /noopener|noreferrer/i.test(features)) return null;
    return tab;
  });
  vi.stubGlobal("open", openSpy);
  return tab;
}

beforeEach(() => {
  app = makeApp();
});

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("UpdatedJobSearchPage", () => {
  it.each([
    [100, "Strong Match", "tone-strong"],
    [79, "Good Match", "tone-good"],
  ])("shows the unchanged %s%% score in the circular match UI", async (score, label, tone) => {
    const scoredJob = { ...discoveredJob, match: { ...discoveredJob.match, match_score: score } };
    mockFetchWith({
      jobsBody: {
        jobs: [scoredJob],
        summary: { recently_detected: 1, strong_matches: score >= 85 ? 1 : 0, companies_monitored: 1, sources_checked: 4, last_updated: new Date().toISOString() },
        sources: [],
        has_resume: true,
        has_preferences: true,
      },
    });

    renderRoute(<UpdatedJobSearchPage />, "/updated-jobs");

    const ring = await screen.findByLabelText(new RegExp(`Resume match ${score} percent — ${label}`, "i"));
    expect(ring).toHaveClass("match-score-ring", "uj-match-score-ring", tone);
    expect(within(ring).getByText(`${score}%`)).toBeInTheDocument();
    expect(within(ring).getByText(label)).toBeInTheDocument();
    expect(ring.closest(".job-title-row")).not.toBeNull();
  });

  it("does not render a fake match ring when the score is missing", async () => {
    const unscoredJob = { ...discoveredJob, match: { ...discoveredJob.match, match_score: null } };
    mockFetchWith({
      jobsBody: {
        jobs: [unscoredJob],
        summary: { recently_detected: 1, strong_matches: 0, companies_monitored: 1, sources_checked: 4, last_updated: new Date().toISOString() },
        sources: [],
        has_resume: true,
        has_preferences: true,
      },
    });

    renderRoute(<UpdatedJobSearchPage />, "/updated-jobs");
    await screen.findByText("Senior QA Automation Engineer");

    expect(document.querySelector(".uj-match-score-ring")).toBeNull();
    expect(screen.queryByText("0%")).toBeNull();
  });

  it("keeps the Optimize Resume save-and-navigation handler unchanged", async () => {
    mockFetchWith();
    renderRoute(<UpdatedJobSearchPage />, "/updated-jobs");
    await screen.findByText("Senior QA Automation Engineer");

    await userEvent.click(screen.getAllByRole("button", { name: /^optimize resume$/i })[0]);

    await waitFor(() => expect(app.setAtsSelectedJobId).toHaveBeenCalledWith("555"));
    expect(sessionStorage.getItem("atsSelectedJobId")).toBe("555");
    expect(globalThis.fetch.mock.calls.some(([url]) => String(url).includes("/api/job-discovery/save/"))).toBe(true);
  });

  it("loads and renders discovered jobs by real company — no ATS/connector name, no mock label", async () => {
    mockFetchWith();
    renderRoute(<UpdatedJobSearchPage />, "/updated-jobs");
    await waitFor(() => expect(screen.getByText("Senior QA Automation Engineer")).toBeInTheDocument());
    expect(screen.getByText("Example Employer")).toBeInTheDocument();
    expect(screen.getAllByText(/Recently Detected/).length).toBeGreaterThan(0);

    const bodyText = document.body.textContent;
    expect(bodyText).not.toMatch(/greenhouse/i);
    expect(bodyText).not.toMatch(/\blever\b/i);
    expect(bodyText).not.toMatch(/\bworkday\b/i);
    expect(bodyText).not.toMatch(/mock source/i);
  });

  it("Apply asks the backend to verify, then opens the resolved application_url in a new tab", async () => {
    mockFetchWith();
    const tab = mockWindowOpen();
    renderRoute(<UpdatedJobSearchPage />, "/updated-jobs");
    await waitFor(() => expect(screen.getByText("Senior QA Automation Engineer")).toBeInTheDocument());
    await userEvent.click(screen.getAllByRole("button", { name: /^apply$/i })[0]);
    await waitFor(() => expect(tab.location.href).toBe(discoveredJob.application_url));
    expect(globalThis.fetch.mock.calls.some(([url]) => String(url).includes("/api/job-discovery/apply/"))).toBe(true);
  });

  it("Apply click that the backend reports as expired shows Job Closed and never opens a dead tab", async () => {
    mockFetchWith({
      applyResponse: {
        status: "JOB_EXPIRED", application_url: null,
        availability_status: "EXPIRED", availability_reason: "http_404",
      },
    });
    const tab = mockWindowOpen();
    renderRoute(<UpdatedJobSearchPage />, "/updated-jobs");
    await waitFor(() => expect(screen.getByText("Senior QA Automation Engineer")).toBeInTheDocument());
    await userEvent.click(screen.getAllByRole("button", { name: /^apply$/i })[0]);
    await waitFor(() => expect(tab.close).toHaveBeenCalled());
    expect(tab.location.href).toBe("");
    await waitFor(() => expect(screen.getAllByRole("button", { name: /^job closed$/i }).length).toBeGreaterThan(0));
    expect(screen.getByText(/this job just closed/i)).toBeInTheDocument();
  });

  it("2. Open Employer Site + can_apply=false still navigates the pre-opened tab to the real employer URL — root-cause regression for the about:blank bug", async () => {
    const openEmployerJob = {
      ...discoveredJob,
      discovered_job_id: 505,
      apply_destination_type: "DIRECT_EMPLOYER",
      destination_specificity: "SPECIFIC_JOB",
      availability_status: "UNKNOWN",
      action_label: "Open Employer Site",
      can_apply: false,
    };
    mockFetchWith({
      jobsBody: {
        jobs: [openEmployerJob],
        summary: { recently_detected: 1, strong_matches: 1, companies_monitored: 1, sources_checked: 4, last_updated: new Date().toISOString() },
        sources: [{ source_name: "greenhouse", enabled: true, healthy: true, mode: "live" }],
        has_resume: true, has_preferences: true,
      },
      applyResponse: {
        status: "EMPLOYER_SITE_OPENED", application_url: openEmployerJob.application_url,
        action_label: "Open Employer Site", can_apply: false,
      },
    });
    const tab = mockWindowOpen();
    renderRoute(<UpdatedJobSearchPage />, "/updated-jobs");
    await waitFor(() => expect(screen.getByText("Senior QA Automation Engineer")).toBeInTheDocument());
    const button = screen.getByRole("button", { name: /open employer site/i });
    await userEvent.click(button);
    await waitFor(() => expect(tab.location.href).toBe(openEmployerJob.application_url));
    expect(tab.closed).toBe(false);
  });

  it("3. a job already resolved as Job Closed on load renders a disabled button — no click, no tab opened", async () => {
    const closedJob = {
      ...discoveredJob,
      discovered_job_id: 606,
      apply_destination_type: "DIRECT_EMPLOYER",
      availability_status: "EXPIRED",
      action_label: "Job Closed",
      can_apply: false,
    };
    mockFetchWith({
      jobsBody: {
        jobs: [closedJob],
        summary: { recently_detected: 1, strong_matches: 1, companies_monitored: 1, sources_checked: 4, last_updated: new Date().toISOString() },
        sources: [{ source_name: "greenhouse", enabled: true, healthy: true, mode: "live" }],
        has_resume: true, has_preferences: true,
      },
    });
    const openSpy = vi.fn();
    vi.stubGlobal("open", openSpy);
    renderRoute(<UpdatedJobSearchPage />, "/updated-jobs");
    await waitFor(() => expect(screen.getByText("Senior QA Automation Engineer")).toBeInTheDocument());
    const button = screen.getByRole("button", { name: /^job closed$/i });
    expect(button).toBeDisabled();
    await userEvent.click(button);
    expect(openSpy).not.toHaveBeenCalled();
    expect(globalThis.fetch.mock.calls.some(([url]) => String(url).includes("/api/job-discovery/apply/"))).toBe(false);
  });

  it("Details drawer: Open Employer Site + can_apply=false still navigates to the real employer URL", async () => {
    const openEmployerJob = {
      ...discoveredJob,
      discovered_job_id: 707,
      apply_destination_type: "DIRECT_EMPLOYER",
      destination_specificity: "SPECIFIC_JOB",
      availability_status: "UNKNOWN",
      action_label: "Open Employer Site",
      can_apply: false,
    };
    mockFetchWith({
      jobsBody: {
        jobs: [openEmployerJob],
        summary: { recently_detected: 1, strong_matches: 1, companies_monitored: 1, sources_checked: 4, last_updated: new Date().toISOString() },
        sources: [{ source_name: "greenhouse", enabled: true, healthy: true, mode: "live" }],
        has_resume: true, has_preferences: true,
      },
      applyResponse: {
        status: "EMPLOYER_SITE_OPENED", application_url: openEmployerJob.application_url,
        action_label: "Open Employer Site", can_apply: false,
      },
    });
    const tab = mockWindowOpen();
    renderRoute(<UpdatedJobSearchPage />, "/updated-jobs");
    await waitFor(() => expect(screen.getByText("Senior QA Automation Engineer")).toBeInTheDocument());
    await userEvent.click(screen.getAllByRole("button", { name: /^details$/i })[0]);
    const drawer = screen.getByRole("dialog", { name: /job details/i });
    await userEvent.click(within(drawer).getByRole("button", { name: /open employer site/i }));
    await waitFor(() => expect(tab.location.href).toBe(openEmployerJob.application_url));
  });

  it("Details drawer: a job already resolved as Job Closed renders a disabled button — no navigation", async () => {
    const closedJob = {
      ...discoveredJob,
      discovered_job_id: 808,
      apply_destination_type: "DIRECT_EMPLOYER",
      availability_status: "EXPIRED",
      action_label: "Job Closed",
      can_apply: false,
    };
    mockFetchWith({
      jobsBody: {
        jobs: [closedJob],
        summary: { recently_detected: 1, strong_matches: 1, companies_monitored: 1, sources_checked: 4, last_updated: new Date().toISOString() },
        sources: [{ source_name: "greenhouse", enabled: true, healthy: true, mode: "live" }],
        has_resume: true, has_preferences: true,
      },
    });
    const openSpy = vi.fn();
    vi.stubGlobal("open", openSpy);
    renderRoute(<UpdatedJobSearchPage />, "/updated-jobs");
    await waitFor(() => expect(screen.getByText("Senior QA Automation Engineer")).toBeInTheDocument());
    await userEvent.click(screen.getAllByRole("button", { name: /^details$/i })[0]);
    const drawer = screen.getByRole("dialog", { name: /job details/i });
    const button = within(drawer).getByRole("button", { name: /^job closed$/i });
    expect(button).toBeDisabled();
    await userEvent.click(button);
    expect(openSpy).not.toHaveBeenCalled();
  });

  it("Details opens a drawer with the full job description and View Original Posting", async () => {
    mockFetchWith();
    renderRoute(<UpdatedJobSearchPage />, "/updated-jobs");
    await waitFor(() => expect(screen.getByText("Senior QA Automation Engineer")).toBeInTheDocument());
    await userEvent.click(screen.getAllByRole("button", { name: /^details$/i })[0]);
    expect(screen.getByText(discoveredJob.description)).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /view original posting/i })).toHaveAttribute("href", discoveredJob.direct_url);
    expect(screen.getByText(/matching skills/i)).toBeInTheDocument();
    expect(screen.getByText(/missing.*gap skills/i)).toBeInTheDocument();
  });

  it("keeps the complete Details drawer in a resettable, keyboard-focusable scroll region", async () => {
    const longDescription = `${discoveredJob.description}\n\n${"Additional role detail. ".repeat(250)}`;
    mockFetchWith({
      jobsBody: {
        jobs: [{ ...discoveredJob, description: longDescription }],
        summary: { recently_detected: 1, strong_matches: 1, companies_monitored: 1, sources_checked: 4, last_updated: new Date().toISOString() },
        sources: [],
        has_resume: true,
        has_preferences: true,
      },
    });
    renderRoute(<UpdatedJobSearchPage />, "/updated-jobs");
    await waitFor(() => expect(screen.getByText("Senior QA Automation Engineer")).toBeInTheDocument());
    await userEvent.click(screen.getByRole("button", { name: /^details$/i }));

    const drawer = screen.getByRole("dialog", { name: /job details/i });
    expect(drawer).toHaveAttribute("tabindex", "0");
    expect(within(drawer).getByText("Job Description")).toBeInTheDocument();
    expect(within(drawer).getByRole("button", { name: /close job details/i })).toBeInTheDocument();
    drawer.scrollTop = 180;
    expect(drawer.scrollTop).toBe(180);

    await userEvent.click(within(drawer).getByRole("button", { name: /close job details/i }));
    await userEvent.click(screen.getByRole("button", { name: /^details$/i }));
    expect(screen.getByRole("dialog", { name: /job details/i }).scrollTop).toBe(0);
  });

  it("shows an upload-resume notice and no score badge when the user has no resume", async () => {
    mockFetchWith({
      jobsBody: {
        jobs: [{ ...discoveredJob, match: null, needs_resume: true }],
        summary: { recently_detected: 1, strong_matches: 0, companies_monitored: 1, sources_checked: 4, last_updated: null },
        sources: [],
        has_resume: false,
        has_preferences: true,
      },
      hasResume: false,
    });
    renderRoute(<UpdatedJobSearchPage />, "/updated-jobs");
    await waitFor(() => expect(screen.getByText(/Upload a resume to calculate personalized match scores/i)).toBeInTheDocument());
    expect(screen.getByText(/Upload resume to match/i)).toBeInTheDocument();
  });

  it("shows the empty state when no jobs are discovered yet", async () => {
    mockFetchWith({
      jobsBody: {
        jobs: [],
        summary: { recently_detected: 0, strong_matches: 0, companies_monitored: 0, sources_checked: 4, last_updated: null },
        sources: [],
        has_resume: true,
        has_preferences: true,
      },
    });
    renderRoute(<UpdatedJobSearchPage />, "/updated-jobs");
    await waitFor(() => expect(screen.getByText(/No matching jobs detected yet/i)).toBeInTheDocument());
  });

  it("Refresh Jobs triggers a manual poll then reloads the job list", async () => {
    mockFetchWith();
    renderRoute(<UpdatedJobSearchPage />, "/updated-jobs");
    await waitFor(() => expect(screen.getByText("Senior QA Automation Engineer")).toBeInTheDocument());
    await userEvent.click(screen.getByRole("button", { name: /refresh jobs/i }));
    await waitFor(() => {
      const calledRefresh = globalThis.fetch.mock.calls.some(([url]) => String(url).includes("/api/job-discovery/refresh/"));
      expect(calledRefresh).toBe(true);
    });
  });

  it("Save saves the discovered job without navigating away", async () => {
    mockFetchWith();
    renderRoute(<UpdatedJobSearchPage />, "/updated-jobs");
    await waitFor(() => expect(screen.getByText("Senior QA Automation Engineer")).toBeInTheDocument());
    await userEvent.click(screen.getAllByRole("button", { name: /^save$/i })[0]);
    await waitFor(() => expect(screen.getAllByRole("button", { name: /^saved$/i }).length).toBeGreaterThan(0));
  });

  it("a Remotive-only listing (no verified employer URL) shows 'View Listing', never a false 'Apply'", async () => {
    const remotiveOnlyJob = {
      ...discoveredJob,
      discovered_job_id: 202,
      source: "remotive",
      direct_url: null,
      application_url: "https://remotive.com/remote-jobs/software-dev/example-88888",
      is_direct_apply: false,
      apply_destination_type: "AGGREGATOR_LISTING",
      action_label: "View Listing",
      can_apply: true,
    };
    mockFetchWith({
      jobsBody: {
        jobs: [remotiveOnlyJob],
        summary: { recently_detected: 1, strong_matches: 1, companies_monitored: 1, sources_checked: 4, last_updated: new Date().toISOString() },
        sources: [{ source_name: "remotive", enabled: true, healthy: true, mode: "live" }],
        has_resume: true,
        has_preferences: true,
      },
    });
    const tab = mockWindowOpen();
    renderRoute(<UpdatedJobSearchPage />, "/updated-jobs");
    await waitFor(() => expect(screen.getByText("Senior QA Automation Engineer")).toBeInTheDocument());
    expect(screen.queryByRole("button", { name: /^apply$/i })).toBeNull();
    await userEvent.click(screen.getByRole("button", { name: /view listing/i }));
    await waitFor(() => expect(tab.location.href).toBe(remotiveOnlyJob.application_url));
  });

  it("regression: a Remotive-sourced job WITH a real employer application_url still shows Apply pointing at the employer, not remotive.com", async () => {
    const remotiveWithEmployerUrl = {
      ...discoveredJob,
      discovered_job_id: 303,
      source: "remotive",
      direct_url: null,
      application_url: "https://company.com/careers/job/123",
      is_direct_apply: true,
      apply_destination_type: "DIRECT_EMPLOYER",
      action_label: "Apply",
      can_apply: true,
    };
    mockFetchWith({
      jobsBody: {
        jobs: [remotiveWithEmployerUrl],
        summary: { recently_detected: 1, strong_matches: 1, companies_monitored: 1, sources_checked: 4, last_updated: new Date().toISOString() },
        sources: [{ source_name: "remotive", enabled: true, healthy: true, mode: "live" }],
        has_resume: true,
        has_preferences: true,
      },
    });
    const tab = mockWindowOpen();
    renderRoute(<UpdatedJobSearchPage />, "/updated-jobs");
    await waitFor(() => expect(screen.getByText("Senior QA Automation Engineer")).toBeInTheDocument());
    await userEvent.click(screen.getByRole("button", { name: /^apply$/i }));
    await waitFor(() => expect(tab.location.href).toBe("https://company.com/careers/job/123"));
    expect(tab.location.href).not.toMatch(/remotive\.com/);
  });

  it("Details drawer shows 'View Listing' instead of Apply for an aggregator-only job", async () => {
    const remotiveOnlyJob = {
      ...discoveredJob,
      discovered_job_id: 404,
      source: "remotive",
      direct_url: null,
      application_url: "https://remotive.com/remote-jobs/software-dev/example-404",
      is_direct_apply: false,
      apply_destination_type: "AGGREGATOR_LISTING",
      action_label: "View Listing",
      can_apply: true,
    };
    mockFetchWith({
      jobsBody: {
        jobs: [remotiveOnlyJob],
        summary: { recently_detected: 1, strong_matches: 1, companies_monitored: 1, sources_checked: 4, last_updated: new Date().toISOString() },
        sources: [{ source_name: "remotive", enabled: true, healthy: true, mode: "live" }],
        has_resume: true,
        has_preferences: true,
      },
    });
    const tab = mockWindowOpen();
    renderRoute(<UpdatedJobSearchPage />, "/updated-jobs");
    await waitFor(() => expect(screen.getByText("Senior QA Automation Engineer")).toBeInTheDocument());
    await userEvent.click(screen.getAllByRole("button", { name: /^details$/i })[0]);
    const drawer = screen.getByRole("dialog", { name: /job details/i });
    // No "View Original Posting" link either — there's no direct_url, only the listing itself.
    expect(within(drawer).queryByRole("link", { name: /view original posting/i })).toBeNull();
    expect(within(drawer).queryByRole("button", { name: /^apply$/i })).toBeNull();
    await userEvent.click(within(drawer).getByRole("button", { name: /view listing/i }));
    await waitFor(() => expect(tab.location.href).toBe(remotiveOnlyJob.application_url));
  });

  it("Details drawer shows an Availability badge for a direct employer/ATS job", async () => {
    mockFetchWith({
      jobsBody: {
        jobs: [{ ...discoveredJob, availability_status: "ACTIVE", apply_destination_type: "DIRECT_ATS" }],
        summary: { recently_detected: 1, strong_matches: 1, companies_monitored: 1, sources_checked: 4, last_updated: new Date().toISOString() },
        sources: [{ source_name: "greenhouse", enabled: true, healthy: true, mode: "live" }],
        has_resume: true,
        has_preferences: true,
      },
    });
    renderRoute(<UpdatedJobSearchPage />, "/updated-jobs");
    await waitFor(() => expect(screen.getByText("Senior QA Automation Engineer")).toBeInTheDocument());
    await userEvent.click(screen.getAllByRole("button", { name: /^details$/i })[0]);
    const drawer = screen.getByRole("dialog", { name: /job details/i });
    expect(within(drawer).getByText(/^active$/i)).toBeInTheDocument();
  });
});
