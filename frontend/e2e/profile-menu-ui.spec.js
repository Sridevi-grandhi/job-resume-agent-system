import { expect, test } from "@playwright/test";

const user = { user_id: 7, name: "Test Candidate", email: "rudratanishka1@gmail.com" };

test("profile actions stay inside the shared sidebar at requested sizes and zoom levels", async ({ page }) => {
  const consoleErrors = [];
  page.on("pageerror", (error) => consoleErrors.push(error.message));
  page.on("console", (message) => {
    if (message.type() === "error") consoleErrors.push(message.text());
  });
  await page.route("https://fonts.googleapis.com/**", async (route) => {
    await route.fulfill({ status: 200, contentType: "text/css", body: "" });
  });
  await page.route("http://localhost:8000/**", async (route) => {
    await route.fulfill({ status: 200, contentType: "application/json", body: "{}" });
  });
  await page.route("http://localhost:8000/api/email/events/**", async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({ events: [{ id: 101, resolution: "NEEDS_CONFIRMATION", subject: "Interview Invitation" }] }),
    });
  });
  await page.addInitScript((currentUser) => {
    localStorage.setItem("currentUser", JSON.stringify(currentUser));
  }, user);

  for (const size of [{ width: 1366, height: 768 }, { width: 1920, height: 1080 }]) {
    for (const zoom of [1, 1.25, 1.5]) {
      const label = `${size.width}x${size.height} at ${zoom * 100}%`;
      await page.setViewportSize(size);
      await page.goto("/dashboard");
      await page.evaluate((factor) => { document.documentElement.style.zoom = String(factor); }, zoom);
      const sidebar = page.locator(".sidebar");
      const profile = sidebar.locator(".user-menu");
      await expect(profile).toBeVisible();
      await expect(sidebar.getByText("Career momentum")).toBeVisible();
      await profile.click();
      const menu = sidebar.locator(".sidebar-profile-actions");
      await expect(menu, label).toBeVisible();
      await expect(menu.locator(".sidebar-profile-item")).toHaveCount(3);
      await expect(sidebar.locator(".sidebar-notification-badge")).toHaveText("1");
      await expect(sidebar.getByText(user.email)).toBeVisible();
      await expect(sidebar.getByText("Career momentum")).toBeHidden();
      if (size.width === 1366 && zoom === 1) {
        await expect(sidebar.locator("nav a")).toHaveCount(9);
        await expect(sidebar.locator("nav a").last()).toBeInViewport();
      }
      await expect(sidebar.getByRole("button", { name: "Logout" })).toBeVisible();
      await expect(page.locator(".topbar .notification, .topbar .top-avatar")).toHaveCount(0);
      const geometry = await page.evaluate(() => {
        const sidebarRect = document.querySelector(".sidebar").getBoundingClientRect();
        const menuRect = document.querySelector(".sidebar-profile-actions").getBoundingClientRect();
        const profileRect = document.querySelector(".user-menu").getBoundingClientRect();
        const logoutRect = document.querySelector(".sidebar-logout").getBoundingClientRect();
        const momentumRect = document.querySelector(".upgrade").getBoundingClientRect();
        const nav = document.querySelector(".sidebar nav");
        const email = document.querySelector(".user-menu small");
        const emailStyle = getComputedStyle(email);
        const menuStyle = getComputedStyle(document.querySelector(".sidebar-profile-actions"));
        const navCanScroll = nav.scrollHeight > nav.clientHeight + 2;
        if (navCanScroll) nav.scrollTop = 40;
        return {
          sidebar: { left: sidebarRect.left, right: sidebarRect.right, top: sidebarRect.top, bottom: sidebarRect.bottom },
          menu: { left: menuRect.left, right: menuRect.right, top: menuRect.top, bottom: menuRect.bottom },
          profile: { top: profileRect.top, bottom: profileRect.bottom },
          logout: { top: logoutRect.top, bottom: logoutRect.bottom },
          momentum: { top: momentumRect.top, bottom: momentumRect.bottom },
          viewport: { width: innerWidth, height: innerHeight },
          horizontalOverflow: document.documentElement.scrollWidth > document.documentElement.clientWidth + 2,
          sidebarHorizontalOverflow: document.querySelector(".sidebar").scrollWidth > document.querySelector(".sidebar").clientWidth + 2,
          logoutColor: getComputedStyle(document.querySelector(".sidebar-logout")).color,
          iconColor: getComputedStyle(document.querySelector(".sidebar-logout svg")).color,
          emailText: email.textContent,
          emailWhiteSpace: emailStyle.whiteSpace,
          emailOverflow: emailStyle.overflowX,
          emailTextOverflow: emailStyle.textOverflow,
          emailMinWidth: emailStyle.minWidth,
          emailOneLine: email.clientHeight <= parseFloat(emailStyle.lineHeight) + 1,
          emailEllipsized: email.scrollWidth > email.clientWidth + 2,
          navScrollbar: getComputedStyle(nav).scrollbarWidth,
          navWebkitScrollbar: getComputedStyle(nav, "::-webkit-scrollbar").display,
          navCanScroll,
          navScrollWorks: !navCanScroll || nav.scrollTop > 0,
          menuScrollbar: menuStyle.scrollbarWidth,
          menuShadow: menuStyle.boxShadow,
          menuBorder: menuStyle.borderTopWidth,
        };
      });
      expect(geometry.menu.left, label).toBeGreaterThanOrEqual(geometry.sidebar.left - 1);
      expect(geometry.menu.right, label).toBeLessThanOrEqual(geometry.sidebar.right + 1);
      expect(geometry.menu.bottom, label).toBeLessThanOrEqual(geometry.profile.top + 1);
      expect(geometry.logout.bottom, label).toBeLessThanOrEqual(geometry.sidebar.bottom + 1);
      expect(geometry.momentum.bottom, label).toBeLessThanOrEqual(geometry.menu.top + 1);
      expect(geometry.profile.bottom, label).toBeLessThanOrEqual(geometry.viewport.height + 1);
      expect(geometry.horizontalOverflow, label).toBe(false);
      expect(geometry.sidebarHorizontalOverflow, label).toBe(false);
      expect(geometry.logoutColor, label).toBe("rgb(255, 255, 255)");
      expect(geometry.iconColor, label).toBe("rgb(255, 255, 255)");
      expect(geometry.emailText, label).toBe(user.email);
      expect(geometry.emailWhiteSpace, label).toBe("nowrap");
      expect(geometry.emailOverflow, label).toBe("hidden");
      expect(geometry.emailTextOverflow, label).toBe("ellipsis");
      expect(geometry.emailMinWidth, label).toBe("0px");
      expect(geometry.emailOneLine, label).toBe(true);
      expect(geometry.emailEllipsized, label).toBe(true);
      expect(geometry.navScrollbar, label).toBe("none");
      expect(geometry.navWebkitScrollbar, label).toBe("none");
      expect(geometry.navScrollWorks, label).toBe(true);
      expect(geometry.menuScrollbar, label).toBe("none");
      expect(geometry.menuShadow, label).not.toBe("none");
      expect(parseFloat(geometry.menuBorder), label).toBeGreaterThan(0);
      expect(parseFloat(geometry.menuBorder), label).toBeLessThanOrEqual(1);

      await sidebar.getByRole("button", { name: "Notifications" }).click();
      await expect(sidebar.locator(".sidebar-notification-dropdown")).toBeVisible();
      const panel = await sidebar.locator(".sidebar-notification-dropdown").boundingBox();
      const bounds = await sidebar.boundingBox();
      expect(panel.x, label).toBeGreaterThanOrEqual(bounds.x - 1);
      expect(panel.x + panel.width, label).toBeLessThanOrEqual(bounds.x + bounds.width + 1);
      await expect(sidebar.getByRole("button", { name: "Logout" })).toBeInViewport();
    }
  }
  await page.setViewportSize({ width: 1366, height: 540 });
  await page.goto("/dashboard");
  await page.locator(".user-menu").click();
  const compactNav = await page.locator(".sidebar nav").evaluate((nav) => {
    const canScroll = nav.scrollHeight > nav.clientHeight + 2;
    nav.scrollTop = 80;
    return { canScroll, scrollTop: nav.scrollTop };
  });
  expect(compactNav.canScroll).toBe(true);
  expect(compactNav.scrollTop).toBeGreaterThan(0);
  await expect(page.locator(".sidebar-profile-actions")).toBeVisible();
  await expect(page.locator(".user-menu")).toBeInViewport();

  await page.goto("/dashboard");
  await page.locator(".user-menu").click();
  await page.getByRole("link", { name: "Settings" }).click();
  await expect(page).toHaveURL(/\/preferences$/);
  await expect(page.locator(".sidebar-profile-actions")).toHaveCount(0);

  for (const path of ["/resume", "/jobs", "/updated-jobs", "/selected-jobs", "/ats-analyzer", "/emails", "/workflow"]) {
    await page.goto(path);
    await expect(page.locator(".sidebar .user-menu")).toBeVisible();
    await page.locator(".sidebar .user-menu").click();
    await expect(page.locator(".sidebar-profile-actions .sidebar-profile-item")).toHaveCount(3);
  }
  await page.getByRole("button", { name: "Logout" }).click();
  await expect(page).toHaveURL(/\/login$/);
  expect(consoleErrors).toEqual([]);
});
