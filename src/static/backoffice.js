// backoffice.js — бэк-офис: KPI, Chart.js, IP stats
// Fetches /api/backoffice/stats with HTTP Basic Auth and renders charts

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

  // ── Build auth header from page credentials (injected by server) ─────────
  // The backoffice.html embeds credentials in a meta tag or we prompt for them
  // For simplicity: read from localStorage or prompt user
  let authHeader = localStorage.getItem('bo_auth');
  if (!authHeader) {
    // Try to get from a data attribute on body
    const body = document.body;
    authHeader = body.dataset.boAuth || '';
  }

  function apiFetch(url) {
    return fetch(url, {
      headers: authHeader ? { 'Authorization': 'Basic ' + authHeader } : {},
    });
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

      if (res.status === 401) {
        // Prompt for credentials
        showAuthPrompt();
        return;
      }

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

  // ── Auth prompt ────────────────────────────────────────────────────────────
  function showAuthPrompt() {
    const overlay = document.createElement('div');
    overlay.style.cssText = 'position:fixed;inset:0;background:rgba(0,14,32,0.6);' +
      'display:flex;align-items:center;justify-content:center;z-index:9999';
    overlay.innerHTML = `
      <div style="background:#fff;border-radius:10px;padding:32px;width:360px;box-shadow:0 8px 32px rgba(1,12,28,0.2)">
        <h2 style="font-size:18px;font-weight:700;color:var(--title);margin-bottom:8px">Вход в бэк-офис</h2>
        <p style="font-size:13px;color:var(--muted);margin-bottom:20px">
          Введите учётные данные для доступа к статистике.
        </p>
        <input type="text" id="bo-user" placeholder="Логин"
          style="width:100%;margin-bottom:12px;padding:10px 12px;font-size:14px;
                 border:1px solid var(--border);border-radius:6px;box-sizing:border-box">
        <input type="password" id="bo-pass" placeholder="Пароль"
          style="width:100%;margin-bottom:16px;padding:10px 12px;font-size:14px;
                 border:1px solid var(--border);border-radius:6px;box-sizing:border-box">
        <button id="bo-login" style="width:100%;padding:10px;background:var(--orange);color:#fff;
          border:none;border-radius:6px;font-size:14px;font-weight:600;cursor:pointer">
          Войти
        </button>
        <p id="bo-error" class="hidden" style="color:var(--red);font-size:13px;margin-top:8px;text-align:center"></p>
      </div>
    `;
    document.body.appendChild(overlay);

    const loginBtn = document.getElementById('bo-login');
    const userInp = document.getElementById('bo-user');
    const passInp = document.getElementById('bo-pass');
    const errEl = document.getElementById('bo-error');

    function doLogin() {
      const user = userInp.value.trim();
      const pass = passInp.value;
      if (!user || !pass) {
        errEl.textContent = 'Заполните оба поля';
        errEl.classList.remove('hidden');
        return;
      }
      const creds = btoa(user + ':' + pass);
      authHeader = creds;
      localStorage.setItem('bo_auth', creds);
      overlay.remove();
      loadStats();
    }

    loginBtn.addEventListener('click', doLogin);
    passInp.addEventListener('keydown', e => { if (e.key === 'Enter') doLogin(); });
    userInp.focus();
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

  // ── Init ──────────────────────────────────────────────────────────────────
  loadStats();

})();