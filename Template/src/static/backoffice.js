/* =============================================================================
   ШАБЛОН БЭКОФИСА — загрузка и отображение метрик
   =============================================================================
   TEMPLATE: Адаптируй CASE_NAMES и renderCasesTable под кейсы проекта.
   ============================================================================= */

// TEMPLATE: Замени на реальные названия кейсов
const CASE_NAMES = {
  1: 'Кейс 1',
  2: 'Кейс 2',
  3: 'Кейс 3',
};

let timelineChart = null;
let casesChart = null;

async function loadMetrics() {
  const loading = document.getElementById('loading');
  const errorMsg = document.getElementById('error-msg');

  loading.classList.remove('hidden');
  errorMsg.classList.add('hidden');

  try {
    const resp = await fetch('/api/metrics');
    if (!resp.ok) throw new Error('HTTP ' + resp.status);
    const data = await resp.json();
    renderMetrics(data);
    loading.classList.add('hidden');
  } catch (e) {
    loading.classList.add('hidden');
    errorMsg.textContent = 'Ошибка загрузки метрик: ' + e.message;
    errorMsg.classList.remove('hidden');
  }
}

function renderMetrics(data) {
  document.getElementById('stat-requests').textContent = data.total_requests || 0;
  document.getElementById('stat-unique-ip').textContent = data.unique_ips || 0;

  const pct = data.total_positive_pct;
  document.getElementById('stat-positive-pct').textContent = pct != null ? pct + '%' : '—';

  const chatCount = data.chat_feedback_count;
  document.getElementById('stat-chat-count').textContent = chatCount != null ? chatCount : '—';

  const chatPct = data.chat_positive_pct;
  document.getElementById('stat-chat-pct').textContent = chatPct != null ? chatPct + '%' : '—';

  renderTimelineChart(data.timeline || []);
  renderCasesChart(data.case_stats || []);
  renderIpTable(data.ip_stats || []);
  renderCasesTable(data.case_stats || []);
}

function renderTimelineChart(timeline) {
  const canvas = document.getElementById('timeline-chart');
  const noData = document.getElementById('no-timeline');

  if (!timeline.length) {
    canvas.classList.add('hidden');
    noData.classList.remove('hidden');
    return;
  }

  // Fallback если chart.umd.min.js не загрузился
  if (typeof Chart === 'undefined') {
    canvas.classList.add('hidden');
    noData.textContent = timeline.map(r => `${r.date}: ${r.count}`).join(', ');
    noData.classList.remove('hidden');
    return;
  }

  canvas.classList.remove('hidden');
  noData.classList.add('hidden');

  if (timelineChart) timelineChart.destroy();
  timelineChart = new Chart(canvas, {
    type: 'line',
    data: {
      labels: timeline.map(r => r.date),
      datasets: [{
        label: 'Запросов в день',
        data: timeline.map(r => r.count),
        borderColor: '#0052CC',
        backgroundColor: 'rgba(0,82,204,0.1)',
        tension: 0.3,
        fill: true,
      }]
    },
    options: {
      responsive: true,
      plugins: { legend: { display: false } },
      scales: { y: { beginAtZero: true, ticks: { stepSize: 1 } } }
    }
  });
}

function renderCasesChart(caseStats) {
  const canvas = document.getElementById('cases-chart');
  const noData = document.getElementById('no-cases');

  const hasData = caseStats.some(c => c.requests > 0);
  if (!hasData) {
    canvas.classList.add('hidden');
    noData.classList.remove('hidden');
    return;
  }

  // Fallback если chart.umd.min.js не загрузился
  if (typeof Chart === 'undefined') {
    canvas.classList.add('hidden');
    noData.textContent = caseStats.map(c => `К${c.case_id}: ${c.requests}`).join(', ');
    noData.classList.remove('hidden');
    return;
  }

  canvas.classList.remove('hidden');
  noData.classList.add('hidden');

  if (casesChart) casesChart.destroy();
  casesChart = new Chart(canvas, {
    type: 'bar',
    data: {
      labels: caseStats.map(c => `К${c.case_id}`),
      datasets: [{
        label: 'Запусков',
        data: caseStats.map(c => c.requests),
        backgroundColor: '#0052CC',
        borderRadius: 4,
      }]
    },
    options: {
      responsive: true,
      plugins: {
        legend: { display: false },
        tooltip: {
          callbacks: {
            title: (items) => CASE_NAMES[parseInt(items[0].label.slice(1))] || items[0].label
          }
        }
      },
      scales: { y: { beginAtZero: true, ticks: { stepSize: 1 } } }
    }
  });
}

function renderIpTable(ipStats) {
  const container = document.getElementById('ip-table-container');
  if (!ipStats.length) {
    container.innerHTML = '<div class="no-data">Нет данных</div>';
    return;
  }

  const rows = ipStats.map((item, idx) => `
    <tr>
      <td>${idx + 1}</td>
      <td><code>${item.ip}</code></td>
      <td>${item.count}</td>
    </tr>
  `).join('');

  container.innerHTML = `
    <table>
      <thead><tr><th>#</th><th>IP-адрес</th><th>Запросов</th></tr></thead>
      <tbody>${rows}</tbody>
    </table>
  `;
}

// TEMPLATE: Адаптируй количество кейсов и критерии
function renderCasesTable(caseStats) {
  const container = document.getElementById('cases-table-container');

  const rows = caseStats.map(c => {
    const pct = c.pct_positive;
    let badge = '<span class="badge badge-gray">—</span>';
    if (pct != null) {
      badge = pct >= 70
        ? `<span class="badge badge-green">${pct}%</span>`
        : `<span class="badge badge-red">${pct}%</span>`;
    }

    const reqBadge = c.requests >= 5
      ? `<span class="badge badge-green">${c.requests}</span>`
      : `<span class="badge ${c.requests > 0 ? 'badge-red' : 'badge-gray'}">${c.requests}</span>`;

    const esc = s => String(s || '').replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
    let modelsBreakdown = '';
    if (c.models_breakdown && c.models_breakdown.length > 0) {
      const bdRows = c.models_breakdown.map(m =>
        `<tr><td style="padding:1px 6px">${esc(m.model)}</td><td style="padding:1px 6px;white-space:nowrap">${m.positive} 👍 / ${m.negative} 👎</td></tr>`
      ).join('');
      modelsBreakdown = `
        <details style="margin-top:4px;font-size:11px">
          <summary style="cursor:pointer;color:#0043A4">по моделям</summary>
          <table style="margin-top:4px;font-size:11px;width:auto">${bdRows}</table>
        </details>`;
    }

    return `
      <tr>
        <td>Кейс ${c.case_id}</td>
        <td>${CASE_NAMES[c.case_id] || '—'}</td>
        <td>${reqBadge}</td>
        <td>${c.positive} / ${c.negative}${modelsBreakdown}</td>
        <td>${badge}</td>
      </tr>
    `;
  }).join('');

  container.innerHTML = `
    <table>
      <thead>
        <tr>
          <th>Кейс</th>
          <th>Название</th>
          <th>Запусков</th>
          <th>Оценки (+/-)</th>
          <th>% положительных</th>
        </tr>
      </thead>
      <tbody>${rows}</tbody>
    </table>
  `;
}

async function loadChatFeedback() {
  const container = document.getElementById('chat-feedback-container');
  container.innerHTML = '<div class="no-data">Загрузка...</div>';

  try {
    const resp = await fetch('/api/metrics/chat-feedback?limit=50');
    if (!resp.ok) throw new Error('HTTP ' + resp.status);
    const data = await resp.json();
    renderChatFeedbackTable(data.items || []);
  } catch (e) {
    container.innerHTML = `<div class="no-data" style="color:#BF2600">Ошибка: ${e.message}</div>`;
  }
}

function renderChatFeedbackTable(items) {
  const container = document.getElementById('chat-feedback-container');
  if (!items.length) {
    container.innerHTML = '<div class="no-data">Нет данных</div>';
    return;
  }

  // Паттерн: summary (bold заголовок 2-4 слова) + <details> спойлер с полным вопросом.
  // summary генерируется LLM при голосовании и хранится в БД.
  // В чате ничего не меняется — пузырь вопроса остаётся как есть.
  const esc = (s) => String(s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');

  const rows = items.map(item => {
    const vote = item.vote === 1
      ? '<span class="badge badge-green">👍</span>'
      : '<span class="badge badge-red">👎</span>';
    const ts = item.timestamp ? item.timestamp.slice(0, 16).replace('T', ' ') : '—';
    const ctxLabel = item.context_type === 'item' ? 'элемент' : 'объект';

    // Summary (2-4 слова) как заголовок, полный вопрос под спойлером
    const titleText = item.summary || item.user_message.slice(0, 40) + (item.user_message.length > 40 ? '…' : '');
    const questionCell = `
      <span class="bo-question-title">${esc(titleText)}</span>
      <details class="bo-spoiler">
        <summary>показать вопрос</summary>
        <div class="bo-spoiler-body">${esc(item.user_message)}</div>
      </details>
    `;

    const modelCell = item.model_label
      ? `<span class="badge badge-gray" style="font-size:10px">${esc(item.model_label)}</span>`
      : '<span style="color:#B0B7C3;font-size:11px">—</span>';

    return `
      <tr>
        <td>${vote}</td>
        <td>${questionCell}</td>
        <td><span class="badge badge-gray">${ctxLabel}</span> ${esc(item.context_name || '—')}</td>
        <td style="font-size:12px">${modelCell}</td>
        <td style="white-space:nowrap;color:#6B778C;font-size:12px">${ts}</td>
      </tr>
    `;
  }).join('');

  container.innerHTML = `
    <table>
      <thead>
        <tr>
          <th>Оценка</th>
          <th>Вопрос</th>
          <th>Контекст</th>
          <th>Модель</th>
          <th>Время</th>
        </tr>
      </thead>
      <tbody>${rows}</tbody>
    </table>
  `;
}

document.addEventListener('DOMContentLoaded', () => {
  loadMetrics();
  loadChatFeedback();
  loadModels();
});

// ===== MODELS MANAGEMENT =====
// TEMPLATE: Используй как есть.

let _editingModelId = null;

async function loadModels() {
  const container = document.getElementById('models-container');
  try {
    const resp = await fetch('/api/admin/models', { credentials: 'include' });
    if (!resp.ok) throw new Error('HTTP ' + resp.status);
    const data = await resp.json();
    renderModelsTable(data.models || []);
  } catch (e) {
    container.innerHTML = `<div class="no-data">Ошибка загрузки: ${e.message}</div>`;
  }
}

function renderModelsTable(models) {
  const container = document.getElementById('models-container');
  if (!models.length) {
    container.innerHTML = '<div class="no-data">Пользовательских моделей нет. Используется модель из .env</div>';
    return;
  }
  const esc = s => String(s || '').replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
  const rows = models.map(m => `
    <tr>
      <td>${esc(m.display_name || m.name)}</td>
      <td><code>${esc(m.name)}</code></td>
      <td style="font-size:12px;color:#5E6C84">${esc(m.base_url || '—')}</td>
      <td>${m.has_token ? '<span class="badge badge-green">есть</span>' : '<span class="badge badge-gray">нет</span>'}</td>
      <td>
        <button class="icon-btn" title="Редактировать" onclick="openModelModal(${m.id})">✏️</button>
        <button class="icon-btn" title="Удалить" onclick="deleteModel(${m.id})">🗑️</button>
      </td>
    </tr>
  `).join('');
  container.innerHTML = `
    <table>
      <thead><tr>
        <th>Отображаемое имя</th><th>Название модели</th>
        <th>Base URL</th><th>Токен</th><th></th>
      </tr></thead>
      <tbody>${rows}</tbody>
    </table>
  `;
}

function openModelModal(modelId) {
  _editingModelId = modelId || null;
  document.getElementById('model-modal-title').textContent = modelId ? 'Редактировать модель' : 'Добавить модель';
  document.getElementById('model-name').value = '';
  document.getElementById('model-base-url').value = '';
  document.getElementById('model-token').value = '';
  document.getElementById('model-display-name').value = '';
  document.getElementById('model-token-hint').textContent = '';

  if (modelId) {
    fetch('/api/admin/models', { credentials: 'include' })
      .then(r => r.json())
      .then(data => {
        const m = (data.models || []).find(x => x.id === modelId);
        if (m) {
          document.getElementById('model-name').value = m.name || '';
          document.getElementById('model-base-url').value = m.base_url || '';
          document.getElementById('model-display-name').value = m.display_name || '';
          document.getElementById('model-token-hint').textContent = m.has_token
            ? 'Токен сохранён. Введите новый чтобы заменить, оставьте пустым чтобы не менять.'
            : '';
        }
      });
  }
  document.getElementById('model-modal').classList.add('open');
}

function closeModelModal() {
  document.getElementById('model-modal').classList.remove('open');
  _editingModelId = null;
}

async function saveModel() {
  const name = document.getElementById('model-name').value.trim();
  if (!name) { alert('Введите название модели'); return; }
  const base_url = document.getElementById('model-base-url').value.trim() || null;
  const tokenInput = document.getElementById('model-token').value;
  const token = _editingModelId
    ? (tokenInput.trim() !== '' ? tokenInput.trim() : undefined)
    : (tokenInput.trim() || null);
  const display_name = document.getElementById('model-display-name').value.trim() || null;

  const body = { name, base_url, display_name };
  if (token !== undefined) body.token = token;

  try {
    const url = _editingModelId ? `/api/admin/models/${_editingModelId}` : '/api/admin/models';
    const resp = await fetch(url, {
      method: _editingModelId ? 'PUT' : 'POST',
      credentials: 'include',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    });
    if (!resp.ok) {
      const err = await resp.json().catch(() => ({}));
      alert('Ошибка: ' + (err.detail || resp.status));
      return;
    }
    closeModelModal();
    loadModels();
  } catch (e) {
    alert('Ошибка: ' + e.message);
  }
}

async function deleteModel(modelId) {
  if (!confirm('Удалить модель?')) return;
  try {
    const resp = await fetch(`/api/admin/models/${modelId}`, {
      method: 'DELETE', credentials: 'include',
    });
    if (!resp.ok) {
      const err = await resp.json().catch(() => ({}));
      alert('Ошибка: ' + (err.detail || resp.status));
      return;
    }
    loadModels();
  } catch (e) {
    alert('Ошибка: ' + e.message);
  }
}

// Закрыть модал по клику на фон
document.addEventListener('DOMContentLoaded', () => {
  document.getElementById('model-modal').addEventListener('click', e => {
    if (e.target === e.currentTarget) closeModelModal();
  });
});
