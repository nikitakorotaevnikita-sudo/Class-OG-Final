// vision.js — распознавание текста со скана: загрузка файла, вызов модели, вывод по страницам.

(function () {
  'use strict';

  const $ = (id) => document.getElementById(id);
  const MAX_MB = 20;

  let selectedFile = null;
  let recognisedText = '';

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

  function showError(message) {
    const box = $('error');
    box.textContent = message;
    box.classList.toggle('hidden', !message);
  }

  function setBusy(busy, text) {
    $('loading').classList.toggle('hidden', !busy);
    if (text) $('loading-text').textContent = text;
    $('btn-recognize').disabled = busy || !selectedFile;
  }

  // ── Выбор файла ───────────────────────────────────────────────────────────

  function pickFile(file) {
    showError('');
    if (!file) return;

    const name = file.name || '';
    const ok = /\.(png|jpe?g|webp|pdf)$/i.test(name);
    if (!ok) {
      showError('Нужен PNG, JPG, WEBP или PDF — этот формат распознавание не принимает.');
      return;
    }
    if (file.size > MAX_MB * 1024 * 1024) {
      showError(`Файл ${(file.size / 1024 / 1024).toFixed(1)} МБ, предел ${MAX_MB} МБ.`);
      return;
    }

    selectedFile = file;
    const line = $('file-line');
    line.textContent = `${name} · ${(file.size / 1024 / 1024).toFixed(1)} МБ`;
    line.classList.remove('hidden');
    $('btn-recognize').disabled = false;
  }

  function reset() {
    selectedFile = null;
    recognisedText = '';
    $('file-input').value = '';
    $('file-line').classList.add('hidden');
    $('result-section').classList.add('hidden');
    $('pages').innerHTML = '';
    $('btn-recognize').disabled = true;
    $('btn-copy').disabled = true;
    showError('');
  }

  // ── Распознавание ─────────────────────────────────────────────────────────

  async function recognise() {
    if (!selectedFile) return;
    showError('');
    $('result-section').classList.add('hidden');

    const isPdf = /\.pdf$/i.test(selectedFile.name);
    setBusy(true, isPdf
      ? 'Распознаём страницы PDF — по одному запросу к модели на страницу, это небыстро...'
      : 'Распознаём изображение...');

    const form = new FormData();
    form.append('file', selectedFile);

    try {
      const response = await fetch('/api/recognize-image', { method: 'POST', body: form });
      const data = await response.json();
      if (!response.ok) throw new Error(data.detail || ('HTTP ' + response.status));
      render(data);
      toast('Готово', 'success');
    } catch (error) {
      showError('Распознать не удалось: ' + error.message);
      toast('Ошибка распознавания', 'error');
    } finally {
      setBusy(false);
    }
  }

  function render(data) {
    const pages = data.pages || [];
    recognisedText = pages.map((p) => p.text || '').join('\n\n').trim();

    $('result-meta').textContent =
      `${data.filename} · страниц: ${pages.length} · ${data.provider}/${data.model} · ${data.elapsed_sec} с`;

    $('pages').innerHTML = pages.map((page) => `
      <article class="result-card">
        <div class="meta-label">Страница ${page.page}</div>
        <pre class="recognised-text">${esc(page.text) || '(пусто)'}</pre>
      </article>`).join('');

    $('result-section').classList.remove('hidden');
    $('btn-copy').disabled = !recognisedText;
  }

  async function copyText() {
    if (!recognisedText) return;
    try {
      await navigator.clipboard.writeText(recognisedText);
      toast('Текст скопирован', 'success');
    } catch (error) {
      showError('Браузер не дал доступ к буферу обмена: ' + error.message);
    }
  }

  // ── Инициализация ─────────────────────────────────────────────────────────

  document.addEventListener('DOMContentLoaded', () => {
    const zone = $('drop-zone');
    const input = $('file-input');

    zone.addEventListener('click', () => input.click());
    input.addEventListener('change', () => pickFile(input.files[0]));

    ['dragenter', 'dragover'].forEach((event) => {
      zone.addEventListener(event, (e) => {
        e.preventDefault();
        zone.classList.add('drop-zone-active');
      });
    });
    ['dragleave', 'drop'].forEach((event) => {
      zone.addEventListener(event, (e) => {
        e.preventDefault();
        zone.classList.remove('drop-zone-active');
      });
    });
    zone.addEventListener('drop', (e) => pickFile(e.dataTransfer.files[0]));

    $('btn-recognize').addEventListener('click', recognise);
    $('btn-copy').addEventListener('click', copyText);
    $('btn-reset').addEventListener('click', reset);
  });
})();
