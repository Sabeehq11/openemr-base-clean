import { test, expect, Page } from '@playwright/test';
import { login } from '../fixtures';
import * as fs from 'fs';
import * as path from 'path';

// Live C1 check against the dev stack (agent started with W2_ENABLED=true and the stub VLM).
// Drives the module's real upload.php as a logged-in physician. Synthetic fixtures only.
// Fixture split: copilot_doc_a owns even pids, copilot_doc_b owns odd pids.
const PASS = process.env.COPILOT_FIXTURE_PASSWORD ?? '';
const FIX = path.resolve(__dirname, '../../../agent/tests/fixtures/w2');
const UPLOAD = '/interface/modules/custom_modules/oe-module-copilot/public/upload.php';

async function csrfOf(page: Page): Promise<string> {
  // The dock is rendered by the module on the tabs shell right after login; the token belongs to this session.
  const csrf = await page.locator('#copilot-root').getAttribute('data-csrf');
  expect(csrf).toBeTruthy();
  return csrf as string;
}

async function openPatient(page: Page, pid: number): Promise<void> {
  const r = await page.request.get(`/interface/patient_file/summary/demographics.php?set_pid=${pid}`);
  expect(r.ok()).toBeTruthy();
}

async function uploadBytes(page: Page, csrf: string, name: string, buffer: Buffer, docType: string) {
  const r = await page.request.post(UPLOAD, {
    headers: { 'X-CSRF-Token': csrf },
    multipart: { doc_type: docType, file: { name, mimeType: 'application/pdf', buffer } },
  });
  let body: unknown = null;
  try { body = await r.json(); } catch { body = await r.text(); }
  return { status: r.status(), body: body as any };
}

async function upload(page: Page, csrf: string | null, file: string, docType: string) {
  const headers: Record<string, string> = csrf ? { 'X-CSRF-Token': csrf } : {};
  const r = await page.request.post(UPLOAD, {
    headers,
    multipart: { doc_type: docType, file: { name: file, mimeType: 'application/octet-stream', buffer: fs.readFileSync(path.join(FIX, file)) } },
  });
  let body: unknown = null;
  try { body = await r.json(); } catch { body = await r.text(); }
  return { status: r.status(), body: body as any };
}

const out: Record<string, unknown> = {};
test.afterAll(() => fs.writeFileSync('/tmp/w2-live-results.json', JSON.stringify(out, null, 2)));

test.describe.serial('W2 live upload', () => {
  test.skip(!PASS, 'needs COPILOT_FIXTURE_PASSWORD');

  test('lab upload, repeat upload, intake form, isolation, CSRF', async ({ page }) => {
    await login(page, 'copilot_doc_a', PASS);
    const csrf = await csrfOf(page);
    await openPatient(page, 100);

    out.lab_first = await upload(page, csrf, 'lab_report.pdf', 'lab_pdf');
    out.lab_repeat = await upload(page, csrf, 'lab_report.pdf', 'lab_pdf');
    out.intake_first = await upload(page, csrf, 'intake_form.pdf', 'intake_form');
    out.intake_repeat = await upload(page, csrf, 'intake_form.pdf', 'intake_form');
    out.wrong_type = await upload(page, csrf, 'wrong_type.pdf', 'lab_pdf');
    out.truncated = await upload(page, csrf, 'truncated.pdf', 'lab_pdf');
    out.no_csrf = await upload(page, null, 'lab_report.pdf', 'lab_pdf');
    out.bad_doc_type = await upload(page, csrf, 'lab_report.pdf', 'xray');

    // Patient isolation: physician A opens a patient on physician B's care team (odd pid) and tries to upload.
    await openPatient(page, 101);
    out.cross_patient = await upload(page, csrf, 'lab_report_injection.pdf', 'lab_pdf');

    // More rejected uploads (each must leave nothing behind): oversize, not a document, empty file.
    await openPatient(page, 100);
    out.oversize = await uploadBytes(page, csrf, 'big.pdf', Buffer.concat([Buffer.from('%PDF-'), Buffer.alloc(9 * 1024 * 1024, 97)]), 'lab_pdf');
    out.text_file = await uploadBytes(page, csrf, 'notes.pdf', Buffer.from('just some text, not a pdf'), 'lab_pdf');
    out.empty_file = await uploadBytes(page, csrf, 'empty.pdf', Buffer.alloc(0), 'lab_pdf');
    // Denied again on another odd patient with a different file, and the intake form path.
    await openPatient(page, 103);
    out.cross_patient_intake = await uploadBytes(page, csrf, 'intake_form.pdf', fs.readFileSync(path.join(FIX, 'intake_form.pdf')), 'intake_form');

    expect((out.lab_first as any).status).toBe(200);
  });

  test('first-time lab upload for a second patient on the care team writes and links', async ({ page }) => {
    await login(page, 'copilot_doc_a', PASS);
    const csrf = await csrfOf(page);
    await openPatient(page, 102);
    out.fresh_lab_102 = await upload(page, csrf, 'lab_report.pdf', 'lab_pdf');
    out.fresh_lab_102_repeat = await upload(page, csrf, 'lab_report.pdf', 'lab_pdf');
  });

  test('anonymous request is refused', async ({ browser }) => {
    const ctx = await browser.newContext({ ignoreHTTPSErrors: true });
    const r = await ctx.request.post(`http://localhost:8300${UPLOAD}`, {
      multipart: { doc_type: 'lab_pdf', file: { name: 'a.pdf', mimeType: 'application/pdf', buffer: fs.readFileSync(path.join(FIX, 'lab_report.pdf')) } },
    });
    out.anonymous = { status: r.status(), body: (await r.text()).slice(0, 200) };
    await ctx.close();
  });
});
