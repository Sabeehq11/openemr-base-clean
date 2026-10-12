import { test as base, expect, Page } from '@playwright/test';

export const ADMIN_USER = process.env.OE_USER ?? 'admin';
export const ADMIN_PASS = process.env.OE_PASS ?? 'pass';

export async function login(page: Page, user = ADMIN_USER, pass = ADMIN_PASS): Promise<void> {
  await page.goto('/interface/login/login.php?site=default');
  await page.locator('#authUser').fill(user);
  await page.locator('#clearPass').fill(pass);
  await page.locator('#login-button, button[type=submit]').first().click();
  await page.waitForURL(/interface\/main\/tabs\/main\.php/);
}

type Fixtures = { loggedIn: Page };

// `loggedIn` yields a page that is already authenticated as the admin user.
export const test = base.extend<Fixtures>({
  loggedIn: async ({ page }, use) => {
    await login(page);
    await use(page);
  },
});

export { expect };
