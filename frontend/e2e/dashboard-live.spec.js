import { expect, test } from "@playwright/test";

const LIVE = Boolean(process.env.LIVE_DASHBOARD);
const user = { user_id: 3, email: "rudratanishka1@gmail.com", name: "Tanishka" };

test.describe("live Dashboard polish", () => {
  test.skip(!LIVE, "Set LIVE_DASHBOARD=1 to run against the real backend.");

  test("header/sidebar names agree and canonical KPIs hydrate without recalculation", async ({ page }) => {
    const posts = [];
    page.on("request", (req) => {
      if (req.method() === "POST") posts.push(req.url());
    });
    await page.addInitScript((currentUser) => {
      localStorage.setItem("currentUser", JSON.stringify(currentUser));
    }, user);
    await page.goto("/dashboard");
    await expect(page.getByRole("heading", { name: /welcome back, tanishka/i })).toBeVisible();
    await expect(page.locator(".user-menu b")).toHaveText("Tanishka");
    await expect(page.locator(".user-menu small")).toHaveText(user.email);
    await expect(page.getByText("Career seeker")).toHaveCount(0);
    await expect(page.getByText("Total jobs").locator("..")).toContainText("43");
    await expect(page.getByText("Avg. match score").locator("..")).toContainText("72%");
    await expect(page.getByText("33 of 43 jobs scored").first()).toBeVisible();
    await expect(page.locator(".metric", { hasText: "Best match" })).toContainText("100%");
    await expect(page.locator(".metric", { hasText: "Best match" })).toContainText("Java Fullstack Lead");
    await expect(page.locator(".best-match")).toContainText("Java Fullstack Lead");
    await expect(page.locator(".best-match")).toContainText("100%");
    await expect(page.getByRole("link", { name: /view all/i })).toHaveAttribute("href", "/jobs");
    const calculate = posts.filter((url) => /\/api\/match\/calculate\//.test(url) || /\/api\/jobs\/match/.test(url));
    expect(calculate).toEqual([]);
  });

  test("dashboard remains usable at 1366×768, 1920×1080, and 150% zoom", async ({ page }) => {
    await page.addInitScript((currentUser) => {
      localStorage.setItem("currentUser", JSON.stringify(currentUser));
    }, user);
    for (const size of [{ width: 1366, height: 768 }, { width: 1920, height: 1080 }]) {
      await page.setViewportSize(size);
      await page.goto("/dashboard");
      await expect(page.getByRole("heading", { name: /welcome back, tanishka/i })).toBeVisible();
      await expect(page.locator(".top-job-matches-panel")).toBeVisible();
      await expect(page.locator(".score-ring")).toBeVisible();
      const overflowX = await page.evaluate(() => document.documentElement.scrollWidth > document.documentElement.clientWidth + 2);
      expect(overflowX, `${size.width}x${size.height} horizontal overflow`).toBe(false);
    }
    await page.setViewportSize({ width: 1366, height: 768 });
    await page.evaluate(() => { document.documentElement.style.zoom = "1.5"; });
    await expect(page.getByRole("heading", { name: /welcome back, tanishka/i })).toBeVisible();
    await expect(page.locator(".workflow-progress")).toBeVisible();
  });
});
