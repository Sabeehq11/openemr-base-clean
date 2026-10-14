import { test, expect } from '../fixtures';
import { login } from '../fixtures';

// Needs the agent running (stub provider) and scripts/copilot/authz_fixture.php applied.
// COPILOT_FIXTURE_PASSWORD is the fixture users' password (kept in agent/.env, never committed).
const PASS = process.env.COPILOT_FIXTURE_PASSWORD ?? '';

test.describe('Co-Pilot dock (module wiring)', () => {
  test.skip(!PASS, 'COPILOT_FIXTURE_PASSWORD not set');

  test('dock is rendered for a logged-in user and sends no authority from the browser', async ({ page }) => {
    await login(page, 'copilot_doc_a', PASS);
    await expect(page.locator('#copilot-toggle')).toBeVisible();
    const root = page.locator('#copilot-root');
    // Only the endpoint and a CSRF token are in the page. No signing secret, OAuth token or agent URL.
    const html = await page.content();
    expect(html).not.toMatch(/signing_secret|client_secret|Bearer |agent:8400/);
    expect(await root.getAttribute('data-endpoint')).toContain('oe-module-copilot/public/chat.php');
  });

  test('unauthenticated and CSRF-less calls to the proxy are refused', async ({ page, request }) => {
    const anon = await request.post('/interface/modules/custom_modules/oe-module-copilot/public/chat.php',
      { data: { question: 'hi' } });
    // OpenEMR's own session bootstrap refuses it before any module code runs.
    expect([302, 400, 401, 403]).toContain(anon.status());
    await login(page, 'copilot_doc_a', PASS);
    const noCsrf = await page.request.post('/interface/modules/custom_modules/oe-module-copilot/public/chat.php',
      { data: { question: 'hi' } });
    expect(noCsrf.status()).toBe(403);
  });

  test('module files and config are not served over HTTP', async ({ request }) => {
    for (const p of ['config.local.php', 'src/ChatProxy.php', 'config.local.php.example']) {
      const r = await request.get('/interface/modules/custom_modules/oe-module-copilot/' + p);
      expect(r.status()).toBe(403);
    }
  });
});
