// E2E: вкладка «Настройки» в бэк-офисе — модель LLM и креды Directum RX.
const { test, expect } = require('@playwright/test');
const fs = require('fs');
const path = require('path');

/** Реальный пароль RX читаем из .env (он вне git), а не пишем в тест. */
function rxPasswordFromEnv() {
  const envPath = path.join(__dirname, '..', '..', '.env');
  if (!fs.existsSync(envPath)) return null;
  const line = fs.readFileSync(envPath, 'utf8')
    .split(/\r?\n/)
    .find((l) => l.startsWith('RX_PASSWORD='));
  const value = line ? line.slice('RX_PASSWORD='.length).trim() : '';
  return value || null;
}

/** Собираем ошибки консоли: молчаливый JS-сбой не должен пройти как «страница открылась». */
function watchConsole(page) {
  const errors = [];
  page.on('pageerror', (e) => errors.push(String(e)));
  page.on('console', (m) => { if (m.type() === 'error') errors.push(m.text()); });
  return errors;
}

async function openSettings(page) {
  await page.goto('/backoffice');
  await page.getByRole('tab', { name: 'Настройки' }).click();
}

test('вкладка переключается и форма настроек рендерится', async ({ page }) => {
  const errors = watchConsole(page);
  await openSettings(page);

  await expect(page.locator('#panel-settings')).toBeVisible();
  await expect(page.locator('#panel-stats')).toBeHidden();

  // Обе группы полей отрисованы
  await expect(page.getByRole('heading', { name: 'Модель LLM' })).toBeVisible();
  await expect(page.getByRole('heading', { name: 'Directum RX' })).toBeVisible();

  expect(errors).toEqual([]);
});

test('провайдер и адрес RX подтягиваются из конфига', async ({ page }) => {
  await openSettings(page);

  await expect(page.locator('#set-LLM_PROVIDER')).toHaveValue(/ario|groq|gemini|ollama/);
  await expect(page.locator('#set-RX_ODATA_URL')).toHaveValue(/^https?:\/\//);
  await expect(page.locator('#set-RX_USER')).not.toHaveValue('');
});

test('секреты не приезжают на страницу в открытом виде', async ({ page }) => {
  await openSettings(page);

  const password = page.locator('#set-RX_PASSWORD');
  await expect(password).toHaveAttribute('type', 'password');
  // Поле пустое: маска живёт в placeholder, чтобы сохранение её не записало
  await expect(password).toHaveValue('');
  await expect(password).toHaveAttribute('placeholder', /Сохранён|Не задан/);

  const secret = rxPasswordFromEnv();
  test.skip(!secret, 'RX_PASSWORD не задан в .env — проверять нечего');

  const html = await page.content();
  expect(html).not.toContain(secret);
});

test('«Сохранить» без правок сообщает, что менять нечего', async ({ page }) => {
  await openSettings(page);
  await page.locator('#settings-save').click();

  await expect(page.locator('#settings-status')).toContainText('Изменений нет');
});

test('невалидный URL отклоняется с понятным текстом', async ({ page }) => {
  await openSettings(page);

  await page.locator('#set-RX_ODATA_URL').fill('172.16.104.68/integration/odata');
  await page.locator('#settings-save').click();

  await expect(page.locator('#settings-status')).toContainText('http:// или https://');
});

test('проверка подключения к RX показывает результат', async ({ page }) => {
  await openSettings(page);
  await page.locator('#settings-test-rx').click();

  await expect(page.locator('#settings-status')).toContainText(
    /Подключение успешно|креды отклонены|Нет соединения/, { timeout: 30000 });
});

test('проверка подключения отчитывается об ошибке на недостижимом хосте', async ({ page }) => {
  await openSettings(page);

  await page.locator('#set-RX_ODATA_URL').fill('http://192.0.2.1/integration/odata');
  await page.locator('#settings-test-rx').click();

  await expect(page.locator('#settings-status')).toContainText('Нет соединения', { timeout: 40000 });
});

test('«Сбросить изменения» возвращает значения из конфига', async ({ page }) => {
  await openSettings(page);

  const url = page.locator('#set-RX_ODATA_URL');
  const original = await url.inputValue();
  await url.fill('http://example.invalid/odata');

  await page.locator('#settings-reload').click();
  await expect(url).toHaveValue(original);
});
