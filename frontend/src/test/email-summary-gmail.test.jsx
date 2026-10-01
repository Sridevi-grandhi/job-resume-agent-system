import { describe, expect, it, vi, beforeEach } from "vitest";
import { fireEvent, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { renderRoute } from "./render.jsx";
import { makeApp } from "./mockApp";
import EmailSummaryPage from "../pages/EmailSummaryPage";

let app;
vi.mock("../context/AppContext", () => ({ useApp: () => app }));

const hoursAgo = (h) => new Date(Date.now() - h * 60 * 60 * 1000).toISOString();

beforeEach(() => {
  app = makeApp();
});

describe("EmailSummaryPage — Gmail connection", () => {
  it("refreshes stored results and connection status on focus without starting a Gmail scan", () => {
    renderRoute(<EmailSummaryPage />, "/emails");
    const initialLoads = app.loadJobEmailEvents.mock.calls.length;
    fireEvent.focus(window);
    expect(app.loadJobEmailEvents).toHaveBeenCalledTimes(initialLoads + 1);
    expect(app.loadApplications).toHaveBeenCalledTimes(initialLoads + 1);
    expect(app.loadGmailStatus).toHaveBeenCalledTimes(initialLoads + 1);
    expect(app.scanGmailForJobs).not.toHaveBeenCalled();
  });
  it("shows Connect Gmail when not connected", () => {
    renderRoute(<EmailSummaryPage />, "/emails");
    expect(screen.getByText(/not connected/i)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /connect gmail/i })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /scan job emails/i })).toBeNull();
  });

  it("shows Connected + Scan/Disconnect when connected", () => {
    app.gmailStatus = { connected: true, status: "CONNECTED", email_address: "candidate@gmail.com" };
    renderRoute(<EmailSummaryPage />, "/emails");
    expect(document.querySelector(".gmail-connected-line").textContent).toMatch(/connected as candidate@gmail\.com/i);
    expect(screen.getByRole("button", { name: /scan job emails/i })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /disconnect gmail/i })).toBeInTheDocument();
  });

  it("clicking Connect Gmail calls connectGmailAccount", async () => {
    renderRoute(<EmailSummaryPage />, "/emails");
    await userEvent.click(screen.getByRole("button", { name: /connect gmail/i }));
    expect(app.connectGmailAccount).toHaveBeenCalled();
  });

  it("clicking Scan Job Emails calls scanGmailForJobs", async () => {
    app.gmailStatus = { connected: true, status: "CONNECTED", email_address: "candidate@gmail.com" };
    renderRoute(<EmailSummaryPage />, "/emails");
    await userEvent.click(screen.getByRole("button", { name: /scan job emails/i }));
    expect(app.scanGmailForJobs).toHaveBeenCalled();
  });

  it("shows a formatted Last synced timestamp once the account has been scanned", () => {
    app.gmailStatus = { connected: true, status: "CONNECTED", email_address: "candidate@gmail.com", last_scanned_at: "2026-09-17T10:30:00Z" };
    renderRoute(<EmailSummaryPage />, "/emails");
    expect(screen.getByText(/last synced:/i)).toBeInTheDocument();
    expect(screen.queryByText(/last synced:\s*not yet scanned/i)).toBeNull();
  });

  it("shows 'Not yet scanned' when the account has never been scanned", () => {
    app.gmailStatus = { connected: true, status: "CONNECTED", email_address: "candidate@gmail.com", last_scanned_at: null };
    renderRoute(<EmailSummaryPage />, "/emails");
    expect(screen.getByText(/last synced:\s*not yet scanned/i)).toBeInTheDocument();
  });
});

describe("EmailSummaryPage — metric cards (computed from real event data, never hardcoded)", () => {
  it("computes New Emails / Job Opportunities / Recruiter Outreach / Application Updates from jobEmailEvents", () => {
    app.jobEmailEvents = [
      { id: 1, event_type: "RECRUITER_OUTREACH", received_at: hoursAgo(1), is_read: false, resolution: "NEEDS_CONFIRMATION" },
      { id: 2, event_type: "APPLICATION_UPDATE", received_at: hoursAgo(2), is_read: true, resolution: "NEEDS_CONFIRMATION" },
      { id: 3, event_type: "INTERVIEW_INVITE", received_at: hoursAgo(3), is_read: false, resolution: "NEEDS_CONFIRMATION" },
      { id: 4, event_type: "OTHER", received_at: hoursAgo(4), is_read: false, resolution: "NEEDS_CONFIRMATION" },
    ];
    renderRoute(<EmailSummaryPage />, "/emails");
    const metricCards = () => document.querySelector(".email-metric-cards");
    const metric = (label) => within(metricCards()).getByText(label).closest("article");
    // New Emails: unread within the last week -> events 1, 3, 4 (event 2 is read)
    expect(within(metric("New Emails")).getByText("3")).toBeInTheDocument();
    // Job Opportunities: RECRUITER_OUTREACH + OTHER -> events 1, 4
    expect(within(metric("Job Opportunities")).getByText("2")).toBeInTheDocument();
    // Recruiter Outreach: RECRUITER_OUTREACH only -> event 1
    expect(within(metric("Recruiter Outreach")).getByText("1")).toBeInTheDocument();
    // Application Updates -> event 2
    expect(within(metric("Application Updates")).getByText("1")).toBeInTheDocument();
  });

  it("excludes events older than a week from every metric card", () => {
    app.jobEmailEvents = [
      { id: 1, event_type: "RECRUITER_OUTREACH", received_at: hoursAgo(24 * 8), is_read: false, resolution: "NEEDS_CONFIRMATION" },
    ];
    renderRoute(<EmailSummaryPage />, "/emails");
    const metricCards = document.querySelector(".email-metric-cards");
    expect(within(within(metricCards).getByText("Recruiter Outreach").closest("article")).getByText("0")).toBeInTheDocument();
    expect(within(within(metricCards).getByText("New Emails").closest("article")).getByText("0")).toBeInTheDocument();
  });

  it("shows zero on every card with no events, never a stale/example number", () => {
    app.jobEmailEvents = [];
    renderRoute(<EmailSummaryPage />, "/emails");
    const metricCards = document.querySelector(".email-metric-cards");
    ["New Emails", "Job Opportunities", "Recruiter Outreach", "Application Updates"].forEach((label) => {
      expect(within(within(metricCards).getByText(label).closest("article")).getByText("0")).toBeInTheDocument();
    });
  });
});

describe("EmailSummaryPage — scan configuration moved to Settings", () => {
  it("has no duplicate scan settings and does not start a scan on mount", () => {
    renderRoute(<EmailSummaryPage />, "/emails");
    expect(screen.queryByRole("checkbox", { name: /automatic gmail scan|recruiter messages/i })).toBeNull();
    expect(screen.queryByLabelText(/scan frequency/i)).toBeNull();
    expect(screen.queryByRole("button", { name: /save settings/i })).toBeNull();
    expect(app.scanGmailForJobs).not.toHaveBeenCalled();
  });
});

const baseEvents = [
  {
    id: 1, subject: "Sr. Java Developer Opportunity at SES", sender: "recruiter@ses.com", event_type: "RECRUITER_OUTREACH",
    body_snippet: "Hi, I came across your profile and wanted to reach out.", received_at: hoursAgo(1),
    is_read: false, is_starred: false, resolution: "NEEDS_CONFIRMATION", confidence: 0.7,
    matched_application_id: null, matched_job_id: null, match_confidence: null,
  },
  {
    id: 2, subject: "Application Status Update - ABC Corp", sender: "noreply@abccorp.com", event_type: "APPLICATION_UPDATE",
    body_snippet: "Your application for the Java Developer role is still under review.", received_at: hoursAgo(24),
    is_read: true, is_starred: false, resolution: "CONFIRMED", confidence: 0.8,
    matched_application_id: 5, matched_job_id: 9, match_confidence: 0.9,
    matched_application_title: "Java Developer", matched_application_company: "ABC Corp",
  },
  {
    id: 3, subject: "Interview Invitation - Tech Solutions", sender: "hr@techsolutions.com", event_type: "INTERVIEW_INVITE",
    body_snippet: "We would like to invite you for an interview.", received_at: hoursAgo(48),
    is_read: false, is_starred: true, resolution: "NEEDS_CONFIRMATION", confidence: 0.9,
    matched_application_id: 9, matched_job_id: 2, match_confidence: 0.8,
    matched_application_title: "Backend Engineer", matched_application_company: "Tech Solutions",
  },
];

function emailGrid() {
  return document.querySelector(".email-grid");
}

describe("EmailSummaryPage — category tabs, search, sort", () => {
  it("shows all 5 tabs with live counts and never a Daily Summary tab", () => {
    app.jobEmailEvents = baseEvents;
    renderRoute(<EmailSummaryPage />, "/emails");
    expect(screen.getByRole("tab", { name: /all emails \(3\)/i })).toHaveAttribute("aria-selected", "true");
    expect(screen.getByRole("tab", { name: /job opportunities \(1\)/i })).toBeInTheDocument();
    expect(screen.getByRole("tab", { name: /recruiter outreach \(1\)/i })).toBeInTheDocument();
    expect(screen.getByRole("tab", { name: /application updates \(1\)/i })).toBeInTheDocument();
    expect(screen.getByRole("tab", { name: /^interview \(1\)/i })).toBeInTheDocument();
    expect(screen.queryByRole("tab", { name: /daily summary/i })).toBeNull();
  });

  it("clicking a tab filters the visible email cards", async () => {
    app.jobEmailEvents = baseEvents;
    renderRoute(<EmailSummaryPage />, "/emails");
    await userEvent.click(screen.getByRole("tab", { name: /^interview/i }));
    expect(within(emailGrid()).getByText("Interview Invitation - Tech Solutions")).toBeInTheDocument();
    expect(within(emailGrid()).queryByText("Sr. Java Developer Opportunity at SES")).toBeNull();
  });

  it("search filters by subject, sender, matched company/title, and snippet", async () => {
    app.jobEmailEvents = baseEvents;
    renderRoute(<EmailSummaryPage />, "/emails");
    await userEvent.type(screen.getByPlaceholderText(/search emails/i), "tech solutions");
    expect(within(emailGrid()).getByText("Interview Invitation - Tech Solutions")).toBeInTheDocument();
    expect(within(emailGrid()).queryByText("Sr. Java Developer Opportunity at SES")).toBeNull();
    expect(within(emailGrid()).queryByText("Application Status Update - ABC Corp")).toBeNull();
  });

  it("an empty search/filter result shows the empty state, not a broken grid", async () => {
    app.jobEmailEvents = baseEvents;
    renderRoute(<EmailSummaryPage />, "/emails");
    await userEvent.type(screen.getByPlaceholderText(/search emails/i), "no such email anywhere");
    expect(screen.getByText(/no emails found/i)).toBeInTheDocument();
  });

  it("sorts newest first by default and oldest first when selected", async () => {
    app.jobEmailEvents = baseEvents;
    renderRoute(<EmailSummaryPage />, "/emails");
    const subjectsInOrder = () => within(emailGrid()).getAllByRole("heading", { level: 3 }).map((h) => h.textContent);
    expect(subjectsInOrder()[0]).toBe("Sr. Java Developer Opportunity at SES"); // most recent (1h ago)

    await userEvent.selectOptions(screen.getByLabelText(/sort emails/i), "oldest");
    expect(subjectsInOrder()[0]).toBe("Interview Invitation - Tech Solutions"); // oldest (48h ago)
  });

  it("Unread First sorts unread events ahead of read ones", async () => {
    app.jobEmailEvents = baseEvents; // event 2 is the only read one
    renderRoute(<EmailSummaryPage />, "/emails");
    await userEvent.selectOptions(screen.getByLabelText(/sort emails/i), "unread");
    const subjectsInOrder = within(emailGrid()).getAllByRole("heading", { level: 3 }).map((h) => h.textContent);
    expect(subjectsInOrder.indexOf("Application Status Update - ABC Corp")).toBe(2); // the read one sorts last
  });
});

describe("EmailSummaryPage — email cards: Mark as Read / star (CareerBoost-local only)", () => {
  it("Mark as Read calls markJobEmailEventReadAction with the event id", async () => {
    app.jobEmailEvents = [baseEvents[0]];
    renderRoute(<EmailSummaryPage />, "/emails");
    await userEvent.click(screen.getByRole("button", { name: /^mark as read$/i }));
    expect(app.markJobEmailEventReadAction).toHaveBeenCalledWith(1);
  });

  it("shows a disabled 'Read' button once is_read is true", () => {
    app.jobEmailEvents = [{ ...baseEvents[0], is_read: true }];
    renderRoute(<EmailSummaryPage />, "/emails");
    expect(screen.getByRole("button", { name: /^read$/i })).toBeDisabled();
    expect(screen.queryByRole("button", { name: /^mark as read$/i })).toBeNull();
  });

  it("clicking the star calls toggleJobEmailEventStarAction — CareerBoost-local, never a Gmail call", async () => {
    app.jobEmailEvents = [baseEvents[0]];
    renderRoute(<EmailSummaryPage />, "/emails");
    await userEvent.click(screen.getByRole("button", { name: /^star email$/i }));
    expect(app.toggleJobEmailEventStarAction).toHaveBeenCalledWith(1);
  });

  it("shows an 'Unstar' label once is_starred is true", () => {
    app.jobEmailEvents = [{ ...baseEvents[0], is_starred: true }];
    renderRoute(<EmailSummaryPage />, "/emails");
    expect(screen.getByRole("button", { name: /^unstar email$/i })).toBeInTheDocument();
  });
});

describe("EmailSummaryPage — View Details drawer", () => {
  it("opens with subject, sender, received time, and classification", async () => {
    app.jobEmailEvents = [baseEvents[0]];
    renderRoute(<EmailSummaryPage />, "/emails");
    await userEvent.click(screen.getByRole("button", { name: /view details/i }));
    const dialog = screen.getByRole("dialog", { name: /email details/i });
    expect(within(dialog).getByRole("heading", { name: "Sr. Java Developer Opportunity at SES" })).toBeInTheDocument();
    expect(within(dialog).getByText(/from: recruiter@ses\.com/i)).toBeInTheDocument();
    expect(within(dialog).getByText(/received:/i)).toBeInTheDocument();
    expect(within(dialog).getByText(/classification:/i)).toBeInTheDocument();
  });

  it("opens the correct email's details when multiple cards are shown", async () => {
    app.jobEmailEvents = baseEvents;
    renderRoute(<EmailSummaryPage />, "/emails");
    const card = within(emailGrid()).getByText("Interview Invitation - Tech Solutions").closest("article");
    await userEvent.click(within(card).getByRole("button", { name: /view details/i }));
    expect(within(screen.getByRole("dialog")).getByRole("heading", { name: "Interview Invitation - Tech Solutions" })).toBeInTheDocument();
  });

  it("closes via the close button", async () => {
    app.jobEmailEvents = [baseEvents[0]];
    renderRoute(<EmailSummaryPage />, "/emails");
    await userEvent.click(screen.getByRole("button", { name: /view details/i }));
    expect(screen.getByRole("dialog")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: /close email details/i }));
    expect(screen.queryByRole("dialog")).toBeNull();
  });

  // Professional email formatting fix — View Details' full body preserves
  // the original line/paragraph breaks (body_formatted) instead of showing
  // one flattened line (body_snippet).
  it("shows the paragraph-formatted body (not the flattened snippet)", async () => {
    app.jobEmailEvents = [{
      id: 20, subject: "Application Received", sender: "no-reply@ats-vendor.example.com", event_type: "APPLICATION_CONFIRMATION",
      confidence: 0.9, matched_application_id: null, match_confidence: null,
      resolution: "NEEDS_CONFIRMATION",
      body_snippet: "Hi, Thank you for applying. Best regards, HR Team",
      body_formatted: "Hi,\n\nThank you for applying.\n\nBest regards,\nHR Team",
      received_at: hoursAgo(1), is_read: false, is_starred: false,
    }];
    renderRoute(<EmailSummaryPage />, "/emails");
    await userEvent.click(screen.getByRole("button", { name: /view details/i }));
    const preview = document.querySelector(".uj-drawer .email-preview");
    expect(preview.textContent).toBe("Hi,\n\nThank you for applying.\n\nBest regards,\nHR Team");
  });

  it("falls back to the flattened body_snippet when body_formatted is not present (e.g. a legacy row)", async () => {
    app.jobEmailEvents = [{
      id: 22, subject: "Application Received", sender: "no-reply@ats-vendor.example.com", event_type: "APPLICATION_CONFIRMATION",
      confidence: 0.9, matched_application_id: null, match_confidence: null,
      resolution: "NEEDS_CONFIRMATION",
      body_snippet: "Hi, Thank you for applying.",
      received_at: hoursAgo(1), is_read: false, is_starred: false,
    }];
    renderRoute(<EmailSummaryPage />, "/emails");
    await userEvent.click(screen.getByRole("button", { name: /view details/i }));
    const preview = document.querySelector(".uj-drawer .email-preview");
    expect(preview.textContent).toBe("Hi, Thank you for applying.");
  });

  it("keeps the compact email-list row snippet as the flattened body_snippet, unaffected by body_formatted", async () => {
    app.jobEmailEvents = [{
      id: 23, subject: "Application Received", sender: "no-reply@ats-vendor.example.com", event_type: "APPLICATION_CONFIRMATION",
      confidence: 0.9, matched_application_id: null, match_confidence: null,
      resolution: "NEEDS_CONFIRMATION",
      body_snippet: "Hi, Thank you for applying. Best regards, HR Team",
      body_formatted: "Hi,\n\nThank you for applying.\n\nBest regards,\nHR Team",
      received_at: hoursAgo(1), is_read: false, is_starred: false,
    }];
    renderRoute(<EmailSummaryPage />, "/emails");
    const row = within(emailGrid()).getByText("Application Received").closest("article");
    expect(within(row).getByText("Hi, Thank you for applying. Best regards, HR Team")).toBeInTheDocument();
  });

  it("shows 'No reliable application match found' and disables Confirm for a low-confidence event", async () => {
    app.jobEmailEvents = [{
      id: 2, subject: "Application Received - CareerBoost Test", sender: "no-reply@ats-vendor.example.com", event_type: "APPLICATION_CONFIRMATION",
      confidence: 0.135, matched_application_id: null, matched_job_id: null, match_confidence: 0.2,
      resolution: "NEEDS_CONFIRMATION", body_snippet: "Thank you for applying for the Software Engineer position.",
      received_at: hoursAgo(1), is_read: false, is_starred: false,
    }];
    renderRoute(<EmailSummaryPage />, "/emails");
    await userEvent.click(screen.getByRole("button", { name: /view details/i }));
    expect(screen.getByText(/no reliable application match found/i)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /^confirm$/i })).toBeDisabled();
    expect(screen.getByRole("button", { name: /^ignore$/i })).not.toBeDisabled();
  });

  // Safety bug fix: the "No reliable application match found" message and
  // Confirm's disabled state must never disagree. Confidence (the blended
  // classifier score, e.g. 27%) is a DIFFERENT field from match_confidence
  // (the reliability score) — a low classifier confidence must never by
  // itself disable Confirm when the match itself is reliable.
  it("a reliable match enables Confirm even when the blended classifier confidence is low (e.g. 27%)", async () => {
    app.jobEmailEvents = [{
      id: 7, subject: "Application Update", sender: "hr@abccorp.com", event_type: "APPLICATION_UPDATE",
      confidence: 0.27, matched_application_id: 5, matched_job_id: 9, match_confidence: 0.8,
      matched_application_title: "Java Developer", matched_application_company: "ABC Corp",
      resolution: "NEEDS_CONFIRMATION", body_snippet: "Your application is under review.",
      received_at: hoursAgo(1), is_read: false, is_starred: false,
    }];
    renderRoute(<EmailSummaryPage />, "/emails");
    await userEvent.click(screen.getByRole("button", { name: /view details/i }));
    expect(screen.getByText(/confidence.*27%/i)).toBeInTheDocument();
    expect(screen.queryByText(/no reliable application match found/i)).toBeNull();
    expect(screen.getByText(/matched application:/i)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /^confirm$/i })).not.toBeDisabled();
    expect(screen.queryByText(/confirm is unavailable/i)).toBeNull();
  });

  it("a reliable match with no title/company still shows the matched line (never the contradictory no-match message) and keeps Confirm enabled", async () => {
    app.jobEmailEvents = [{
      id: 9, subject: "Application Update", sender: "hr@abccorp.com", event_type: "APPLICATION_UPDATE",
      confidence: 0.9, matched_application_id: 5, matched_job_id: 9, match_confidence: 0.8,
      matched_application_title: null, matched_application_company: null,
      resolution: "NEEDS_CONFIRMATION", body_snippet: "Your application is under review.",
      received_at: hoursAgo(1), is_read: false, is_starred: false,
    }];
    renderRoute(<EmailSummaryPage />, "/emails");
    await userEvent.click(screen.getByRole("button", { name: /view details/i }));
    expect(screen.queryByText(/no reliable application match found/i)).toBeNull();
    expect(screen.getByText(/matched application:/i)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /^confirm$/i })).not.toBeDisabled();
  });

  it("shows the 'Confirm is unavailable' helper text only when there is no reliable match", async () => {
    app.jobEmailEvents = [{
      id: 10, subject: "Application Received", sender: "no-reply@ats-vendor.example.com", event_type: "APPLICATION_CONFIRMATION",
      confidence: 0.27, matched_application_id: null, matched_job_id: null, match_confidence: 0.2,
      resolution: "NEEDS_CONFIRMATION", body_snippet: "Thank you for applying.",
      received_at: hoursAgo(1), is_read: false, is_starred: false,
    }];
    renderRoute(<EmailSummaryPage />, "/emails");
    await userEvent.click(screen.getByRole("button", { name: /view details/i }));
    expect(screen.getByText(/confirm is unavailable because no reliable application match was found/i)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /^confirm$/i })).toBeDisabled();
    expect(screen.getByRole("button", { name: /^ignore$/i })).not.toBeDisabled();
  });

  it("a reliable match enables Confirm and shows the matched application", async () => {
    app.jobEmailEvents = [{
      id: 3, subject: "Interview Invitation", sender: "hr@abccorp.com", event_type: "INTERVIEW_INVITE",
      confidence: 0.8, matched_application_id: 5, matched_job_id: 9, match_confidence: 0.8,
      matched_application_title: "Java Developer", matched_application_company: "ABC Corp",
      resolution: "NEEDS_CONFIRMATION", body_snippet: "We would like to schedule an interview.",
      received_at: hoursAgo(1), is_read: false, is_starred: false,
    }];
    renderRoute(<EmailSummaryPage />, "/emails");
    await userEvent.click(screen.getByRole("button", { name: /view details/i }));
    expect(screen.queryByText(/no reliable application match found/i)).toBeNull();
    expect(screen.getByText(/matched application:/i)).toBeInTheDocument();
    expect(screen.getByText(/java developer.*abc corp/i)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /^confirm$/i })).not.toBeDisabled();
  });

  it("a legacy row with matched_application_id set but match_confidence=null stays unconfirmable, but Ignore still works", async () => {
    app.jobEmailEvents = [{
      id: 6, subject: "Application Received", sender: "no-reply@ats-vendor.example.com", event_type: "APPLICATION_CONFIRMATION",
      confidence: 0.135, matched_application_id: 2, matched_job_id: 1111, match_confidence: null,
      resolution: "NEEDS_CONFIRMATION", body_snippet: "Thank you for applying.",
      received_at: hoursAgo(1), is_read: false, is_starred: false,
    }];
    renderRoute(<EmailSummaryPage />, "/emails");
    await userEvent.click(screen.getByRole("button", { name: /view details/i }));
    expect(screen.getByRole("button", { name: /^confirm$/i })).toBeDisabled();
    await userEvent.click(screen.getByRole("button", { name: /^ignore$/i }));
    expect(app.resolveJobEmailEventAction).toHaveBeenCalledWith(6, "ignore");
  });

  it("Confirm calls resolveJobEmailEventAction for a reliable match", async () => {
    app.jobEmailEvents = [{
      id: 8, subject: "Application Received - ABC Corp", sender: "careers@abccorp.com", event_type: "APPLICATION_CONFIRMATION",
      confidence: 0.45, matched_application_id: 9, matched_job_id: 1, match_confidence: 0.5,
      resolution: "NEEDS_CONFIRMATION", body_snippet: "Thank you for applying to ABC Corp.",
      received_at: hoursAgo(1), is_read: false, is_starred: false,
    }];
    renderRoute(<EmailSummaryPage />, "/emails");
    await userEvent.click(screen.getByRole("button", { name: /view details/i }));
    await userEvent.click(screen.getByRole("button", { name: /^confirm$/i }));
    expect(app.resolveJobEmailEventAction).toHaveBeenCalledWith(8, "confirm");
  });

  it("Confirm/Ignore never render for an already-CONFIRMED event; shows the confirmed line instead", async () => {
    app.jobEmailEvents = [{
      id: 7, subject: "Job Offer Update", sender: "hr@abccorp.com", event_type: "OFFER",
      confidence: 0.9, matched_application_id: 5, match_confidence: 0.9,
      matched_application_title: "Java Developer", matched_application_company: "ABC Corp",
      resolution: "CONFIRMED", body_snippet: "Status updated.", received_at: hoursAgo(1), is_read: false, is_starred: false,
    }];
    renderRoute(<EmailSummaryPage />, "/emails");
    await userEvent.click(screen.getByRole("button", { name: /view details/i }));
    expect(screen.queryByRole("button", { name: /^confirm$/i })).toBeNull();
    expect(screen.queryByRole("button", { name: /^ignore$/i })).toBeNull();
    expect(screen.getByText(/application update confirmed/i)).toBeInTheDocument();
  });

  it("shows the possible-update confidence line only while NEEDS_CONFIRMATION", async () => {
    app.jobEmailEvents = [{
      id: 1, subject: "Interview Invitation", sender: "hr@abccorp.com", event_type: "INTERVIEW_INVITE",
      confidence: 0.4, matched_application_id: null, resolution: "NEEDS_CONFIRMATION",
      body_snippet: "We would like to schedule an interview.", received_at: hoursAgo(1), is_read: false, is_starred: false,
    }];
    renderRoute(<EmailSummaryPage />, "/emails");
    await userEvent.click(screen.getByRole("button", { name: /view details/i }));
    expect(document.querySelector(".possible-update-line").textContent).toMatch(/confidence.*40%/i);
  });
});

// Recruiter Draft / Suggested Reply feature removal: CareerBoost's Gmail
// integration is read-only, so a generated reply could never actually be
// sent from the app — View Details now ends at Confirm/Ignore + the full
// formatted body, with no reply-generation UI of any kind.
describe("EmailSummaryPage — no recruiter draft / suggested reply UI anywhere", () => {
  const outreachEvent = {
    id: 2, subject: "Exciting opportunity", sender: "Jane Recruiter <recruiter@abccorp.com>",
    event_type: "RECRUITER_OUTREACH", body_snippet: "I came across your profile.",
    body_formatted: "I came across your profile.",
    matched_application_id: null, matched_application_title: null, matched_application_company: null,
    match_confidence: null, resolution: "NEEDS_CONFIRMATION", confidence: 0.7,
    received_at: hoursAgo(1), is_read: false, is_starred: false,
  };

  it("shows no Tone selector, Generate/Suggested Reply, or reply editor for a RECRUITER_OUTREACH event", async () => {
    app.jobEmailEvents = [outreachEvent];
    renderRoute(<EmailSummaryPage />, "/emails");
    await userEvent.click(screen.getByRole("button", { name: /view details/i }));
    const dialog = screen.getByRole("dialog");
    expect(within(dialog).queryByLabelText(/tone/i)).toBeNull();
    expect(within(dialog).queryByRole("button", { name: /generate reply/i })).toBeNull();
    expect(within(dialog).queryByText(/generating…/i)).toBeNull();
    expect(within(dialog).queryByRole("heading", { name: /suggested reply/i })).toBeNull();
    expect(within(dialog).queryByRole("button", { name: /^regenerate$/i })).toBeNull();
    expect(within(dialog).queryByRole("button", { name: /^copy$/i })).toBeNull();
    expect(within(dialog).queryByRole("textbox", { name: /body/i })).toBeNull();
  });

  it("View Details ends with the full formatted body and nothing after it", async () => {
    app.jobEmailEvents = [outreachEvent];
    renderRoute(<EmailSummaryPage />, "/emails");
    await userEvent.click(screen.getByRole("button", { name: /view details/i }));
    const dialog = screen.getByRole("dialog");
    const lastElement = dialog.lastElementChild;
    expect(lastElement.className).toMatch(/email-preview/);
    expect(lastElement.textContent).toBe("I came across your profile.");
  });
});
