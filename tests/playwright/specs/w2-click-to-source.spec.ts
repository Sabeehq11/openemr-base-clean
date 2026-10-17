import { test, expect } from '@playwright/test';
import { login } from '../fixtures';
import * as fs from 'fs';
import * as path from 'path';

// C5 deployed check: upload a synthetic lab report, see the extraction, open "View source" and check the page image
// and the cited-region box are shown. Run once against the tunnel:
//   BASE_URL=https://<name>.trycloudflare.com COPILOT_FIXTURE_USER=copilot_doc_a COPILOT_FIXTURE_PASSWORD=... \
//     npx playwright test specs/w2-click-to-source.spec.ts
// Needs a stack with W2_ENABLED=true. Synthetic fixtures only. Skipped without credentials.
// The agent is private, so this goes only through the module's upload.php and source.php.
const USER = process.env.COPILOT_FIXTURE_USER ?? 'copilot_doc_a';
const PASS = process.env.COPILOT_FIXTURE_PASSWORD ?? '';
const PID = Number(process.env.COPILOT_FIXTURE_PID ?? 100); // even pids belong to copilot_doc_a
const FIX = path.resolve(__dirname, '../../../agent/tests/fixtures/w2');

test.describe('W2 click-to-source', () => {
  test.skip(!PASS, 'needs COPILOT_FIXTURE_PASSWORD');

  test('upload, extraction, View source shows the page and a positioned box', async ({ page }) => {
    await login(page, USER, PASS);
    const open = await page.request.get(`/interface/patient_file/summary/demographics.php?set_pid=${PID}`);
    expect(open.ok()).toBeTruthy();
    await page.reload();

    await page.locator('#copilot-toggle').click();
    await expect(page.locator('#copilot-panel')).toBeVisible();
    await page.locator('.copilot-upload-type').selectOption('lab_pdf');
    await page.locator('.copilot-upload-file').setInputFiles(path.join(FIX, 'lab_report.pdf'));
    await page.locator('.copilot-upload button[type=submit]').click();

    const answer = page.locator('.copilot-answer').filter({ hasText: 'lab result' }).first();
    await expect(answer).toBeVisible({ timeout: 60_000 });

    const view = answer.locator('.copilot-view-source').first();
    await expect(view).toBeVisible();
    const source = page.waitForResponse((r) => r.url().includes('/source.php') && r.status() === 200);
    await view.click();
    expect((await source).headers()['content-type']).toMatch(/^image\//);

    const img = answer.locator('.copilot-source-img').first();
    await expect(img).toBeVisible();
    await expect.poll(() => img.evaluate((i: HTMLImageElement) => i.naturalWidth)).toBeGreaterThan(0);

    const box = answer.locator('[data-testid=copilot-source-bbox]').first();
    await expect(box).toBeVisible();
    const [imgBox, mark] = [await img.boundingBox(), await box.boundingBox()];
    expect(imgBox && mark).toBeTruthy();
    // the box sits inside the page image
    expect(mark!.x).toBeGreaterThanOrEqual(imgBox!.x - 1);
    expect(mark!.y).toBeGreaterThanOrEqual(imgBox!.y - 1);
    expect(mark!.x + mark!.width).toBeLessThanOrEqual(imgBox!.x + imgBox!.width + 1);
    expect(mark!.y + mark!.height).toBeLessThanOrEqual(imgBox!.y + imgBox!.height + 1);
    await expect(answer.locator('.copilot-source-quote').first()).toContainText('Page');

    // a document id that is not this patient's gets the same not-found as a missing one
    const other = await page.request.get(
      '/interface/modules/custom_modules/oe-module-copilot/public/source.php?document_id=999999&page=1');
    expect(other.status()).toBe(404);
  });

  test('anonymous request to source.php is refused', async ({ browser, baseURL }) => {
    const ctx = await browser.newContext({ ignoreHTTPSErrors: true });
    const r = await ctx.request.get(
      `${baseURL}/interface/modules/custom_modules/oe-module-copilot/public/source.php?document_id=1&page=1`,
      { maxRedirects: 0 });
    expect(r.status()).not.toBe(200);
    await ctx.close();
  });
});
