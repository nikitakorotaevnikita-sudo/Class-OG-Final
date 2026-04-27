// app.js — классификация обращений граждан (Directum Design System)

(function() {
  'use strict';

  // ── Состояние ─────────────────────────────────────────────────────────────
  let lastResult = null;
  let lastLogId = null;
  let verifiedCount = 0;
  let finetuneThreshold = 50;

  // ── Утилиты ─────────────────────────────────────────────────────────────────
  const $ = (id) => document.getElementById(id);

  function toast(message, kind) {
    const el = $('toast');
    if (!el) return;
    el.textContent = message;
    el.style.background = kind === 'error' ? 'var(--red)' :
                          kind === 'success' ? 'var(--green)' : 'var(--navy)';
    el.classList.remove('hidden');
    clearTimeout(el._tid);
    el._tid = setTimeout(() => el.classList.add('hidden'), 2500);
  }

  // ── Загрузка статистики ───────────────────────────────────────────────────
  async function loadStats() {
    try {
      const res = await fetch('/stats');
      if (res.ok) {
        const data = await res.json();
        verifiedCount = data.verified;
        finetuneThreshold = data.threshold;
        if (data.verified >= data.threshold) {
          toast('Порог дообучения достигнут! Запустите python src/auto_finetune.py', 'success');
        }
      }
    } catch(e) {}
  }

  // ── Загрузка примеров ───────────────────────────────────────────────────────
  async function loadExamples() {
    try {
      const res = await fetch('/examples');
      if (!res.ok) throw new Error('HTTP ' + res.status);
      const data = await res.json();
      const select = $('examples-select');
      for (const ex of data.examples) {
        const opt = document.createElement('option');
        opt.value = ex.id;
        opt.textContent = ex.title;
        opt.dataset.text = ex.text;
        select.appendChild(opt);
      }
    } catch(err) {
      toast('Не удалось загрузить примеры: ' + err.message, 'error');
    }
  }

  // ── Подстановка текста при выборе примера ────────────────────────────────────
  $('examples-select').addEventListener('change', (e) => {
    const opt = e.target.selectedOptions[0];
    if (opt && opt.dataset.text) {
      $('appeal-text').value = opt.dataset.text;
    }
  });

  // ── Цветовая шкала уверенности ───────────────────────────────────────────────
  function confidenceBar(value) {
    const pct = Math.round(value * 100);
    const color = value >= 0.80 ? 'var(--green)' :
                  value >= 0.65 ? 'var(--amber)' : 'var(--red)';
    const cls   = value >= 0.80 ? 'conf-bar-green' :
                  value >= 0.65 ? 'conf-bar-amber' : 'conf-bar-red';
    return `
      <div class="conf-bar-wrap" style="margin-top:8px">
        <div class="conf-bar-bg">
          <div class="conf-bar-fill ${cls}" style="width:${pct}%"></div>
        </div>
        <div class="text-xs text-muted">Уверенность: ${pct}%</div>
      </div>
    `;
  }

  // ── Рендер одного вопроса ────────────────────────────────────────────────────
  function renderQuestion(q, idx, total) {
    const altsHtml = (q.alternatives || []).length
      ? `<div class="question-alts">Альтернативы: ${
          q.alternatives.map(a => `<span class="font-mono">${a.code}</span>`).join(', ')
        }</div>`
      : '';
    const header = total > 1
      ? `<div class="question-header">Вопрос ${idx + 1} из ${total}</div>`
      : '';
    return `
      <div class="question-block">
        ${header}
        <div class="question-grid">
          <div class="question-grid-label">Код:</div>
          <div class="font-mono question-grid-val">${q.code}</div>
          <div class="question-grid-label">Тема:</div>
          <div class="question-grid-val">${q.name}</div>
          <div class="question-grid-label">Путь:</div>
          <div class="text-xs" style="color:var(--subtle)">${q.full_path}</div>
          <div class="question-grid-label">Ведение:</div>
          <div class="question-grid-val">${q.predmet_vedeniya}</div>
          <div class="question-grid-label">Обоснование:</div>
          <div style="color:var(--muted)">${q.reasoning || '—'}</div>
        </div>
        ${altsHtml}
        ${confidenceBar(q.confidence)}
      </div>
    `;
  }

  // ── Рендер всего результата ──────────────────────────────────────────────────
  function renderResult(data) {
    $('result').classList.remove('hidden');
    const pct = Math.round(data.overall_confidence * 100);
    $('overall-confidence').textContent = 'общая уверенность: ' + pct + '%';

    $('result-meta').style.display = 'grid';
    $('result-meta').style.gridTemplateColumns = '1fr 1fr';
    $('result-meta').style.gap = '8px';
    $('result-meta').innerHTML = `
      <div><span class="text-muted">Вид обращения:</span> <strong>${data.vid_obrascheniya}</strong></div>
      <div><span class="text-muted">Тип:</span> <strong>${data.tip_obrascheniya}</strong></div>
    `;

    $('needs-verification').classList.toggle('hidden', !data.needs_verification);
    $('questions').innerHTML = data.questions
      .map((q, i) => renderQuestion(q, i, data.questions.length))
      .join('');

    $('json-pre').textContent = JSON.stringify(data, null, 2);
    $('correct-form').classList.add('hidden');
    $('correct-code').value = '';
    $('result').scrollIntoView({ behavior: 'smooth', block: 'start' });
  }

  // ── Обработчик "Классифицировать" ────────────────────────────────────────────
  $('classify-btn').addEventListener('click', async () => {
    const fileInput = $('file-input');
    const textInput = $('appeal-text');

    $('classify-btn').disabled = true;
    $('loading').classList.remove('hidden');
    $('result').classList.add('hidden');

    try {
      let res;
      if (fileInput.files.length > 0) {
        const formData = new FormData();
        formData.append('file', fileInput.files[0]);
        res = await fetch('/classify/file', { method: 'POST', body: formData });
      } else {
        const text = textInput.value;
        if (!text || !text.trim()) {
          throw new Error('Введите текст обращения или загрузите файл');
        }
        res = await fetch('/classify', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ appeal_text: text }),
        });
      }

      if (!res.ok) {
        const err = await res.json().catch(() => ({}));
        throw new Error(err.detail || ('HTTP ' + res.status));
      }
      const data = await res.json();
      lastResult = data;
      lastLogId = data.log_id;
      renderResult(data);
    } catch(err) {
      toast('Ошибка классификации: ' + err.message, 'error');
    } finally {
      $('classify-btn').disabled = false;
      $('loading').classList.add('hidden');
    }
  });

  // ── Верификация: общий хелпер ────────────────────────────────────────────────
  async function postVerify(action, operatorCodes, annotation) {
    if (!lastLogId) {
      toast('Сначала выполните классификацию', 'error');
      return false;
    }
    const body = { log_id: lastLogId, action };
    if (operatorCodes) body.operator_codes = operatorCodes;
    if (annotation) body.annotation = annotation;

    try {
      const res = await fetch('/verify', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
      });
      if (!res.ok) {
        const err = await res.json().catch(() => ({}));
        throw new Error(err.detail || ('HTTP ' + res.status));
      }
      return true;
    } catch(err) {
      toast('Ошибка верификации: ' + err.message, 'error');
      return false;
    }
  }

  // ── Подтвердить ──────────────────────────────────────────────────────────────
  $('btn-confirm').addEventListener('click', async () => {
    const ok = await postVerify('confirm');
    if (ok) {
      toast('Подтверждено — записано в лог', 'success');
      $('result').classList.add('hidden');
      lastLogId = null;
    }
  });

  // ── Отклонить ────────────────────────────────────────────────────────────────
  $('btn-reject').addEventListener('click', async () => {
    const ok = await postVerify('reject');
    if (ok) {
      toast('Отклонено — записано в лог', 'success');
      $('result').classList.add('hidden');
      lastLogId = null;
    }
  });

  // ── Исправить: раскрыть форму ──────────────────────────────────────────────
  $('btn-correct').addEventListener('click', () => {
    $('correct-form').classList.toggle('hidden');
    $('correct-code').focus();
  });

  // ── Исправить: сохранить ─────────────────────────────────────────────────────
  $('btn-correct-save').addEventListener('click', async () => {
    const code = $('correct-code').value.trim();
    const annotation = $('correct-annotation').value.trim();

    if (!code) { toast('Введите код вопроса', 'error'); return; }
    if (annotation.length > 0 && annotation.length < 10) {
      $('annotation-hint').classList.remove('hidden');
      toast('Пояснение должно быть минимум 10 символов', 'error');
      return;
    }
    $('annotation-hint').classList.add('hidden');

    const ok = await postVerify('correct', [code], annotation);
    if (ok) {
      toast('Исправление сохранено — записано в лог', 'success');
      $('result').classList.add('hidden');
      $('correct-annotation').value = '';
      lastLogId = null;
    }
  });

  // ── Старт ───────────────────────────────────────────────────────────────────
  loadExamples();
  loadStats();

})();