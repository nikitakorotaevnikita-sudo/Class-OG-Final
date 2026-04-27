// test_backoffice.spec.js — AC-4: all backoffice UI criteria
// Tests: KPI cards, charts, IP table, Basic Auth enforcement

const { test, expect } = require('@playwright/test');

const BO_USER = process.env.BO_USER || 'admin';
const BO_PASS = process.env.BO_PASS || 'password';
const AUTH_HEADER = 'Basic ' + Buffer.from(BO_USER + ':' + BO_PASS).toString('base64');
const BASE = 'http://localhost:8000';

test.describe('AC-4: Backoffice page', () => {

  test('AC-4.1: GET /backoffice returns 200 with auth', async () => {
    const res = await fetch(BASE + '/backoffice', {
      headers: { 'Authorization': AUTH_HEADER },
    });
    expect(res.status).toBe(200);
    expect(res.headers.get('content-type')).toContain('text/html');
  });

  test('AC-4.2: 4 KPI cards present', async ({ page }) => {
    await page.goto(BASE + '/backoffice');
    await page.waitForSelector('.kpi', { timeout: 10000 });
    const kpis = await page.locator('.kpi').count();
    expect(kpis).toBe(4);
  });

  test('AC-4.3-4.5: 3 canvas charts present', async ({ page }) => {
    await page.goto(BASE + '/backoffice');
    await page.waitForSelector('canvas', { timeout: 10000 });
    const canvases = await page.locator('canvas').count();
    expect(canvases).toBe(3);
  });

  test('AC-4.6: IP stats table present', async ({ page }) => {
    await page.goto(BASE + '/backoffice');
    await page.waitForSelector('table#ip-stats', { timeout: 10000 });
    const tbody = page.locator('#ip-stats-body');
    await expect(tbody).toBeVisible();
  });

  test('AC-4.7: /api/backoffice/stats returns 401 without auth', async () => {
    const res = await fetch(BASE + '/api/backoffice/stats');
    expect(res.status).toBe(401);
  });

  test('AC-4.8: /api/backoffice/stats returns data with auth', async () => {
    const res = await fetch(BASE + '/api/backoffice/stats', {
      headers: { 'Authorization': AUTH_HEADER },
    });
    expect(res.status).toBe(200);
    const data = await res.json();
    expect(data).toHaveProperty('total_classifications');
    expect(data).toHaveProperty('confidence_histogram');
    expect(data).toHaveProperty('top_codes');
    expect(data).toHaveProperty('daily_usage');
    expect(data).toHaveProperty('ip_stats');
    expect(typeof data.confidence_histogram).toBe('object');
    expect(Array.isArray(data.top_codes)).toBe(true);
    expect(Array.isArray(data.ip_stats)).toBe(true);
  });

  test('AC-4.9: KPI section present with cards', async ({ page }) => {
    await page.goto(BASE + '/backoffice');
    // Wait for the KPI grid to be rendered (static part)
    await page.waitForSelector('.kpi-grid', { timeout: 10000 });
    const kpiCards = await page.locator('.kpi').count();
    expect(kpiCards).toBe(4);
    // Verify each card has label and value elements
    const labels = await page.locator('.kpi-label').count();
    const values = await page.locator('.kpi-value').count();
    expect(labels).toBe(4);
    expect(values).toBe(4);
  });

  test('AC-4.10: Chart canvases present in chart cards', async ({ page }) => {
    await page.goto(BASE + '/backoffice');
    await page.waitForSelector('.chart-card canvas', { timeout: 10000 });
    const canvases = await page.locator('canvas').count();
    expect(canvases).toBe(3);
  });

  test('AC-4.11: Historical link in sidebar visible', async ({ page }) => {
    await page.goto(BASE + '/backoffice');
    const link = page.locator('.sidebar a[href="/static/historical.html"]');
    await expect(link).toBeVisible();
  });

  test('AC-4.12: Classification link in sidebar visible', async ({ page }) => {
    await page.goto(BASE + '/backoffice');
    const link = page.locator('.sidebar a[href="/"]');
    await expect(link).toBeVisible();
  });

});