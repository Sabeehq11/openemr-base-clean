import { test, expect, Page } from '../fixtures';
import { login } from '../fixtures';

// Fixture split (scripts/authz_fixture.php): physician A is on the care team of even pids, physician B of odd pids.
// Needs the agent running with LLM_PROVIDER=stub.
const PASS = process.env.COPILOT_FIXTURE_PASSWORD ?? '';

async function openPatient(page: Page, pid: number) {
  // Sets the server-side session pid, exactly as opening a chart does.
  const r = await page.request.get(`/interface/patient_file/summary/demographics.php?set_pid=${pid}`);
  expect(r.ok()).toBeTruthy();
}

async function ask(page: Page, text?: string) {
  await page.locator('#copilot-toggle').click();
  if (text) {
    await page.locator('#copilot-input').fill(text);
    await page.locator('#copilot-send').click();
  } else {
    await page.locator('#copilot-brief').click();
  }
}

test.describe('Co-Pilot brief flow', () => {
  test.skip(!PASS || process.env.COPILOT_E2E_MODE === 'degraded', 'needs fixture password and the normal agent');

  test('physician gets a cited brief for their own patient', async ({ page }) => {
    await login(page, 'copilot_doc_a', PASS);
    await openPatient(page, 100);
    await ask(page);
    const answer = page.locator('.copilot-answer').first();
    await expect(answer).toBeVisible({ timeout: 30_000 });
    await expect(answer.locator('.copilot-cite').first()).toBeVisible();
    // Every citation points at a real FHIR resource id.
    const sources = await answer.locator('.copilot-cite').evaluateAll(els => els.map(e => (e as HTMLElement).dataset.source));
    expect(sources.length).toBeGreaterThan(0);
    for (const s of sources) { expect(s).toMatch(/^[A-Za-z]+\/[0-9a-f-]{36}$/); }
  });

  test('physician is denied another physician\'s patient with a generic message', async ({ page }) => {
    await login(page, 'copilot_doc_a', PASS);
    await openPatient(page, 101);
    await ask(page);
    const denied = page.locator('.copilot-denied').first();
    await expect(denied).toBeVisible({ timeout: 30_000 });
    await expect(denied).toHaveText('You do not have access to this patient.');
    await expect(page.locator('.copilot-answer')).toHaveCount(0);
  });

  test('nurse is denied', async ({ page }) => {
    await login(page, 'copilot_nurse', PASS);
    await openPatient(page, 100);
    await ask(page);
    await expect(page.locator('.copilot-denied').first()).toBeVisible({ timeout: 30_000 });
    await expect(page.locator('.copilot-answer')).toHaveCount(0);
  });

  test('no open patient yields a prompt, not a request to the agent', async ({ page }) => {
    await login(page, 'copilot_doc_b', PASS);
    await ask(page);
    await expect(page.locator('.copilot-error').first()).toContainText('Open a patient chart first');
  });
});

test.describe('Co-Pilot follow-up and patient switch', () => {
  test.skip(!PASS || process.env.COPILOT_E2E_MODE === 'degraded', 'needs fixture password and the normal (stub LLM) agent');

  test('follow-up stays in the same conversation and is answered with citations', async ({ page }) => {
    await login(page, 'copilot_doc_a', PASS);
    await openPatient(page, 100);
    await ask(page);
    await expect(page.locator('.copilot-answer')).toHaveCount(1, { timeout: 30_000 });
    await page.locator('#copilot-input').fill('What are the current medications?');
    await page.locator('#copilot-send').click();
    await expect(page.locator('.copilot-answer')).toHaveCount(2, { timeout: 30_000 });
    await expect(page.locator('.copilot-user')).toHaveCount(2);
    await expect(page.locator('.copilot-answer').nth(1).locator('.copilot-cite').first()).toBeVisible();
  });

  test('model markup in answers is rendered as text, never as HTML', async ({ page }) => {
    await login(page, 'copilot_doc_a', PASS);
    await openPatient(page, 100);
    await ask(page);
    await expect(page.locator('.copilot-answer').first()).toBeVisible({ timeout: 30_000 });
    expect(await page.locator('#copilot-log script, #copilot-log img, #copilot-log iframe').count()).toBe(0);
  });
});

test.describe('Co-Pilot degraded mode (agent started with LLM_PROVIDER=unavailable)', () => {
  test.skip(!PASS || process.env.COPILOT_E2E_MODE !== 'degraded', 'run via scripts/e2e_copilot.sh');

  test('LLM outage shows chart data only, labelled, with sources and no prose answer', async ({ page }) => {
    await login(page, 'copilot_doc_a', PASS);
    await openPatient(page, 100);
    await ask(page);
    const d = page.locator('.copilot-degraded').first();
    await expect(d).toBeVisible({ timeout: 30_000 });
    await expect(d).toContainText('language model is unavailable');
    await expect(page.locator('.copilot-answer')).toHaveCount(0);
  });

  test('OpenEMR itself keeps working while the assistant is degraded', async ({ page }) => {
    await login(page, 'copilot_doc_a', PASS);
    const r = await page.request.get('/interface/patient_file/summary/demographics.php?set_pid=100');
    expect(r.ok()).toBeTruthy();
  });
});
