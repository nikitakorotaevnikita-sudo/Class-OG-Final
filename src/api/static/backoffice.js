// backoffice.js — бэк-офис: KPI, Chart.js, IP stats

(function() {
  'use strict';

  // ── Chart.js global defaults ─────────────────────────────────────────────
  Chart.defaults.color = '#625F6A';
  Chart.defaults.borderColor = '#E0E0E0';
  Chart.defaults.font.family = "'Segoe UI', system-ui, sans-serif";

  const COLORS = {
    orange: '#FF7A00',
    blue:   '#3C65CC',
    green:  '#3AC436',
    red:    '#D32F2F',
    amber:  '#F5A623',
    purple: '#7C3AED',
  };

  function apiFetch(url) {
    return fetch(url);
  }

  // ── KPI helpers ───────────────────────────────────────────────────────────
  function setKPI(id, value, subId, subText) {
    const el = document.getElementById(id);
    if (el) el.textContent = value;
    const subEl = document.getElementById(subId);
    if (subEl) subEl.textContent = subText;
  }

  // ── Render charts ─────────────────────────────────────────────────────────
  let chartConfidence, chartTopCodes, chartDaily;

  function renderConfidenceHistogram(data) {
    const ctx = document.getElementById('chart-confidence').getContext('2d');
    const labels = Object.keys(data.confidence_histogram || {});
    const values = labels.map(l => data.confidence_histogram[l]);

    if (chartConfidence) chartConfidence.destroy();
    chartConfidence = new Chart(ctx, {
      type: 'bar',
      data: {
        labels: labels,
        datasets: [{
          label: 'Число обращений',
          data: values,
          backgroundColor: COLORS.orange,
          borderRadius: 4,
        }]
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        plugins: {
          title: { display: false },
          legend: { display: false },
        },
        scales: {
          x: {
            title: { display: true, text: 'Confidence', font: { size: 11 } },
            grid: { display: false },
          },
          y: {
            title: { display: true, text: 'Кол-во', font: { size: 11 } },
            beginAtZero: true,
          }
        }
      }
    });
  }

  function renderTopCodes(data) {
    const ctx = document.getElementById('chart-top-codes').getContext('2d');
    const codes = (data.top_codes || []).slice(0, 10);
    const labels = codes.map(c => {
      const name = c.name || '';
      return name.length > 28 ? name.substring(0, 28) + '…' : name;
    });
    const values = codes.map(c => c.count || 0);

    if (chartTopCodes) chartTopCodes.destroy();
    chartTopCodes = new Chart(ctx, {
      type: 'bar',
      data: {
        labels: labels,
        datasets: [{
          label: 'Использований',
          data: values,
          backgroundColor: COLORS.blue,
          borderRadius: 4,
        }]
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        indexAxis: 'y',
        plugins: {
          title: { display: false },
          legend: { display: false },
        },
        scales: {
          x: {
            title: { display: true, text: 'Использований', font: { size: 11 } },
            beginAtZero: true,
          },
          y: {
            title: { display: true, text: 'Код', font: { size: 11 } },
            grid: { display: false },
          }
        }
      }
    });
  }

  function renderDailyUsage(data) {
    const ctx = document.getElementById('chart-daily').getContext('2d');
    const daily = data.daily_usage || [];
    const labels = daily.map(d => d.date || '');
    const values = daily.map(d => d.count || 0);

    if (chartDaily) chartDaily.destroy();
    chartDaily = new Chart(ctx, {
      type: 'line',
      data: {
        labels: labels,
        datasets: [{
          label: 'Классификаций за день',
          data: values,
          borderColor: COLORS.orange,
          backgroundColor: 'rgba(255,122,0,0.1)',
          fill: true,
          tension: 0.3,
          pointRadius: 3,
        }]
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        plugins: {
          title: { display: false },
          legend: { display: false },
        },
        scales: {
          x: {
            title: { display: true, text: 'Дата', font: { size: 11 } },
            grid: { display: false },
          },
          y: {
            title: { display: true, text: 'Кол-во', font: { size: 11 } },
            beginAtZero: true,
          }
        }
      }
    });
  }

  function renderIPTable(data) {
    const tbody = document.getElementById('ip-stats-body');
    const ips = data.ip_stats || [];
    if (ips.length === 0) {
      tbody.innerHTML = '<tr><td colspan="2" style="text-align:center;color:var(--subtle);padding:20px">Нет данных</td></tr>';
      return;
    }
    tbody.innerHTML = ips.map(ip => `
      <tr>
        <td style="font-family:monospace;font-size:13px">${ip.ip}</td>
        <td style="text-align:center;font-weight:600">${ip.count}</td>
      </tr>
    `).join('');
  }

  // ── Main load ─────────────────────────────────────────────────────────────
  async function loadStats() {
    try {
      const res = await apiFetch('/api/backoffice/stats');

      if (!res.ok) {
        showError('Ошибка загрузки: HTTP ' + res.status);
        return;
      }

      const data = await res.json();

      // KPI cards
      const total = data.total_classifications || 0;
      const verified = data.total_verifications || 0;
      const confirmed = data.confirmed || 0;
      const confirmedRate = verified > 0
        ? Math.round(confirmed / verified * 100) + '%'
        : '—';
      const avgConf = data.avg_confidence != null
        ? Math.round(data.avg_confidence * 100) + '%'
        : '—';

      setKPI('kpi-total', total, 'kpi-total-sub', 'классификаций');
      setKPI('kpi-verified', verified, 'kpi-verified-sub', confirmed + ' подтверждено + ' + (data.corrected || 0) + ' исправлено');
      setKPI('kpi-confirmed-rate', confirmedRate, 'kpi-confirmed-sub', verified + ' верификаций');
      setKPI('kpi-avg-confidence', avgConf, 'kpi-avg-sub', 'средняя уверенность модели');

      // Charts
      if (data.confidence_histogram) {
        renderConfidenceHistogram(data);
      }
      if (data.top_codes && data.top_codes.length > 0) {
        renderTopCodes(data);
      }
      if (data.daily_usage && data.daily_usage.length > 0) {
        renderDailyUsage(data);
      }
      renderIPTable(data);

    } catch(err) {
      showError('Ошибка загрузки данных: ' + err.message);
    }
  }

  // ── Error display ─────────────────────────────────────────────────────────
  function showError(msg) {
    const el = document.createElement('div');
    el.className = 'alert alert-red';
    el.textContent = msg;
    el.style.position = 'fixed';
    el.style.top = '70px';
    el.style.right = '16px';
    el.style.zIndex = '999';
    el.style.maxWidth = '300px';
    document.body.appendChild(el);
    setTimeout(() => el.remove(), 5000);
  }

  // ── Модели (реестр версий) ────────────────────────────────────────────────
  function esc(value) {
    return String(value ?? '')
      .replaceAll('&', '&amp;')
      .replaceAll('<', '&lt;')
      .replaceAll('>', '&gt;')
      .replaceAll('"', '&quot;');
  }

  function fmtRecall(v) {
    return v == null ? '—' : (Math.round(v * 1000) / 10) + '%';
  }

  function renderModels(models) {
    const tbody = document.getElementById('models-body');
    if (!tbody) return;
    if (!models.length) {
      tbody.innerHTML = '<tr><td colspan="6" class="empty-cell">Нет зарегистрированных версий</td></tr>';
      return;
    }
    tbody.innerHTML = models.map(m => `
      <tr>
        <td style="text-align:center;font-weight:600">${esc(m.version)}</td>
        <td style="font-family:monospace;font-size:12px">${esc(m.model_path || m.base_model_name)}</td>
        <td>${fmtRecall(m.recall5_before)} / ${fmtRecall(m.recall5_after)}</td>
        <td style="text-align:center">${m.training_samples_count ?? '—'}</td>
        <td>${m.trained_at ? esc(m.trained_at.substring(0, 16).replace('T', ' ')) : '—'}</td>
        <td>${m.is_active
          ? '<span class="badge badge-green">Активна</span>'
          : `<button class="btn btn-ghost btn-sm" type="button" data-activate="${esc(m.version)}">Активировать</button>`}</td>
      </tr>
    `).join('');
  }

  async function loadModels() {
    try {
      const res = await apiFetch('/api/models');
      if (!res.ok) throw new Error('HTTP ' + res.status);
      renderModels(await res.json());
    } catch (err) {
      const tbody = document.getElementById('models-body');
      if (tbody) tbody.innerHTML = '<tr><td colspan="6" class="empty-cell">Не удалось загрузить: ' + esc(err.message) + '</td></tr>';
    }
  }

  async function activateModel(version, btn) {
    if (!confirm('Активировать версию модели ' + version + '?')) return;
    btn.disabled = true;
    try {
      // Basic Auth: при 401 браузер сам запросит логин бэк-офиса и повторит запрос.
      const res = await fetch('/api/models/' + encodeURIComponent(version) + '/activate', {
        method: 'POST',
        credentials: 'same-origin',
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(data.detail || ('HTTP ' + res.status));
      await loadModels();
    } catch (err) {
      showError('Активация не выполнена: ' + err.message);
      btn.disabled = false;
    }
  }

  document.addEventListener('click', (e) => {
    const btn = e.target.closest('[data-activate]');
    if (btn) activateModel(btn.getAttribute('data-activate'), btn);
  });

  // ── Init ──────────────────────────────────────────────────────────────────
  loadStats();
  loadModels();

})();
