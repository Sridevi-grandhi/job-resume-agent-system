import { recordJobApply } from "../api";

// Root cause of the "opens about:blank forever" bug: window.open(url, name,
// features) returns null whenever the features string contains "noopener"
// OR "noreferrer" (per spec — noreferrer implies noopener too), specifically
// so the opener page CANNOT get a handle back to the new window. That's
// exactly the handle this pre-open pattern needs to later navigate the tab,
// so passing either flag here made every `if (newTab)` branch below silently
// no-op — Chrome dutifully opened the blank tab and then NOTHING ever
// touched it again, regardless of what the backend returned. The same
// opener-severing protection is applied manually instead
// (`newTab.opener = null`), which keeps the handle while still preventing
// the destination page from reaching back into this one.
function openBlankTab() {
  const newTab = window.open("", "_blank");
  if (newTab) newTab.opener = null;
  return newTab;
}

// Backend is the single source of truth for the whole Apply / Open Employer
// Site / View Listing / Job Closed decision (Step 6 / Step 15 of the
// availability workflow, plus the destination-specificity fix): a stored
// application_url is never trusted directly — this always asks the backend
// to (re)verify first, then acts on its response, so a job that closed
// since the page loaded never opens a dead link.
//
// The navigate-vs-close decision below is based ONLY on whether the backend
// returned a safe application_url — never on can_apply or status. can_apply
// answers a different question ("is this a verified direct Apply"); Open
// Employer Site and View Listing both return can_apply=false but a real,
// safe URL that must still be opened. Only a response with no URL at all
// (JOB_EXPIRED, or any other reason the backend declines to hand one back)
// means the tab must be closed instead.
//
// The blank tab is opened SYNCHRONOUSLY, before the `await`, and only later
// pointed at the real destination (or closed) — opening a tab AFTER an
// async gap is what triggers popup blockers; pre-opening it inside the
// click handler's own call stack is the standard workaround.
export async function applyClick({ userId, discoveredJobId, targetBlank = true }) {
  const newTab = targetBlank ? openBlankTab() : null;
  try {
    const result = await recordJobApply(userId, discoveredJobId);
    if (result?.application_url) {
      if (newTab) newTab.location.href = result.application_url;
      else window.open(result.application_url, "_blank", "noopener,noreferrer");
    } else if (newTab) {
      newTab.close();
    }
    return result;
  } catch (error) {
    if (newTab) newTab.close();
    throw error;
  }
}
