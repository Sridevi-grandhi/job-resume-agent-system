import { expect, test } from "@playwright/test";

const modules = [
  ["dashboard", "/dashboard"],
  ["preferences", "/preferences"],
  ["resume", "/resume"],
  ["job-search", "/jobs"],
  ["updated-job-search", "/updated-jobs"],
  ["selected-jobs", "/selected-jobs"],
  ["ats", "/ats-analyzer"],
  ["email-summary", "/emails"],
  ["workflow", "/workflow"],
];

test("application pages use shared typography without page overflow", async ({ page }) => {
  await page.route("https://fonts.googleapis.com/**", (route) => route.fulfill({ status: 200, contentType: "text/css", body: "" }));
  await page.route("**/api/**", (route) => route.fulfill({ status: 200, contentType: "application/json", body: "{}" }));
  await page.addInitScript(() => {
    localStorage.setItem("currentUser", JSON.stringify({ user_id: 7, name: "Test Candidate", email: "candidate@example.test" }));
  });

  for (const { width, height, zoom } of [
    { width: 1920, height: 1080, zoom: 1 },
    { width: 1366, height: 768, zoom: 1 },
    { width: 1366, height: 768, zoom: 1.25 },
    { width: 1366, height: 768, zoom: 1.5 },
  ]) {
    await page.setViewportSize({ width, height });
    for (const [name, path] of modules) {
      await page.goto(path);
      await expect(page.locator(".page-heading h2, .dashboard .panel>header h3").first()).toBeVisible();
      await page.evaluate((factor) => { document.documentElement.style.zoom = String(factor); }, zoom);
      if (name === "dashboard") {
        await expect(page.locator(".topbar h1")).toContainText("Welcome back, Test Candidate");
        await expect(page.locator(".topbar p")).toHaveText("Track jobs, optimize resumes and boost your career with AI.");
      } else {
        await expect(page.locator(".topbar")).toHaveCount(0);
        await expect(page.locator(".page-heading h2")).toBeVisible();
        await expect(page.locator(".page-heading p")).toBeVisible();
        const titleTop = await page.locator(".page-heading h2").evaluate((title) => title.getBoundingClientRect().top);
        expect(titleTop, `${name} heading starts near top at ${zoom * 100}%`).toBeLessThan(100);
      }
      const sizes = await page.evaluate(() => {
        const title = document.querySelector(".page-heading h2");
        const panel = document.querySelector(".panel>header h3");
        return {
          title: title ? parseFloat(getComputedStyle(title).fontSize) : null,
          panel: panel ? parseFloat(getComputedStyle(panel).fontSize) : null,
          overflow: document.documentElement.scrollWidth > document.documentElement.clientWidth + 2,
        };
      });
      expect(sizes.overflow, `${name} at ${width}x${height}, ${zoom * 100}%`).toBe(false);
      if (sizes.title) expect(sizes.title).toBeGreaterThanOrEqual(24);
      if (sizes.panel) expect(sizes.panel, `${name} section title`).toBeGreaterThanOrEqual(18);
      if (name === "selected-jobs") {
        const panelFits = await page.locator(".selected-import-panel").evaluate((panel) => panel.scrollHeight <= panel.clientHeight + 2);
        expect(panelFits, `selected job form at ${width}x${height}, ${zoom * 100}%`).toBe(true);
      }
      if (name === "email-summary") {
        const headerFits = await page.locator(".email-summary-header-grid").evaluate((grid) => grid.scrollWidth <= grid.clientWidth + 2);
        expect(headerFits, `email header at ${width}x${height}, ${zoom * 100}%`).toBe(true);
      }
      if (zoom === 1.5 && width === 1366) {
        await page.screenshot({ path: test.info().outputPath(`${name}-150.png`), fullPage: true });
      }
    }
  }
});

test("secondary modules keep mobile navigation without the welcome block", async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await page.route("**/api/**", (route) => route.fulfill({ status: 200, contentType: "application/json", body: "{}" }));
  await page.addInitScript(() => {
    localStorage.setItem("currentUser", JSON.stringify({ user_id: 7, name: "Test Candidate", email: "candidate@example.test" }));
  });
  await page.goto("/preferences");
  await expect(page.locator(".topbar")).toHaveCount(0);
  await expect(page.locator(".page-heading h2")).toHaveText("Preferences");
  await page.getByRole("button", { name: "Open navigation" }).click();
  await expect(page.locator(".sidebar.open")).toBeVisible();
});

test("authentication forms keep labels and inputs readable", async ({ page }) => {
  await page.route("https://fonts.googleapis.com/**", (route) => route.fulfill({ status: 200, contentType: "text/css", body: "" }));
  await page.route("**/api/**", (route) => route.fulfill({ status: 200, contentType: "application/json", body: "{}" }));
  for (const path of ["/login", "/signup", "/forgot-password", "/reset-password?token=test"]) {
    await page.goto(path);
    await expect(page.locator(".auth-card h2")).toBeVisible();
    const sizes = await page.evaluate(() => ({
      label: parseFloat(getComputedStyle(document.querySelector(".auth-card label")).fontSize),
      input: parseFloat(getComputedStyle(document.querySelector(".auth-card input")).fontSize),
      overflow: document.documentElement.scrollWidth > document.documentElement.clientWidth + 2,
    }));
    expect(sizes.label).toBeGreaterThanOrEqual(14);
    expect(sizes.input).toBeGreaterThanOrEqual(15);
    expect(sizes.overflow).toBe(false);
  }
});
