import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { applyClick } from "../utils/applyNavigation";
import { recordJobApply } from "../api";

vi.mock("../api", () => ({ recordJobApply: vi.fn() }));

// Faithful to real browser behavior (per spec/MDN): window.open() returns
// null whenever the features string contains "noopener" or "noreferrer" —
// this is the exact root cause of the "stuck at about:blank" bug (the code
// used to pass those flags on the PRE-OPEN call, so it lost its own handle
// to the tab and every later `if (newTab)` branch silently did nothing).
// A mock that always returns a tab object (regardless of arguments) would
// never have caught that regression, so this one actually checks the
// features string, same as a real browser would.
function installWindowOpenMock() {
  const tab = {
    location: { href: "" },
    opener: {},
    closed: false,
    close: vi.fn(function close() {
      this.closed = true;
    }),
  };
  const openSpy = vi.fn((url, target, features) => {
    if (features && /noopener|noreferrer/i.test(features)) return null;
    return tab;
  });
  vi.stubGlobal("open", openSpy);
  return { tab, openSpy };
}

beforeEach(() => {
  recordJobApply.mockReset();
});

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("applyClick", () => {
  it("pre-opens the tab WITHOUT noopener/noreferrer so the handle survives, and severs opener manually", async () => {
    const { tab, openSpy } = installWindowOpenMock();
    recordJobApply.mockResolvedValue({ application_url: "https://careers.example.com/job/1", status: "APPLY_STARTED" });
    await applyClick({ userId: 1, discoveredJobId: 42 });
    expect(openSpy).toHaveBeenCalledWith("", "_blank");
    expect(tab.opener).toBeNull();
  });

  it("1. Apply + valid returned URL: pre-opened tab navigates to it", async () => {
    const { tab } = installWindowOpenMock();
    recordJobApply.mockResolvedValue({ application_url: "https://careers.example.com/job/1", status: "APPLY_STARTED", action_label: "Apply", can_apply: true });
    await applyClick({ userId: 1, discoveredJobId: 42 });
    expect(tab.location.href).toBe("https://careers.example.com/job/1");
    expect(tab.closed).toBe(false);
  });

  it("2. Open Employer Site + valid URL + can_apply=false: tab STILL navigates, never stays about:blank", async () => {
    const { tab } = installWindowOpenMock();
    recordJobApply.mockResolvedValue({
      application_url: "https://careers.example.com/jobs", status: "EMPLOYER_SITE_OPENED",
      action_label: "Open Employer Site", can_apply: false,
    });
    await applyClick({ userId: 1, discoveredJobId: 42 });
    expect(tab.location.href).toBe("https://careers.example.com/jobs");
    expect(tab.closed).toBe(false);
  });

  it("7. View Listing + valid URL: continues opening the listing normally", async () => {
    const { tab } = installWindowOpenMock();
    recordJobApply.mockResolvedValue({
      application_url: "https://www.linkedin.com/jobs/view/1", status: "LISTING_OPENED",
      action_label: "View Listing", can_apply: true,
    });
    await applyClick({ userId: 1, discoveredJobId: 42 });
    expect(tab.location.href).toBe("https://www.linkedin.com/jobs/view/1");
  });

  it("4. backend returns JOB_EXPIRED: blank tab closes, never navigates", async () => {
    const { tab } = installWindowOpenMock();
    recordJobApply.mockResolvedValue({ application_url: null, status: "JOB_EXPIRED", action_label: "Job Closed", can_apply: false });
    const result = await applyClick({ userId: 1, discoveredJobId: 42 });
    expect(tab.close).toHaveBeenCalled();
    expect(tab.location.href).toBe("");
    expect(result.status).toBe("JOB_EXPIRED");
  });

  it("6. missing destination URL (no status field at all): blank tab closes", async () => {
    const { tab } = installWindowOpenMock();
    recordJobApply.mockResolvedValue({ application_url: null });
    await applyClick({ userId: 1, discoveredJobId: 42 });
    expect(tab.close).toHaveBeenCalled();
  });

  it("5. backend error (rejected request): blank tab closes, error propagates", async () => {
    const { tab } = installWindowOpenMock();
    recordJobApply.mockRejectedValue(new Error("network down"));
    await expect(applyClick({ userId: 1, discoveredJobId: 42 })).rejects.toThrow("network down");
    expect(tab.close).toHaveBeenCalled();
  });

  it("navigation decision never depends on can_apply — Open Employer Site (can_apply=false) still navigates", async () => {
    const { tab } = installWindowOpenMock();
    recordJobApply.mockResolvedValue({ application_url: "https://careers.example.com/jobs", can_apply: false, action_label: "Open Employer Site" });
    await applyClick({ userId: 1, discoveredJobId: 42 });
    // The only thing that matters is application_url being present.
    expect(tab.location.href).toBe("https://careers.example.com/jobs");
  });
});
