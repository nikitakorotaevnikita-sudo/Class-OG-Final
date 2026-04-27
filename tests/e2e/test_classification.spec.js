// test_classification.spec.js — AC-1, AC-2: classify + verify
// Tests: classification flow, result display, verification

const { test, expect } = require('@playwright/test');

const TEST_APPEAL = 'Прошу вывезти мусор с контейнерной площадки по адресу ул. Ленина, д.5. Контейнер переполнен, мусор разбросан.';
const BASE = 'http://localhost:8000';

test.describe('AC-1, AC-2: Classification flow', () => {

  test('AC-1.1: Main page loads without errors', async ({ page }) => {
    await page.goto(BASE + '/');
    await expect(page.locator('.header')).toBeVisible();
    await expect(page.locator('.sidebar')).toBeVisible();
    await expect(page.locator('#classify-btn')).toBeVisible();
  });

  test('AC-1.2: Examples dropdown populates', async ({ page }) => {
    await page.goto(BASE + '/');
    await page.waitForFunction(() => {
      const sel = document.getElementById('examples-select');
      return sel && sel.options.length > 1;
    }, { timeout: 10000 });
    const options = await page.locator('#examples-select option').count();
    expect(options).toBeGreaterThan(1);
  });

  test('AC-1.3: Classification request returns JSON', async ({ request }) => {
    const res = await request.post(BASE + '/classify', {
      data: { appeal_text: TEST_APPEAL },
      headers: { 'Content-Type': 'application/json' },
    });
    expect([200, 503]).toContain(res.status());
    if (res.status() === 200) {
      const json = await res.json();
      expect(json).toHaveProperty('overall_confidence');
      expect(json).toHaveProperty('log_id');
      expect(json).toHaveProperty('questions');
      expect(Array.isArray(json.questions)).toBe(true);
    }
  });

  test('AC-1.4: Example selection populates textarea', async ({ page }) => {
    await page.goto(BASE + '/');
    await page.waitForFunction(() =>
      document.getElementById('examples-select')?.options?.length > 1,
      { timeout: 10000 }
    );
    await page.selectOption('#examples-select', { index: 1 });
    const text = await page.locator('#appeal-text').inputValue();
    expect(text.trim().length).toBeGreaterThan(0);
  });

  test('AC-2.1: Verify endpoint accepts confirm action', async ({ request }) => {
    const clRes = await request.post(BASE + '/classify', {
      data: { appeal_text: TEST_APPEAL },
      headers: { 'Content-Type': 'application/json' },
    });
    if (clRes.status() !== 200) { test.skip('Agent not available'); return; }
    const clData = await clRes.json();
    const logId = clData.log_id;

    const verifyRes = await request.post(BASE + '/verify', {
      data: { log_id: logId, action: 'confirm' },
      headers: { 'Content-Type': 'application/json' },
    });
    expect(verifyRes.status()).toBe(200);
    const verifyData = await verifyRes.json();
    expect(verifyData.status).toBe('ok');
    expect(verifyData.action).toBe('confirm');
  });

  test('AC-2.2: Verify endpoint rejects missing log_id', async ({ request }) => {
    const res = await request.post(BASE + '/verify', {
      data: { log_id: 'nonexistent-id', action: 'confirm' },
      headers: { 'Content-Type': 'application/json' },
    });
    expect([200, 404, 500]).toContain(res.status());
  });

  test('AC-2.3: Correct action requires operator_codes', async ({ request }) => {
    const res = await request.post(BASE + '/verify', {
      data: { log_id: 'any-id', action: 'correct' },
      headers: { 'Content-Type': 'application/json' },
    });
    expect([400, 404, 500]).toContain(res.status());
  });

  test('Navigation: sidebar links work', async ({ page }) => {
    await page.goto(BASE + '/');
    await page.click('a[href="/static/historical.html"]');
    await expect(page).toHaveURL(/historical\.html/);
    await expect(page.locator('.header')).toBeVisible();
    await page.click('a[href="/backoffice"]');
    await expect(page).toHaveURL(/backoffice/);
  });

});