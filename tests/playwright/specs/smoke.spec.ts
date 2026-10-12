import { test, expect, login } from '../fixtures';

test.describe('OpenEMR smoke', () => {
  test('health endpoint reports ready', async ({ request }) => {
    const res = await request.get('/meta/health/readyz');
    expect(res.status()).toBe(200);
  });

  test('login page renders', async ({ page }) => {
    await page.goto('/interface/login/login.php?site=default');
    await expect(page.locator('#authUser')).toBeVisible();
  });

  test('invalid credentials are rejected', async ({ page }) => {
    await page.goto('/interface/login/login.php?site=default');
    await page.locator('#authUser').fill('admin');
    await page.locator('#clearPass').fill('definitely-wrong');
    await page.locator('#login-button, button[type=submit]').first().click();
    await expect(page).not.toHaveURL(/tabs\/main\.php/);
  });

  test('admin can log in and reach the main tabs shell', async ({ page }) => {
    await login(page);
    await expect(page).toHaveURL(/tabs\/main\.php/);
  });
});
