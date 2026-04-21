// app.js — классификация обращений граждан
// Tasks 8-10: загрузка примеров, классификация, верификация

// ── Состояние ───────────────────────────────────────────────────────────────
let lastResult = null;
let lastLogId = null;

// ── Утилиты ─────────────────────────────────────────────────────────────────
const $ = (id) => document.getElementById(id);

function toast(message, kind = "info") {
  const el = $("toast");
  el.textContent = message;
  el.className = "fixed bottom-4 right-4 px-4 py-2 rounded shadow-lg text-white " +
    (kind === "error" ? "bg-red-600" : kind === "success" ? "bg-green-600" : "bg-gray-900");
  el.classList.remove("hidden");
  setTimeout(() => el.classList.add("hidden"), 2500);
}

// ── Загрузка примеров ───────────────────────────────────────────────────────
async function loadExamples() {
  try {
    const res = await fetch("/examples");
    if (!res.ok) throw new Error("HTTP " + res.status);
    const data = await res.json();
    const select = $("examples-select");
    for (const ex of data.examples) {
      const opt = document.createElement("option");
      opt.value = ex.id;
      opt.textContent = ex.title;
      opt.dataset.text = ex.text;
      select.appendChild(opt);
    }
  } catch (err) {
    toast("Не удалось загрузить примеры: " + err.message, "error");
  }
}

// ── Подстановка текста при выборе примера ────────────────────────────────────
$("examples-select").addEventListener("change", (e) => {
  const opt = e.target.selectedOptions[0];
  if (opt && opt.dataset.text) {
    $("appeal-text").value = opt.dataset.text;
  }
});

// ── Цветовая шкала уверенности ───────────────────────────────────────────────
function confidenceBar(value) {
  const pct = Math.round(value * 100);
  const color =
    value >= 0.80 ? "bg-green-500" :
    value >= 0.65 ? "bg-yellow-500" :
                    "bg-red-500";
  return `
    <div class="w-full bg-gray-200 rounded h-2 overflow-hidden">
      <div class="${color} h-2" style="width: ${pct}%"></div>
    </div>
    <div class="text-xs text-gray-500 mt-1">Уверенность: ${pct}%</div>
  `;
}

// ── Рендер одного вопроса ────────────────────────────────────────────────────
function renderQuestion(q, idx, total) {
  const altsHtml = (q.alternatives || []).length
    ? `<div class="text-xs text-gray-500 mt-2">Альтернативы: ${
        q.alternatives.map(a => `<span class="font-mono">${a.code}</span>`).join(", ")
      }</div>`
    : "";
  const header = total > 1 ? `<div class="font-medium mb-2">Вопрос ${idx + 1} из ${total}</div>` : "";
  return `
    <div class="border rounded p-3">
      ${header}
      <div class="grid grid-cols-[140px_1fr] gap-y-1 text-sm">
        <div class="text-gray-500">Код:</div>          <div class="font-mono">${q.code}</div>
        <div class="text-gray-500">Тема:</div>         <div>${q.name}</div>
        <div class="text-gray-500">Путь:</div>         <div class="text-xs text-gray-600">${q.full_path}</div>
        <div class="text-gray-500">Ведение:</div>      <div>${q.predmet_vedeniya}</div>
        <div class="text-gray-500">Обоснование:</div>  <div class="text-gray-700">${q.reasoning || "—"}</div>
      </div>
      ${altsHtml}
      <div class="mt-3">${confidenceBar(q.confidence)}</div>
    </div>
  `;
}

// ── Рендер всего результата ──────────────────────────────────────────────────
function renderResult(data) {
  $("result").classList.remove("hidden");

  const overallPct = Math.round(data.overall_confidence * 100);
  $("overall-confidence").textContent = `общая уверенность: ${overallPct}%`;

  $("result-meta").innerHTML = `
    <div><span class="text-gray-500">Вид обращения:</span> <strong>${data.vid_obrascheniya}</strong></div>
    <div><span class="text-gray-500">Тип:</span> <strong>${data.tip_obrascheniya}</strong></div>
  `;

  $("needs-verification").classList.toggle("hidden", !data.needs_verification);

  $("questions").innerHTML = data.questions
    .map((q, i) => renderQuestion(q, i, data.questions.length))
    .join("");

  $("json-pre").textContent = JSON.stringify(data, null, 2);

  $("correct-form").classList.add("hidden");
  $("correct-code").value = "";

  $("result").scrollIntoView({ behavior: "smooth", block: "start" });
}

// ── Обработчик "Классифицировать" ────────────────────────────────────────────
$("classify-btn").addEventListener("click", async () => {
  const text = $("appeal-text").value.trim();
  if (text.length < 10) {
    toast("Введите текст обращения (минимум 10 символов)", "error");
    return;
  }

  $("classify-btn").disabled = true;
  $("loading").classList.remove("hidden");
  $("result").classList.add("hidden");

  try {
    const res = await fetch("/classify", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ appeal_text: text }),
    });
    if (!res.ok) {
      const err = await res.json().catch(() => ({}));
      throw new Error(err.detail || `HTTP ${res.status}`);
    }
    const data = await res.json();
    lastResult = data;
    lastLogId = data.log_id;
    renderResult(data);
  } catch (err) {
    toast("Ошибка классификации: " + err.message, "error");
  } finally {
    $("classify-btn").disabled = false;
    $("loading").classList.add("hidden");
  }
});

// ── Верификация: общий хелпер ────────────────────────────────────────────────
async function postVerify(action, operator_codes) {
  if (!lastLogId) {
    toast("Сначала выполните классификацию", "error");
    return;
  }
  const body = { log_id: lastLogId, action };
  if (operator_codes) body.operator_codes = operator_codes;

  try {
    const res = await fetch("/verify", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    if (!res.ok) {
      const err = await res.json().catch(() => ({}));
      throw new Error(err.detail || `HTTP ${res.status}`);
    }
    return true;
  } catch (err) {
    toast("Ошибка верификации: " + err.message, "error");
    return false;
  }
}

// ── Подтвердить ──────────────────────────────────────────────────────────────
$("btn-confirm").addEventListener("click", async () => {
  const ok = await postVerify("confirm");
  if (ok) {
    toast("✓ Подтверждено — записано в лог", "success");
    $("result").classList.add("hidden");
    lastLogId = null;
  }
});

// ── Отклонить ────────────────────────────────────────────────────────────────
$("btn-reject").addEventListener("click", async () => {
  const ok = await postVerify("reject");
  if (ok) {
    toast("✗ Отклонено — записано в лог", "success");
    $("result").classList.add("hidden");
    lastLogId = null;
  }
});

// ── Исправить: раскрыть форму ──────────────────────────────────────────────
$("btn-correct").addEventListener("click", () => {
  $("correct-form").classList.toggle("hidden");
  $("correct-code").focus();
});

// ── Исправить: сохранить ─────────────────────────────────────────────────────
$("btn-correct-save").addEventListener("click", async () => {
  const code = $("correct-code").value.trim();
  if (!code) {
    toast("Введите код вопроса", "error");
    return;
  }
  const ok = await postVerify("correct", [code]);
  if (ok) {
    toast("✎ Исправление сохранено — записано в лог", "success");
    $("result").classList.add("hidden");
    lastLogId = null;
  }
});

// ── Старт ───────────────────────────────────────────────────────────────────
loadExamples();