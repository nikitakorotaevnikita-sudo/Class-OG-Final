/* =============================================================================
   УПРАВЛЕНИЕ ПРОМПТАМИ
   =============================================================================
   CRUD пользовательских промптов, редактирование системных промптов,
   скачивание отчёта (.docx).

   TEMPLATE: Адаптируй runCustomPrompt() — endpoint и тело запроса.
   Остальное используй как есть.

   Зависимости: state, appendChatMessage(), showThinkingInElement(),
   setInputDisabled(), updateCaseButtons(), saveChatToSession(),
   scrollChatToBottom(), readSSEStreamToElement(), getSelectedModelId(),
   getSelectedModelLabel() — всё из app.js.
   ============================================================================= */

// ===== СКАЧИВАНИЕ ОТЧЁТА (.docx) =====

/**
 * Добавляет кнопку «Скачать отчёт (.docx)» после элемента с ответом ассистента.
 * TEMPLATE: Вызывай после успешного SSE-стрима, если нужна выгрузка в Word.
 */
function appendDownloadButton(afterElement) {
  const btn = document.createElement('button');
  btn.className = 'download-icon-btn';
  btn.title = 'Скачать отчёт в формате Word (.docx)';
  btn.innerHTML = '💾 Скачать отчёт (.docx)';
  btn.onclick = () => downloadReportDocx(state.lastReportText);
  afterElement.parentElement.insertBefore(btn, afterElement.nextSibling);
  scrollChatToBottom();
}

/**
 * Отправляет markdown-текст на POST /api/report/download и скачивает .docx.
 * TEMPLATE: Адаптируй filename и поля тела запроса под проект.
 */
function downloadReportDocx(mdText) {
  if (!mdText) return;
  const contextName = state.selectedObjectName || 'Отчёт';
  const filename = `report_${contextName.replace(/[^а-яА-ЯёЁa-zA-Z0-9]/g, '_')}.docx`;

  fetch('/api/report/download', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      text: mdText,
      title: contextName,
    }),
  })
    .then(r => r.ok ? r.blob() : Promise.reject('Ошибка генерации'))
    .then(blob => {
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = filename;
      a.click();
      URL.revokeObjectURL(url);
    })
    .catch(e => alert('Ошибка скачивания: ' + e));
}

// ===== ПОЛЬЗОВАТЕЛЬСКИЕ ПРОМПТЫ =====

/**
 * Загружает пользовательские промпты из /api/prompts и обновляет кнопки.
 */
async function loadCustomPrompts() {
  try {
    const resp = await fetch('/api/prompts');
    if (!resp.ok) return;
    const data = await resp.json();
    state.customPrompts = data.prompts || [];
    renderCustomPromptButtons();
  } catch (e) { /* ignore */ }
}

/**
 * Рендерит кнопки пользовательских промптов перед кнопкой «+ Добавить промпт».
 * TEMPLATE: Адаптируй условие disabled под требования проекта.
 */
function renderCustomPromptButtons() {
  const bar = document.getElementById('case-buttons-bar');
  const addBtn = document.getElementById('btn-add-prompt');
  bar.querySelectorAll('[data-custom-prompt-id]').forEach(el => el.remove());

  for (const prompt of state.customPrompts) {
    const btn = document.createElement('button');
    btn.className = 'case-btn';
    btn.dataset.customPromptId = String(prompt.id);
    btn.dataset.mode = 'object';
    btn.title = prompt.name;
    btn.disabled = !state.selectedObjectId;
    btn.innerHTML = `
      ${escapeHtml(prompt.name)}
      <span class="prompt-edit-icon" title="Редактировать" onclick="event.stopPropagation();openPromptModal(${prompt.id})">🖊</span>
      <span class="prompt-del-icon" title="Удалить" onclick="event.stopPropagation();deletePrompt(${prompt.id})">✕</span>
    `;
    btn.addEventListener('click', () => runCustomPrompt(prompt));
    bar.insertBefore(btn, addBtn);
  }
}

function escapeHtml(s) {
  return String(s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
}

/**
 * Запускает произвольный пользовательский промпт через API.
 * TEMPLATE: Адаптируй endpoint и тело запроса под проект.
 */
async function runCustomPrompt(prompt) {
  if (!state.selectedObjectId) return;

  if (state.currentAbortController) state.currentAbortController.abort();
  state.currentAbortController = new AbortController();
  const signal = state.currentAbortController.signal;

  state.chatMessages = [];
  sessionStorage.removeItem('proto_chat');
  document.getElementById('chat-messages').innerHTML = '';
  state.lastReportText = '';

  const contextName = state.mode === 'item' ? state.selectedItemName : state.selectedObjectName;
  const userLabel = `${prompt.name}\n📋 ${contextName}`;
  state.chatMessages.push({ role: 'user', content: userLabel });
  appendChatMessage('user', userLabel);

  const assistantDiv = appendChatMessage('assistant', '');
  const stopThinking = showThinkingInElement(assistantDiv);

  setInputDisabled(true);

  try {
    // TEMPLATE: Замени endpoint и тело на реальный API-метод проекта
    const resp = await fetch('/api/cases/custom', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', 'X-Session-Id': state.sessionId },
      body: JSON.stringify({
        mode: state.mode,
        object_id: state.selectedObjectId,
        item_id: state.selectedItemId,
        custom_prompt: prompt.prompt_text,
      }),
      signal,
    });

    if (!resp.ok) {
      const errText = await resp.text();
      let detail = errText;
      try { detail = JSON.parse(errText).detail; } catch (e) { /* ignore */ }
      stopThinking();
      assistantDiv.innerHTML = `<p style="color:var(--color-danger)">Ошибка: ${detail || `HTTP ${resp.status}`}</p>`;
      return;
    }

    const fullText = await readSSEStreamToElement(resp, assistantDiv, signal, () => {
      stopThinking();
      assistantDiv.innerHTML = '';
    });

    state.chatMessages.push({ role: 'assistant', content: fullText });
    saveChatToSession();
    state.lastReportText = fullText;

    if (fullText && !fullText.startsWith('Ошибка:')) {
      appendDownloadButton(assistantDiv);
    }
    appendFeedbackBar(assistantDiv, 0);

  } catch (e) {
    stopThinking();
    if (e.name === 'AbortError') {
      assistantDiv.innerHTML = '<em style="color:var(--color-text-muted)">Прервано</em>';
      return;
    }
    assistantDiv.innerHTML = `<p style="color:var(--color-danger)">Ошибка: ${e.message}</p>`;
  } finally {
    setInputDisabled(false);
    updateCaseButtons();
  }
}

// ===== МОДАЛ: СИСТЕМНЫЙ ПРОМПТ =====

async function editSystemPrompt(promptId) {
  const id = promptId || 1;
  try {
    const resp = await fetch(`/api/prompts/system/${id}`);
    if (!resp.ok) throw new Error();
    const data = await resp.json();
    const ta = document.getElementById('system-prompt-text');
    ta.value = data.text;
    ta.dataset.defaultText = data.default_text;
    ta.dataset.promptId = String(id);
  } catch (e) {
    document.getElementById('system-prompt-text').value = '';
  }
  document.getElementById('system-prompt-modal').classList.remove('hidden');
}

function closeSystemPromptModal(event) {
  if (event && event.target !== document.getElementById('system-prompt-modal')) return;
  document.getElementById('system-prompt-modal').classList.add('hidden');
}

async function saveSystemPrompt() {
  const ta = document.getElementById('system-prompt-text');
  const text = ta.value.trim();
  if (!text) { alert('Текст промпта не может быть пустым'); return; }
  const id = ta.dataset.promptId || '1';
  try {
    const resp = await fetch(`/api/prompts/system/${id}`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ prompt_text: text }),
    });
    if (!resp.ok) throw new Error('Ошибка сохранения');
    document.getElementById('system-prompt-modal').classList.add('hidden');
  } catch (e) {
    alert('Ошибка: ' + e.message);
  }
}

async function resetSystemPrompt() {
  const ta = document.getElementById('system-prompt-text');
  const defaultText = ta.dataset.defaultText;
  if (defaultText) {
    ta.value = defaultText;
    return;
  }
  const id = ta.dataset.promptId || '1';
  try {
    await fetch(`/api/prompts/system/${id}`, { method: 'DELETE' });
    const resp = await fetch(`/api/prompts/system/${id}`);
    if (resp.ok) {
      const data = await resp.json();
      ta.value = data.default_text;
    }
  } catch (e) { /* ignore */ }
}

// ===== МОДАЛ: ПОЛЬЗОВАТЕЛЬСКИЙ ПРОМПТ =====

function openPromptModal(promptId) {
  const title = document.getElementById('custom-prompt-modal-title');
  const editId = document.getElementById('custom-prompt-edit-id');
  const nameInput = document.getElementById('custom-prompt-name');
  const textArea = document.getElementById('custom-prompt-text');

  if (promptId) {
    const prompt = state.customPrompts.find(p => p.id === promptId);
    if (!prompt) return;
    title.textContent = 'Редактировать промпт';
    editId.value = String(promptId);
    nameInput.value = prompt.name;
    textArea.value = prompt.prompt_text;
  } else {
    title.textContent = 'Новый промпт';
    editId.value = '';
    nameInput.value = '';
    textArea.value = '';
  }
  document.getElementById('custom-prompt-modal').classList.remove('hidden');
}

function closePromptModal(event) {
  if (event && event.target !== document.getElementById('custom-prompt-modal')) return;
  document.getElementById('custom-prompt-modal').classList.add('hidden');
}

async function savePrompt() {
  const editId = document.getElementById('custom-prompt-edit-id').value;
  const name = document.getElementById('custom-prompt-name').value.trim();
  const text = document.getElementById('custom-prompt-text').value.trim();
  if (!name || !text) { alert('Заполните название и текст промпта'); return; }

  const url = editId ? `/api/prompts/${editId}` : '/api/prompts';
  const method = editId ? 'PUT' : 'POST';

  try {
    const resp = await fetch(url, {
      method,
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ name, prompt_text: text }),
    });
    if (!resp.ok) throw new Error('Ошибка сохранения');
    document.getElementById('custom-prompt-modal').classList.add('hidden');
    await loadCustomPrompts();
  } catch (e) {
    alert('Ошибка: ' + e.message);
  }
}

async function deletePrompt(promptId) {
  if (!confirm('Удалить этот промпт?')) return;
  try {
    const resp = await fetch(`/api/prompts/${promptId}`, { method: 'DELETE' });
    if (!resp.ok) throw new Error('Ошибка удаления');
    await loadCustomPrompts();
  } catch (e) {
    alert('Ошибка: ' + e.message);
  }
}
