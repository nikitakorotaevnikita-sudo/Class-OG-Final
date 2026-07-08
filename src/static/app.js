// app.js — интеллектуальная классификация обращений граждан

(function() {
  'use strict';

  let lastLogId = null;
  let llmOptions = [];

  const $ = (id) => document.getElementById(id);

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
    el._tid = setTimeout(() => el.classList.add('hidden'), 2800);
  }

  function setSideResult(lines) {
    const box = $('side-results');
    if (!box) return;
    box.innerHTML = lines.map((line) => `<div class="result-card">${esc(line)}</div>`).join('');
  }

  function selectedProviderConfig() {
    const provider = $('llm-provider')?.value || '';
    return llmOptions.find((item) => item.id === provider) || null;
  }

  function updateModelDatalist() {
    const config = selectedProviderConfig();
    const datalist = $('llm-models');
    if (!datalist || !config) return;
    datalist.innerHTML = '';
    for (const model of config.models || []) {
      const opt = document.createElement('option');
      opt.value = model;
      datalist.appendChild(opt);
    }
    if (!$('llm-model').value.trim()) {
      $('llm-model').value = config.default_model || '';
    }
    $('llm-status').textContent = `LLM: ${config.label} · ${$('llm-model').value}`;
  }

  function getSelectedLLM() {
    return {
      llm_provider: $('llm-provider').value,
      llm_model: $('llm-model').value.trim(),
    };
  }

  async function loadHealth() {
    try {
      const res = await fetch('/health');
      const data = await res.json();
      const status = $('status');
      if (status) {
        status.textContent = data.agent_ready
          ? `online · ${data.classifier_entries} записей`
          : 'startup';
      }
    } catch (error) {
      const status = $('status');
      if (status) status.textContent = 'offline';
    }
  }

  async function loadStats() {
    try {
      const res = await fetch('/stats');
      if (!res.ok) return;
      const data = await res.json();
      setSideResult([
        `Верифицировано: ${data.verified}/${data.threshold}`,
        `Ожидает проверки: ${data.pending}`,
      ]);
      if (data.verified >= data.threshold) {
        toast('Порог дообучения достигнут', 'success');
      }
    } catch (error) {}
  }

  async function loadLlmOptions() {
    try {
      const res = await fetch('/api/llm/options');
      if (!res.ok) throw new Error('HTTP ' + res.status);
      const data = await res.json();
      llmOptions = data.providers || [];

      const providerSelect = $('llm-provider');
      providerSelect.innerHTML = '';
      for (const provider of llmOptions) {
        const opt = document.createElement('option');
        opt.value = provider.id;
        opt.textContent = provider.label;
        providerSelect.appendChild(opt);
      }
      providerSelect.value = data.current_provider || llmOptions[0]?.id || 'ario';
      $('llm-model').value = selectedProviderConfig()?.default_model || '';
      updateModelDatalist();
    } catch (error) {
      $('llm-status').textContent = 'LLM: настройки недоступны';
      toast('Не удалось загрузить список LLM: ' + error.message, 'error');
    }
  }

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
    } catch(error) {
      toast('Не удалось загрузить примеры: ' + error.message, 'error');
    }
  }

  function confidenceBar(value) {
    const pct = Math.round((Number(value) || 0) * 100);
    const fillCls = value >= 0.80 ? 'conf-fill-green' :
                    value >= 0.65 ? 'conf-fill-amber' : 'conf-fill-red';
    return `
      <div class="q-confidence">
        <div class="conf-track"><div class="conf-fill ${fillCls}" style="width:${pct}%"></div></div>
        <div class="q-confidence-label">Уверенность: ${pct}%</div>
      </div>
    `;
  }

  function metaItem(label, value) {
    return `
      <div class="meta-item">
        <span class="meta-label">${esc(label)}</span>
        <strong>${esc(value || '-')}</strong>
      </div>
    `;
  }

  function altBadgeClass(conf) {
    if (conf == null) return '';
    return conf >= 0.80 ? 'badge-green' : conf >= 0.65 ? 'badge-amber' : 'badge-red';
  }

  function renderQuestion(q, idx, total) {
    const alternatives = q.alternatives || [];
    const altsHtml = alternatives.length
      ? `<div class="q-alts">
          <div class="q-alts-label">Альтернативы (топ-${alternatives.length})</div>
          <div class="q-alts-list">
            ${alternatives.map((a) => {
              const conf = typeof a.confidence === 'number' ? Math.round(a.confidence * 100) : null;
              const badge = conf != null
                ? `<span class="badge ${altBadgeClass(a.confidence)}">${conf}%</span>`
                : '';
              return `
                <div class="q-alt-item">
                  <span class="code">${esc(a.code)}</span>
                  <span class="q-alt-name">${esc(a.name || a.full_path || '')}</span>
                  ${badge}
                </div>
              `;
            }).join('')}
          </div>
        </div>`
      : '';
    return `
      <article class="q-block">
        <div class="q-block-head">
          <strong>${total > 1 ? `Вопрос ${idx + 1} из ${total}` : 'Вопрос'}</strong>
        </div>
        <div class="q-meta">
          <div class="lbl">Код:</div>
          <div class="val code">${esc(q.code)}</div>
          <div class="lbl">Тема:</div>
          <div class="val">${esc(q.name)}</div>
          <div class="lbl">Путь:</div>
          <div class="val q-path">${esc(q.full_path)}</div>
          <div class="lbl">Ведение:</div>
          <div class="val">${esc(q.predmet_vedeniya)}</div>
          <div class="lbl">Обоснование:</div>
          <div class="val q-reasoning">${esc(q.reasoning || '-')}</div>
        </div>
        ${altsHtml}
        ${confidenceBar(q.confidence)}
      </article>
    `;
  }

  function renderResult(data) {
    $('result').classList.remove('hidden');
    const pct = Math.round((data.overall_confidence || 0) * 100);
    $('overall-confidence').textContent = `общая уверенность: ${pct}%`;
    $('result-meta').innerHTML = [
      metaItem('Вид обращения', data.vid_obrascheniya),
      metaItem('Тип', data.tip_obrascheniya),
      metaItem('LLM provider', data.llm_provider),
      metaItem('LLM model', data.llm_model),
    ].join('');
    $('needs-verification').classList.toggle('hidden', !data.needs_verification);
    $('questions').innerHTML = (data.questions || [])
      .map((q, i) => renderQuestion(q, i, data.questions.length))
      .join('');
    $('json-pre').textContent = JSON.stringify(data, null, 2);
    $('correct-form').classList.add('hidden');
    $('correct-code').value = '';
    $('correct-annotation').value = '';
    setSideResult([
      `Результат: ${data.vid_obrascheniya || '-'}`,
      `Уверенность: ${pct}%`,
      `Модель: ${data.llm_provider || '-'} · ${data.llm_model || '-'}`,
    ]);
  }

  async function classify() {
    const fileInput = $('file-input');
    const text = $('appeal-text').value;
    const llm = getSelectedLLM();

    $('classify-btn').disabled = true;
    $('loading').classList.remove('hidden');
    $('result').classList.add('hidden');

    try {
      let res;
      if (fileInput.files.length > 0) {
        const formData = new FormData();
        formData.append('file', fileInput.files[0]);
        formData.append('llm_provider', llm.llm_provider);
        formData.append('llm_model', llm.llm_model);
        res = await fetch('/classify/file', { method: 'POST', body: formData });
      } else {
        if (!text || !text.trim()) {
          throw new Error('Введите текст обращения или загрузите файл');
        }
        res = await fetch('/classify', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ appeal_text: text, ...llm }),
        });
      }

      if (!res.ok) {
        const err = await res.json().catch(() => ({}));
        throw new Error(err.detail || ('HTTP ' + res.status));
      }
      const data = await res.json();
      lastLogId = data.log_id;
      renderResult(data);
    } catch(error) {
      toast('Ошибка классификации: ' + error.message, 'error');
      setSideResult(['Классификация не выполнена', error.message]);
    } finally {
      $('classify-btn').disabled = false;
      $('loading').classList.add('hidden');
    }
  }

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
    } catch(error) {
      toast('Ошибка верификации: ' + error.message, 'error');
      return false;
    }
  }

  $('examples-select').addEventListener('change', (event) => {
    const opt = event.target.selectedOptions[0];
    if (opt?.dataset.text) {
      $('appeal-text').value = opt.dataset.text;
      $('file-input').value = '';
    }
  });

  $('llm-provider').addEventListener('change', () => {
    $('llm-model').value = selectedProviderConfig()?.default_model || '';
    updateModelDatalist();
  });

  $('llm-model').addEventListener('input', updateModelDatalist);

  $('clear-btn').addEventListener('click', () => {
    $('examples-select').value = '';
    $('file-input').value = '';
    $('appeal-text').value = '';
    $('result').classList.add('hidden');
    lastLogId = null;
    setSideResult(['Готов к классификации']);
  });

  $('classify-btn').addEventListener('click', classify);

  $('btn-confirm').addEventListener('click', async () => {
    if (await postVerify('confirm')) {
      toast('Подтверждено', 'success');
      $('result').classList.add('hidden');
      lastLogId = null;
      loadStats();
    }
  });

  $('btn-reject').addEventListener('click', async () => {
    if (await postVerify('reject')) {
      toast('Отклонено', 'success');
      $('result').classList.add('hidden');
      lastLogId = null;
      loadStats();
    }
  });

  $('btn-correct').addEventListener('click', () => {
    $('correct-form').classList.toggle('hidden');
    $('correct-code').focus();
  });

  $('btn-correct-save').addEventListener('click', async () => {
    const code = $('correct-code').value.trim();
    const annotation = $('correct-annotation').value.trim();
    if (!code) {
      toast('Введите код вопроса', 'error');
      return;
    }
    if (annotation.length > 0 && annotation.length < 10) {
      $('annotation-hint').classList.remove('hidden');
      toast('Пояснение должно быть минимум 10 символов', 'error');
      return;
    }
    $('annotation-hint').classList.add('hidden');
    if (await postVerify('correct', [code], annotation)) {
      toast('Исправление сохранено', 'success');
      $('result').classList.add('hidden');
      lastLogId = null;
      loadStats();
    }
  });

  loadHealth();
  loadLlmOptions();
  loadExamples();
  loadStats();
})();
