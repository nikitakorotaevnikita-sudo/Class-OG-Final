// settings.js — вкладка «Настройки» бэк-офиса: модель LLM и креды Directum RX

(function() {
  'use strict';

  const $ = (id) => document.getElementById(id);

  // Значения на момент загрузки формы — по ним считаем, что реально изменилось.
  let initialValues = {};

  function esc(value) {
    return String(value ?? '')
      .replaceAll('&', '&amp;')
      .replaceAll('<', '&lt;')
      .replaceAll('>', '&gt;')
      .replaceAll('"', '&quot;')
      .replaceAll("'", '&#039;');
  }

  function toast(message, kind) {
    const el = $('toast');
    if (!el) return;
    el.textContent = message;
    el.style.background = kind === 'error' ? 'var(--red)' :
                          kind === 'success' ? 'var(--green)' : 'var(--navy)';
    el.classList.remove('hidden');
    clearTimeout(el._tid);
    el._tid = setTimeout(() => el.classList.add('hidden'), 3200);
  }

  function setStatus(message, kind) {
    const box = $('settings-status');
    if (!box) return;
    if (!message) {
      box.innerHTML = '';
      return;
    }
    const cls = kind === 'error' ? 'alert-red' :
                kind === 'success' ? 'alert-green' : 'alert-blue';
    box.innerHTML = `<div class="alert ${cls}">${esc(message)}</div>`;
  }

  // ── Вкладки ───────────────────────────────────────────────────────────────

  function activateTab(name) {
    const isSettings = name === 'settings';
    $('tab-stats').setAttribute('aria-selected', String(!isSettings));
    $('tab-settings').setAttribute('aria-selected', String(isSettings));
    $('panel-stats').classList.toggle('hidden', isSettings);
    $('panel-settings').classList.toggle('hidden', !isSettings);
    if (isSettings && !Object.keys(initialValues).length) loadSettings();
  }

  // ── Отрисовка формы ───────────────────────────────────────────────────────

  function renderField(field) {
    const id = `set-${field.key}`;
    let control;

    if (field.kind === 'select') {
      const options = (field.options || [])
        .map((opt) => `<option value="${esc(opt)}"${opt === field.value ? ' selected' : ''}>${esc(opt)}</option>`)
        .join('');
      control = `<select id="${id}" data-key="${esc(field.key)}">${options}</select>`;
    } else {
      // Секрет показываем как обычный текст-плейсхолдер с маской: поле пустое,
      // чтобы случайное сохранение не перезаписало ключ его же маской.
      const isSecret = field.is_secret;
      const type = isSecret ? 'password' : 'text';
      const value = isSecret ? '' : esc(field.value);
      const placeholder = isSecret
        ? (field.has_value ? 'Сохранён — оставьте пустым, чтобы не менять' : 'Не задан')
        : '';
      control = `<input id="${id}" type="${type}" data-key="${esc(field.key)}"` +
                ` value="${value}" placeholder="${esc(placeholder)}" autocomplete="off" spellcheck="false">`;
    }

    const hint = field.hint ? `<span class="field-hint">${esc(field.hint)}</span>` : '';
    const secretState = field.is_secret && field.has_value
      ? '<span class="field-secret-set">значение задано</span>'
      : '';

    return `<label class="field">
      <span>${esc(field.label)}</span>
      ${control}
      ${hint}${secretState}
    </label>`;
  }

  function renderGroups(data) {
    initialValues = {};
    const html = (data.groups || []).map((group) => {
      const fields = group.fields.map((field) => {
        // Для секретов эталон — пустая строка: непустое поле = пользователь ввёл новое.
        initialValues[field.key] = field.is_secret ? '' : field.value;
        return renderField(field);
      }).join('');
      return `<section aria-label="${esc(group.label)}">
        <div class="section-heading"><div><h2>${esc(group.label)}</h2></div></div>
        <div class="settings-grid">${fields}</div>
      </section>`;
    }).join('');

    $('settings-groups').innerHTML = html;
    $('settings-groups').classList.remove('hidden');
    $('settings-loading').classList.add('hidden');
    $('settings-save').disabled = false;
    $('settings-test-rx').disabled = false;

    const envPath = $('settings-env-path');
    if (envPath && data.env_path) envPath.textContent = data.env_path;
  }

  // ── Сбор значений ─────────────────────────────────────────────────────────

  function readForm() {
    const values = {};
    document.querySelectorAll('#settings-groups [data-key]').forEach((el) => {
      values[el.dataset.key] = el.value;
    });
    return values;
  }

  /** Только реально изменённые поля — чтобы не переписывать .env целиком. */
  function changedValues() {
    const current = readForm();
    const diff = {};
    for (const [key, value] of Object.entries(current)) {
      if (value.trim() === '') continue;          // пустой секрет = «не менять»
      if (value === initialValues[key]) continue; // не тронуто
      diff[key] = value;
    }
    return diff;
  }

  // ── Действия ──────────────────────────────────────────────────────────────

  async function loadSettings() {
    $('settings-loading').classList.remove('hidden');
    $('settings-groups').classList.add('hidden');
    setStatus('');
    try {
      const res = await fetch('/api/settings');
      if (!res.ok) throw new Error('HTTP ' + res.status);
      renderGroups(await res.json());
    } catch (error) {
      $('settings-loading').classList.add('hidden');
      setStatus('Не удалось загрузить настройки: ' + error.message, 'error');
    }
  }

  async function saveSettings() {
    const values = changedValues();
    if (!Object.keys(values).length) {
      setStatus('Изменений нет', 'info');
      return;
    }

    const btn = $('settings-save');
    btn.disabled = true;
    setStatus('');
    try {
      const res = await fetch('/api/settings', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ values }),
      });
      const data = await res.json();
      if (!res.ok) throw new Error(data.detail || ('HTTP ' + res.status));

      const saved = (data.saved || []).join(', ');
      setStatus(`${data.message}. Применены без перезапуска: ${saved}`, 'success');
      toast('Настройки сохранены', 'success');
      await loadSettings();   // перечитываем — маски секретов обновятся
    } catch (error) {
      setStatus('Ошибка сохранения: ' + error.message, 'error');
      toast('Не удалось сохранить настройки', 'error');
    } finally {
      btn.disabled = false;
    }
  }

  async function testRx() {
    const btn = $('settings-test-rx');
    const form = readForm();
    btn.disabled = true;
    setStatus('Проверяем подключение к RX...', 'info');
    try {
      const res = await fetch('/api/settings/test-rx', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          RX_ODATA_URL: form.RX_ODATA_URL || null,
          RX_USER: form.RX_USER || null,
          // пустой пароль → сервер возьмёт сохранённый
          RX_PASSWORD: form.RX_PASSWORD || null,
        }),
      });
      const data = await res.json();
      if (!res.ok) throw new Error(data.detail || ('HTTP ' + res.status));
      setStatus(data.detail, data.ok ? 'success' : 'error');
    } catch (error) {
      setStatus('Проверка не выполнена: ' + error.message, 'error');
    } finally {
      btn.disabled = false;
    }
  }

  // ── Инициализация ─────────────────────────────────────────────────────────

  document.addEventListener('DOMContentLoaded', () => {
    if (!$('panel-settings')) return;   // страница без вкладки настроек

    $('tab-stats').addEventListener('click', () => activateTab('stats'));
    $('tab-settings').addEventListener('click', () => activateTab('settings'));
    $('settings-save').addEventListener('click', saveSettings);
    $('settings-test-rx').addEventListener('click', testRx);
    $('settings-reload').addEventListener('click', loadSettings);

    if (location.hash === '#settings') activateTab('settings');
  });
})();
