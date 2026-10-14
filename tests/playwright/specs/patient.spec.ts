import { test, expect } from '../fixtures';

// Uses seeded demo data (scripts/copilot/seed_demo_patients.py): pid 100 = Maria Alvarez.
test.describe('Patient chart', () => {
  test('patient finder lists seeded patients', async ({ loggedIn }) => {
    await loggedIn.goto('/interface/main/finder/dynamic_finder.php');
    await expect(loggedIn.locator('table#pt_table')).toBeVisible();
    await loggedIn.locator('input[type=search]').first().fill('Alvarez');
    await expect(loggedIn.getByText('Alvarez').first()).toBeVisible();
  });

  test('opens the demographics summary for a demo patient', async ({ loggedIn }) => {
    await loggedIn.goto('/interface/patient_file/summary/demographics.php?set_pid=100');
    await expect(loggedIn.getByText(/Alvarez/).first()).toBeVisible();
  });

  test('REST API rejects unauthenticated requests', async ({ request }) => {
    const res = await request.get('/apis/default/api/patient');
    expect([401, 403]).toContain(res.status());
  });
});
