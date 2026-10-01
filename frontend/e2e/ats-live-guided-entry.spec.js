import { expect, test } from "@playwright/test";

const LIVE = Boolean(process.env.LIVE_ATS_ENTRY);
const user = { user_id: 3, email: "rudratanishka1@gmail.com", name: "Tanishka" };
const optimizedJobId = "1758";
const freshJobId = "1738";

test.describe("live Selected Jobs guided ATS entry", () => {
  test.skip(!LIVE, "Set LIVE_ATS_ENTRY=1 to run against the real backend.");

  async function injectUser(page) {
    await page.addInitScript((currentUser) => {
      localStorage.setItem("currentUser", JSON.stringify(currentUser));
    }, user);
  }

  function trackGenerate(page) {
    const posts = [];
    page.on("request", (req) => {
      const url = req.url();
      if (req.method() !== "POST") return;
      if (/\/api\/ats-optimizer\/generate\//.test(url) || /\/api\/ats\/analyze-optimize\//.test(url)) {
        posts.push(url);
      }
    });
    return posts;
  }

  async function optimizeJob(page, jobId, titleSnippet) {
    await page.goto("/selected-jobs");
    await expect(page.getByRole("heading", { name: /saved selected jobs/i })).toBeVisible({ timeout: 20_000 });
    await expect(page.locator(".job-card").first()).toBeVisible({ timeout: 20_000 });
    const card = page.locator(".job-card", { hasText: titleSnippet }).first();
    await expect(card).toBeVisible();
    const posts = trackGenerate(page);
    await card.getByRole("button", { name: /optimize and compare scores/i }).click();
    await expect(page).toHaveURL(new RegExp(`/optimizer/${jobId}`));
    await expect(page.getByRole("heading", { name: /step 1/i })).toBeVisible({ timeout: 15_000 });
    await expect(page.getByLabel(/select job/i)).toHaveValue(jobId);
    await expect(page.getByRole("heading", { name: /step 5/i })).toHaveCount(0);
    await expect(page.getByRole("button", { name: /generate optimized resume/i })).toHaveCount(0);
    return posts;
  }

  test("A. previously optimized Genesis10 job opens Step 1 without generating", async ({ page }) => {
    await injectUser(page);
    const posts = await optimizeJob(page, optimizedJobId, "Genesis10");
    expect(posts, "generate POST count").toEqual([]);
    await page.getByRole("button", { name: /^continue$/i }).click();
    await expect(page.getByRole("heading", { name: /step 2/i })).toBeVisible();
    expect(posts, "generate POST count after Continue").toEqual([]);
  });

  test("B. saved job without completed optimization opens Step 1 without generating", async ({ page }) => {
    await injectUser(page);
    const posts = await optimizeJob(page, freshJobId, "Career Soft");
    expect(posts, "generate POST count").toEqual([]);
  });

  test("sidebar still restores Step 5 after a completed workflow", async ({ page }) => {
    await injectUser(page);
    await page.addInitScript((jobId) => {
      sessionStorage.setItem("atsSelectedJobId", jobId);
      sessionStorage.setItem("atsWizardStep:" + jobId, "5");
    }, optimizedJobId);
    await page.goto("/ats-analyzer");
    await expect(page.getByRole("heading", { name: /step 5/i })).toBeVisible({ timeout: 20_000 });
  });
});
