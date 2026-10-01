import { expect, test } from "@playwright/test";

test("Settings stays usable across desktop sizes and zoom levels", async ({ page }) => {
  const errors = [];
  page.on("pageerror", (error) => errors.push(error.message));
  page.on("console", (message) => {
    if (message.type() === "error") errors.push(message.text());
  });
  await page.route("https://fonts.googleapis.com/**", (route) => route.fulfill({ status: 200, contentType: "text/css", body: "" }));
  await page.route("https://fonts.gstatic.com/**", (route) => route.fulfill({ status: 200, body: "" }));

  let preferences = {
    automatic_gmail_scan_enabled: false, gmail_scan_frequency: "daily",
    email_types_to_scan: ["job_opportunities", "recruiter_messages", "application_updates", "interview_invitations"],
    job_alerts_enabled: true, application_updates_enabled: true, interview_alerts_enabled: true,
  };
  let events = [];
  let gmailConnected = true;
  let scanRequests = 0;
  let profileName = "Tanishka";
  let profileSaves = 0;
  await page.route(/^http:\/\/localhost:800[01]\//, async (route) => {
    const url = route.request().url();
    if (url.includes("/api/auth/profile/") && route.request().method() === "PATCH") {
      const payload = route.request().postDataJSON();
      expect(payload.current_password).toBe("test-password");
      profileName = payload.name;
      profileSaves += 1;
    }
    if (url.includes("/api/email/preferences/") && route.request().method() === "PATCH") {
      preferences = { ...preferences, ...route.request().postDataJSON() };
    }
    if (url.includes("/api/email/gmail/scan/") && route.request().method() === "POST") {
      scanRequests += 1;
    }
    if (url.includes("/api/email/gmail/disconnect/") && route.request().method() === "POST") {
      gmailConnected = false;
    }
    const body = url.includes("/api/auth/profile/")
      ? { user_id: 7, name: profileName, email: "tanishka@example.test" }
      : url.includes("/api/email/preferences/")
      ? preferences
      : url.includes("/api/email/events/")
        ? { events }
      : url.includes("/api/email/gmail/status/")
        ? { connected: gmailConnected, status: gmailConnected ? "CONNECTED" : "DISCONNECTED", email_address: gmailConnected ? "candidate@gmail.com" : null, last_scanned_at: gmailConnected ? "2026-09-28T19:56:17Z" : null }
        : url.includes("/api/email/gmail/scan/")
          ? { messages_checked: 2, outcomes: {} }
          : { status: "healthy" };
    await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(body) });
  });
  await page.addInitScript(() => {
    if (!localStorage.getItem("currentUser")) {
      localStorage.setItem("currentUser", JSON.stringify({ user_id: 7, name: "Tanishka", email: "tanishka@example.test" }));
    }
  });

  await page.goto("/settings");
  await expect(page.getByRole("heading", { name: "Settings" })).toBeVisible();
  await expect(page.getByRole("checkbox", { name: /automatic gmail scan/i })).toBeEnabled();
  await page.getByRole("button", { name: "Edit Profile" }).click();
  await page.getByRole("textbox", { name: "Name" }).fill("Tanishka Test");
  await page.getByLabel("Current password").fill("test-password");
  await page.getByRole("button", { name: "Save Profile" }).click();
  await expect(page.getByText("Profile saved.")).toBeVisible();
  await expect(page.locator(".settings-details").first()).toContainText("Tanishka Test");
  await expect(page.locator(".sidebar-profile .user-menu b")).toHaveText("Tanishka Test");
  await page.getByRole("link", { name: "Dashboard" }).click();
  await expect(page.getByRole("heading", { name: /Welcome back, Tanishka Test/ })).toBeVisible();
  await page.reload();
  await expect(page.getByRole("heading", { name: /Welcome back, Tanishka Test/ })).toBeVisible();
  await page.goto("/settings");
  await expect(page.locator(".settings-details").first()).toContainText("Tanishka Test");
  await page.getByRole("button", { name: "Edit Profile" }).click();
  await page.getByRole("textbox", { name: "Name" }).fill("Tanishka");
  await page.getByLabel("Current password").fill("test-password");
  await page.getByRole("button", { name: "Save Profile" }).click();
  await expect(page.locator(".settings-details").first()).toContainText("Tanishka");
  await expect(page.locator(".sidebar-profile .user-menu b")).toHaveText("Tanishka");
  expect(profileSaves).toBe(2);

  await expect(page.getByRole("button", { name: "Save Email Scan Settings" })).toBeDisabled();
  await page.getByLabel("Scan interval").selectOption("every_1_hour");
  await expect(page.getByRole("button", { name: "Save Email Scan Settings" })).toBeEnabled();
  await page.getByLabel("Scan interval").selectOption("daily");
  await expect(page.getByRole("button", { name: "Save Email Scan Settings" })).toBeDisabled();
  await page.getByLabel("Scan interval").selectOption("every_1_hour");
  await page.getByRole("button", { name: "Save Email Scan Settings" }).click();
  await expect(page.getByText("Email scan settings saved.")).toBeVisible();
  await expect(page.getByRole("button", { name: "Save Email Scan Settings" })).toBeDisabled();
  await page.reload();
  await expect(page.getByLabel("Scan interval")).toHaveValue("every_1_hour");
  expect(preferences.gmail_scan_frequency).toBe("every_1_hour");

  await expect(page.getByRole("checkbox", { name: "Recruiter Messages" })).toBeChecked();
  await page.getByRole("checkbox", { name: "Recruiter Messages" }).uncheck();
  await page.getByRole("button", { name: "Save Email Scan Settings" }).click();
  await expect(page.getByText("Email scan settings saved.")).toBeVisible();
  await page.reload();
  await expect(page.getByRole("checkbox", { name: "Recruiter Messages" })).not.toBeChecked();
  await expect(page.getByRole("checkbox", { name: "Interview Invitations" })).toBeChecked();
  await page.getByRole("checkbox", { name: "Recruiter Messages" }).check();
  await page.getByRole("button", { name: "Save Email Scan Settings" }).click();
  await expect(page.getByText("Email scan settings saved.")).toBeVisible();
  await page.reload();
  await expect(page.getByRole("checkbox", { name: "Recruiter Messages" })).toBeChecked();
  await page.getByLabel("Scan interval").selectOption("daily");
  await page.getByRole("button", { name: "Save Email Scan Settings" }).click();
  await expect(page.getByText("Email scan settings saved.")).toBeVisible();

  await expect(page.getByRole("button", { name: "Save Notification Settings" })).toBeDisabled();
  await page.getByRole("checkbox", { name: "Job alerts" }).uncheck();
  await expect(page.getByRole("button", { name: "Save Notification Settings" })).toBeEnabled();
  await page.getByRole("button", { name: "Save Notification Settings" }).click();
  await expect(page.getByText("Notification settings saved.")).toBeVisible();
  await page.reload();
  await expect(page.getByRole("checkbox", { name: "Job alerts" })).not.toBeChecked();
  await page.getByRole("checkbox", { name: "Job alerts" }).check();
  await page.getByRole("button", { name: "Save Notification Settings" }).click();
  await expect(page.getByText("Notification settings saved.")).toBeVisible();

  events = [{ id: 901, subject: "Interview invitation test", event_type: "INTERVIEW_INVITE", resolution: "NEEDS_CONFIRMATION", received_at: new Date().toISOString() }];
  await page.reload();
  await page.getByRole("checkbox", { name: "Interview alerts" }).uncheck();
  await page.getByRole("button", { name: "Save Notification Settings" }).click();
  await expect(page.getByText("Notification settings saved.")).toBeVisible();
  await page.reload();
  await expect(page.getByRole("checkbox", { name: "Interview Invitations" })).toBeChecked();
  await expect(page.getByRole("checkbox", { name: "Interview alerts" })).not.toBeChecked();
  await page.getByRole("button", { name: "View Notifications" }).click();
  await expect(page.getByText("You're all caught up.")).toBeVisible();
  await page.getByRole("button", { name: "View Notifications" }).click();
  await page.getByRole("checkbox", { name: "Interview alerts" }).check();
  await page.getByRole("button", { name: "Save Notification Settings" }).click();
  await expect(page.getByText("Notification settings saved.")).toBeVisible();
  await page.reload();
  await page.getByRole("button", { name: "View Notifications" }).click();
  await expect(page.getByText("Interview invitation test")).toBeVisible();
  await page.getByRole("button", { name: "View Notifications" }).click();

  await page.getByRole("checkbox", { name: /automatic gmail scan/i }).check();
  await expect(page.getByRole("checkbox", { name: /automatic gmail scan/i })).toBeChecked();
  await page.reload();
  await expect(page.getByRole("checkbox", { name: /automatic gmail scan/i })).toBeChecked();
  await page.getByRole("button", { name: "Scan Now" }).click();
  await expect(page.getByText("Scan complete — checked 2 message(s).")).toBeVisible();
  expect(scanRequests).toBe(1);

  for (const size of [{ width: 1366, height: 768 }, { width: 1920, height: 1080 }]) {
    for (const zoom of [1, 1.25, 1.5]) {
      await page.setViewportSize(size);
      await page.evaluate((factor) => { document.documentElement.style.zoom = String(factor); }, zoom);
      await expect(page.locator(".settings-sections > .panel")).toHaveCount(4);
      const layout = await page.evaluate(() => ({
        viewport: document.documentElement.clientWidth,
        content: document.documentElement.scrollWidth,
        cardOverflow: [...document.querySelectorAll(".settings-sections > .panel")]
          .some((card) => card.scrollWidth > card.clientWidth + 2),
      }));
      expect(layout.content, `${size.width}x${size.height} at ${zoom * 100}%`).toBeLessThanOrEqual(layout.viewport + 2);
      expect(layout.cardOverflow, `${size.width}x${size.height} at ${zoom * 100}%`).toBe(false);
    }
  }
  await page.setViewportSize({ width: 390, height: 844 });
  await page.evaluate(() => { document.documentElement.style.zoom = "1"; });
  const mobileLayout = await page.evaluate(() => ({
    viewport: document.documentElement.clientWidth,
    content: document.documentElement.scrollWidth,
    emailGridColumns: getComputedStyle(document.querySelector(".settings-email-types-grid")).gridTemplateColumns.split(" ").length,
  }));
  expect(mobileLayout.content).toBeLessThanOrEqual(mobileLayout.viewport + 2);
  expect(mobileLayout.emailGridColumns).toBe(1);
  await page.getByRole("button", { name: "Disconnect Gmail" }).click();
  await expect(page.getByRole("button", { name: "Connect Gmail" })).toBeVisible();
  await expect(page.getByRole("checkbox", { name: /automatic gmail scan/i })).toHaveCount(0);
  await page.getByRole("link", { name: "Change Password" }).click();
  await expect(page.getByRole("heading", { name: "Forgot your password?" })).toBeVisible();
  expect(errors).toEqual([]);
});
