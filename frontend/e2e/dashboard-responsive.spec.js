import { expect, test } from "@playwright/test";

const scores = [92, 84, 72, 65, 55, 42];
const jobs = scores.map((score, index) => ({
  id: index + 1,
  title: index === 0
    ? "Senior Principal Platform Engineer for Distributed Cloud Infrastructure and Developer Experience"
    : `Platform Engineer ${index + 1}`,
  company: `Company ${index + 1}`,
  location: "Remote",
  source: "User Selected Job",
  description: "A detailed platform engineering role with cloud infrastructure and developer tooling responsibilities.",
  job_url: `https://jobs.example.test/${index + 1}`,
  match_percentage: score,
}));
const matches = scores.map((score, index) => ({
  id: index + 101,
  job_id: index + 1,
  match_percentage: score,
  resume_version: "original",
}));

test("Dashboard reflows without horizontal overflow and preserves score visuals", async ({ page }) => {
  const consoleErrors = [];
  page.on("pageerror", (error) => consoleErrors.push(error.message));
  page.on("console", (message) => {
    if (message.type() === "error") consoleErrors.push(message.text());
  });
  await page.route("https://fonts.googleapis.com/**", (route) => route.fulfill({ status: 200, contentType: "text/css", body: "" }));
  await page.route("http://localhost:8000/**", (route) => route.fulfill({ status: 200, contentType: "application/json", body: "{}" }));
  await page.route("http://localhost:8000/api/jobs/7", (route) => route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ jobs }) }));
  await page.route("http://localhost:8000/api/match/7", (route) => route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ matches }) }));
  await page.addInitScript(() => {
    localStorage.setItem("currentUser", JSON.stringify({ user_id: 7, name: "Test Candidate", email: "candidate@example.test" }));
  });

  const cases = [
    { width: 1920, height: 1080, zoom: 1 },
    { width: 1366, height: 768, zoom: 1 },
    { width: 1366, height: 768, zoom: 1.25 },
    { width: 1366, height: 768, zoom: 1.5 },
    { width: 1024, height: 768, zoom: 1 },
    { width: 911, height: 512, zoom: 1 }, // effective CSS viewport at 1366x768 / 150%
  ];

  for (const { width, height, zoom } of cases) {
    const label = `${width}x${height} at ${zoom * 100}%`;
    await page.setViewportSize({ width, height });
    await page.goto("/dashboard");
    await page.evaluate((factor) => { document.documentElement.style.zoom = String(factor); }, zoom);
    await expect(page.locator(".top-job-matches-panel .match-score-ring")).toHaveCount(6);
    await expect(page.locator(".score-ring")).toHaveAttribute("aria-label", "Match score distribution: High 2, Good 2, Low 2");
    await expect(page.locator(".metric", { hasText: "Avg. match score" })).toContainText("68%");
    await expect(page.locator(".score-ring b")).toHaveText("68%");
    await expect(page.locator(".score-ring small")).toHaveText("Average");
    await expect(page.locator(".legend p")).toHaveCount(3);
    await expect(page.locator(".top-job-matches-panel .text-button")).toHaveText("View all");
    await expect(page.locator(".workflow-progress a")).toHaveCount(7);

    const measured = await page.evaluate(() => {
      const rect = (selector) => document.querySelector(selector).getBoundingClientRect();
      const overview = document.querySelector(".score-overview");
      const legend = document.querySelector(".legend");
      const cards = document.querySelector(".top-job-matches-panel .stack");
      const chart = document.querySelector(".score-ring");
      const overviewRect = rect(".match-overview-panel");
      const legendRect = legend.getBoundingClientRect();
      const cardsRect = cards.getBoundingClientRect();
      const firstTitle = document.querySelector(".top-job-matches-panel .job-card h3");
      return {
        pageOverflow: document.documentElement.scrollWidth > document.documentElement.clientWidth + 2,
        workflowOverflow: document.querySelector(".workflow-progress").scrollWidth > document.querySelector(".workflow-progress").clientWidth + 2,
        metricsOverflow: document.querySelector(".metrics").scrollWidth > document.querySelector(".metrics").clientWidth + 2,
        lowerOverflow: document.querySelector(".dash-grid").scrollWidth > document.querySelector(".dash-grid").clientWidth + 2,
        chartOverflow: overview.scrollWidth > overview.clientWidth + 2,
        legendInsidePanel: legendRect.right <= overviewRect.right - 1 && legendRect.left >= overviewRect.left + 1,
        jobCardsInsidePanel: cardsRect.right <= rect(".top-job-matches-panel").right - 1,
        jobTitleWraps: firstTitle.getBoundingClientRect().height > parseFloat(getComputedStyle(firstTitle).lineHeight) + 1,
        jobListScrollable: getComputedStyle(cards).overflowY === "auto" && cards.scrollHeight > cards.clientHeight,
        chartColors: getComputedStyle(chart).backgroundImage,
        metricColumns: getComputedStyle(document.querySelector(".metrics")).gridTemplateColumns.split(" ").length,
        lowerColumns: getComputedStyle(document.querySelector(".dash-grid")).gridTemplateColumns.split(" ").length,
      };
    });

    expect(measured.pageOverflow, label).toBe(false);
    expect(measured.workflowOverflow, label).toBe(false);
    expect(measured.metricsOverflow, label).toBe(false);
    expect(measured.lowerOverflow, label).toBe(false);
    expect(measured.chartOverflow, label).toBe(false);
    expect(measured.legendInsidePanel, label).toBe(true);
    expect(measured.jobCardsInsidePanel, label).toBe(true);
    expect(measured.jobTitleWraps, label).toBe(true);
    expect(measured.jobListScrollable, label).toBe(true);
    expect(measured.chartColors, label).toContain("conic-gradient");
    expect(measured.chartColors, label).toContain("rgb(16, 185, 129)");
    expect(measured.chartColors, label).toContain("rgb(23, 104, 232)");
    expect(measured.chartColors, label).toContain("rgb(245, 158, 11)");
    if (width === 1366 && zoom === 1.5) {
      expect(measured.metricColumns).toBe(2);
      expect(measured.lowerColumns).toBe(1);
    }
  }

  await page.setViewportSize({ width: 1366, height: 768 });
  await page.goto("/dashboard");
  await page.evaluate(() => { document.documentElement.style.zoom = "1.5"; });
  await page.locator(".user-menu").click();
  await expect(page.locator(".sidebar-profile-actions")).toBeVisible();
  await expect(page.getByRole("button", { name: "Logout" })).toHaveCSS("color", "rgb(255, 255, 255)");
  await expect(page.getByRole("button", { name: "Notifications" })).toBeVisible();
  await expect(page.getByRole("link", { name: "Settings" })).toBeVisible();
  expect(await page.evaluate(() => document.documentElement.scrollWidth > document.documentElement.clientWidth + 2)).toBe(false);
  expect(consoleErrors).toEqual([]);
});
