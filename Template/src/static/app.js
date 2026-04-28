/* =============================================================================
   ШАБЛОН ФРОНТЕНД-ЛОГИКИ ПРОТОТИПА
   =============================================================================
   Содержит: state management, SSE streaming, markdown renderer, chat, feedback,
   searchable combobox, thinking indicator.

   TEMPLATE: Адаптируй state, loadObjects(), selectObject(), CASE_NAMES,
   THINKING_MESSAGES под объекты и кейсы проекта. Базовый код (SSE, markdown,
   feedback, combobox) — используй как есть.

   IMPORTANT: НЕ подключай marked.js и другие CDN-библиотеки — используй
   встроенный renderMarkdown(). В Docker без интернета CDN не загрузится.
   ============================================================================= */

// TEMPLATE: Замени на реальные названия кейсов проекта
const CASE_NAMES = {
  1: 'Кейс 1',
  2: 'Кейс 2',
  3: 'Кейс 3',
};

// TEMPLATE: Замени на сообщения, отражающие реальные этапы обработки в проекте
const THINKING_MESSAGES = [
  'Загружаю данные...',
  'Анализирую...',
  'Формирую ответ...',
  'Генерирую результат...',
];

// TEMPLATE: Адаптируй state под объекты проекта.
const state = {
  selectedObjectId: null,       // ID выбранного основного объекта
  selectedObjectContext: null,   // Текстовый контекст объекта (строка для LLM)
  selectedObjectName: '',        // Отображаемое имя объекта
  selectedItemId: null,          // ID выбранного дочернего элемента (опционально)
  selectedItemContext: null,     // Контекст дочернего элемента
  selectedItemName: '',          // Имя дочернего элемента
  mode: null,                    // 'object' | 'item' — режим работы
  chatMessages: [],
  currentAbortController: null,
  sessionId: null,
  allObjects: [],                // Все загруженные объекты
  customPrompts: [],             // Пользовательские промпты (загружаются из /api/prompts)
  lastReportText: '',            // Последний ответ ассистента (для скачивания .docx)
};

// ===== MODEL SELECTION =====
// TEMPLATE: Используй как есть. getSelectedModelId() возвращает id модели
// для передачи в /api/cases и /api/chat (model_id: 0 = модель из .env).

function getSelectedModelId() {
  const sel = document.getElementById('model-select');
  const val = sel ? parseInt(sel.value) : 0;
  return isNaN(val) ? 0 : val;
}

function getSelectedModelLabel() {
  const sel = document.getElementById('model-select');
  if (!sel) return null;
  const opt = sel.options[sel.selectedIndex];
  return opt ? opt.textContent : null;
}

function onModelSelectChange() {
  localStorage.setItem('proto_selected_model_id', String(getSelectedModelId()));
}

async function loadModels() {
  try {
    const resp = await fetch('/api/models');
    if (!resp.ok) return;
    const data = await resp.json();
    const sel = document.getElementById('model-select');
    if (!sel) return;
    // TEMPLATE: Замени ключ 'proto_selected_model_id' на уникальный для проекта
    const savedId = parseInt(localStorage.getItem('proto_selected_model_id') || '0') || 0;
    sel.innerHTML = '';
    for (const m of data.models) {
      const opt = document.createElement('option');
      opt.value = m.id;
      opt.textContent = m.label;
      sel.appendChild(opt);
    }
    // Восстанавливаем выбор; если модель удалена — сбрасываем на дефолт
    const savedOpt = Array.from(sel.options).find(o => parseInt(o.value) === savedId);
    if (savedOpt) {
      sel.value = String(savedId);
    } else if (savedId !== 0) {
      sel.value = '0';
      localStorage.setItem('proto_selected_model_id', '0');
    }
  } catch (e) {
    // Если API недоступен — оставляем заглушку
  }
}

// ===== SESSION =====

function initSession() {
  // TEMPLATE: Замени ключ 'proto_session_id' на уникальный для проекта
  let sid = sessionStorage.getItem('proto_session_id');
  if (!sid) {
    sid = 'sess_' + Date.now() + '_' + Math.random().toString(36).slice(2);
    sessionStorage.setItem('proto_session_id', sid);
  }
  state.sessionId = sid;

  // TEMPLATE: Замени ключ 'proto_chat' на уникальный для проекта
  const savedMessages = sessionStorage.getItem('proto_chat');
  if (savedMessages) {
    try {
      state.chatMessages = JSON.parse(savedMessages);
      restoreChatMessages();
    } catch (e) { /* ignore */ }
  }
}

// ===== THINKING INDICATOR =====
// Показывает анимированные статусные сообщения пока LLM генерирует ответ.
// Скрывается только при получении первого SSE chunk (не при response.ok).

function showThinkingInElement(el) {
  let i = 0;
  el.innerHTML = `<div class="thinking-indicator"><span class="spinner"></span><span class="thinking-text">${THINKING_MESSAGES[0]}</span></div>`;
  const textEl = el.querySelector('.thinking-text');
  const iv = setInterval(() => {
    i = (i + 1) % THINKING_MESSAGES.length;
    if (textEl.isConnected) textEl.textContent = THINKING_MESSAGES[i];
  }, 2000);
  // Возвращает функцию для остановки анимации
  return () => clearInterval(iv);
}

// ===== SEARCHABLE COMBOBOX =====
// Заменяет нативный <select> при большом числе объектов (>20).
// Паттерн: input + dropdown с фильтрацией. blur-скрытие с задержкой 150ms,
// чтобы mousedown на опции успел сработать раньше blur.
// TEMPLATE: Используй эти функции как есть, адаптируй только populateObjectsDropdown().

function openCombobox() {
  document.getElementById('objects-dropdown').classList.add('open');
}

function closeComboboxDelayed() {
  setTimeout(() => {
    document.getElementById('objects-dropdown').classList.remove('open');
    // Восстановить имя выбранного объекта если что-то было выбрано
    const input = document.getElementById('objects-search');
    if (state.selectedObjectId) {
      input.value = state.selectedObjectName;
    } else {
      input.value = '';
    }
  }, 150);
}

function filterCombobox() {
  const query = document.getElementById('objects-search').value.toLowerCase();
  const filtered = query
    ? state.allObjects.filter(o => o.name.toLowerCase().includes(query))
    : state.allObjects;
  renderComboboxItems(filtered);
  openCombobox();
}

function renderComboboxItems(objects) {
  const dropdown = document.getElementById('objects-dropdown');
  dropdown.innerHTML = '';
  if (!objects.length) {
    dropdown.innerHTML = '<div class="combobox-empty">Ничего не найдено</div>';
    return;
  }
  for (const obj of objects) {
    const div = document.createElement('div');
    div.className = 'combobox-option';
    // TEMPLATE: Адаптируй отображаемые поля объекта (obj.name, obj.prefix и т.п.)
    div.textContent = obj.name;
    div.dataset.id = obj.id;
    // preventDefault предотвращает срабатывание blur до click
    div.addEventListener('mousedown', (e) => {
      e.preventDefault();
      selectObjectFromCombobox(obj.id, obj.name);
    });
    dropdown.appendChild(div);
  }
}

function selectObjectFromCombobox(objectId, objectName) {
  document.getElementById('objects-search').value = objectName;
  document.getElementById('objects-dropdown').classList.remove('open');
  // TEMPLATE: Замени ключ на уникальный для проекта
  localStorage.setItem('proto_selected_object_id', String(objectId));
  localStorage.setItem('proto_selected_object_name', objectName);
  selectObject(objectId, objectName);
}

// ===== API: OBJECTS =====
// TEMPLATE: Адаптируй эти функции под API проекта.

async function loadObjects() {
  const searchInput = document.getElementById('objects-search');
  if (searchInput) searchInput.placeholder = 'Загрузка...';

  try {
    const resp = await fetch('/api/objects', {
      headers: { 'X-Session-Id': state.sessionId }
    });
    if (!resp.ok) throw new Error('Ошибка загрузки объектов');

    const data = await resp.json();

    if (data.error) {
      if (searchInput) searchInput.placeholder = data.error;
      return;
    }

    state.allObjects = data.objects || [];

    // Алфавитная сортировка по умолчанию (asc, localeCompare русский)
    // TEMPLATE: При необходимости замени поле сортировки (obj.name → obj.prefix и т.п.)
    state.allObjects.sort((a, b) => a.name.localeCompare(b.name, 'ru'));

    // TEMPLATE: Заполни фильтр, если нужен (периоды, категории и т.п.)
    // const filterSelect = document.getElementById('filter-select');
    // for (const value of data.filters) { ... }

    populateObjectsDropdown(state.allObjects);
  } catch (e) {
    if (searchInput) searchInput.placeholder = `Ошибка: ${e.message}`;
  }
}

function populateObjectsDropdown(objects) {
  renderComboboxItems(objects);

  // Восстановить выбор из localStorage (сохраняется между сессиями)
  // TEMPLATE: Замени ключи 'proto_selected_object_id' / '_name' на уникальные
  const savedId = localStorage.getItem('proto_selected_object_id');
  const savedName = localStorage.getItem('proto_selected_object_name');
  if (savedId && objects.find(o => String(o.id) === savedId)) {
    document.getElementById('objects-search').value = savedName || '';
    selectObject(parseInt(savedId), savedName || savedId);
  } else {
    document.getElementById('objects-search').placeholder = '— выберите объект —';
  }
}

function filterObjects() {
  // TEMPLATE: Реализуй фильтрацию объектов по выбранному фильтру
  const filterValue = document.getElementById('filter-select').value;
  const filtered = filterValue
    ? state.allObjects.filter(o => o.filter_field === filterValue)
    : state.allObjects;
  renderComboboxItems(filtered);
}

async function selectObject(objectId, objectName) {
  state.selectedObjectId = objectId;
  state.selectedObjectName = objectName;
  state.selectedItemId = null;
  state.selectedItemContext = null;
  state.mode = 'object';

  document.querySelectorAll('.item-entry').forEach(el => el.classList.remove('active'));

  // IMPORTANT: updateCaseButtons вызываем ДО await fetch — сразу обновляем UI
  updateContextIndicator();
  updateCaseButtons();

  // TEMPLATE: Загрузи дочерние элементы через API (опционально)
  const itemsList = document.getElementById('items-list');
  itemsList.innerHTML = '<div class="items-loading"><span class="spinner"></span> Загрузка...</div>';
  document.getElementById('items-section').classList.remove('hidden');

  try {
    const resp = await fetch(`/api/objects/${objectId}`, {
      headers: { 'X-Session-Id': state.sessionId }
    });
    if (!resp.ok) throw new Error('Ошибка загрузки');

    const data = await resp.json();
    // TEMPLATE: Адаптируй — сохрани контекст объекта
    state.selectedObjectContext = data.context || `Объект: ${objectName}`;

    // TEMPLATE: Заполни список дочерних элементов
    itemsList.innerHTML = '';
    if (data.items && data.items.length) {
      for (const item of data.items) {
        const div = document.createElement('div');
        div.className = 'item-entry';
        div.dataset.itemId = item.id;
        div.onclick = () => selectItem(item.id, item.name);
        div.innerHTML = `
          <div class="item-name">${item.name}</div>
          <div class="item-meta">${item.meta || ''}</div>
        `;
        itemsList.appendChild(div);
      }
    } else {
      itemsList.innerHTML = '<div style="font-size:12px;color:var(--color-text-muted);padding:8px 4px">Нет элементов</div>';
    }

    updateContextIndicator();
  } catch (e) {
    itemsList.innerHTML = `<div style="color:var(--color-danger);font-size:12px;padding:8px 4px">Ошибка: ${e.message}</div>`;
  }
}

async function selectItem(itemId, name) {
  // Повторный клик — развыбрать, вернуться в режим объекта
  if (state.selectedItemId === itemId) {
    document.querySelectorAll('.item-entry').forEach(el => el.classList.remove('active'));
    state.selectedItemId = null;
    state.selectedItemContext = null;
    state.selectedItemName = '';
    state.mode = 'object';
    updateContextIndicator();
    updateCaseButtons();
    return;
  }

  document.querySelectorAll('.item-entry').forEach(el => el.classList.remove('active'));
  document.querySelector(`.item-entry[data-item-id="${itemId}"]`)?.classList.add('active');

  // IMPORTANT: Обновляем state и UI ДО любого await fetch.
  // Иначе при ошибке сети кнопки кейсов и индикатор не обновятся.
  state.selectedItemId = itemId;
  state.selectedItemName = name;
  state.mode = 'item';
  updateContextIndicator();
  updateCaseButtons();

  // TEMPLATE: Загрузи детали элемента через API (опционально)
  try {
    const resp = await fetch(`/api/items/${itemId}`, {
      headers: { 'X-Session-Id': state.sessionId }
    });
    if (!resp.ok) throw new Error('Ошибка загрузки элемента');

    const data = await resp.json();
    state.selectedItemContext = data.context || `Элемент: ${name}`;

    // Контекст обновился — обновляем индикатор
    updateContextIndicator();
  } catch (e) {
    alert('Ошибка загрузки элемента: ' + e.message);
  }
}

function updateContextIndicator() {
  const el = document.getElementById('context-text');
  if (state.mode === 'item') {
    el.textContent = state.selectedItemName;
  } else if (state.mode === 'object') {
    el.textContent = state.selectedObjectName;
  } else {
    el.textContent = '— нет выбора —';
  }
}

function updateCaseButtons() {
  const hasItem = !!state.selectedItemId;
  const hasObject = !!state.selectedObjectId;

  document.querySelectorAll('.case-btn').forEach(btn => {
    // Кнопка "Добавить промпт" всегда активна
    if (btn.id === 'btn-add-prompt') return;

    const mode = btn.dataset.mode;

    // Пользовательские промпты (динамические кнопки) — активны только при объекте
    if (btn.dataset.customPromptId) {
      btn.disabled = !hasObject;
      return;
    }

    if (hasItem) {
      btn.disabled = mode !== 'item';
    } else if (hasObject) {
      btn.disabled = mode !== 'object';
    } else {
      btn.disabled = true;
    }
  });
}

// ===== CASES IN CHAT =====
// IMPORTANT: НЕ меняй паттерн SSE-стриминга — он проверен и работает стабильно.

async function runCaseInChat(caseId) {
  if (!state.selectedObjectId && !state.selectedItemId) return;

  if (state.currentAbortController) state.currentAbortController.abort();
  state.currentAbortController = new AbortController();
  const signal = state.currentAbortController.signal;

  // Каждый кейс — свежий запрос (очищаем историю)
  state.chatMessages = [];
  sessionStorage.removeItem('proto_chat');
  document.getElementById('chat-messages').innerHTML = '';

  const caseName = CASE_NAMES[caseId];
  const contextName = state.mode === 'item'
    ? state.selectedItemName
    : state.selectedObjectName;
  const userLabel = `▶ Кейс ${caseId}: ${caseName}\n📋 ${contextName}`;

  state.chatMessages.push({ role: 'user', content: userLabel });
  appendChatMessage('user', userLabel);

  const assistantDiv = appendChatMessage('assistant', '');
  // IMPORTANT: Спиннер показываем ДО fetch. Скрывается в onFirstChunk — не при response.ok.
  const stopThinking = showThinkingInElement(assistantDiv);

  setInputDisabled(true);

  try {
    const resp = await fetch(`/api/cases/${caseId}`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'X-Session-Id': state.sessionId,
      },
      body: JSON.stringify({
        mode: state.mode,
        object_id: state.selectedObjectId,
        item_id: state.selectedItemId,
        model_id: getSelectedModelId() || undefined,
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

    // onFirstChunk: спиннер скрывается только при ПЕРВОМ chunk-событии
    const fullText = await readSSEStreamToElement(resp, assistantDiv, signal, () => {
      stopThinking();
      assistantDiv.innerHTML = '';
    });
    state.chatMessages.push({ role: 'assistant', content: fullText });
    saveChatToSession();
    appendFeedbackBar(assistantDiv, caseId);

  } catch (e) {
    stopThinking();
    if (e.name === 'AbortError') {
      assistantDiv.innerHTML = '<em style="color:var(--color-text-muted)">Прервано</em>';
      return;
    }
    assistantDiv.innerHTML = `<p style="color:var(--color-danger)">Ошибка: ${e.message}</p>`;
  } finally {
    setInputDisabled(false);
  }
}

function appendFeedbackBar(afterElement, caseId) {
  const bar = document.createElement('div');
  bar.className = 'feedback-bar';
  bar.innerHTML = `
    <span class="feedback-label">Оцените результат:</span>
    <button class="feedback-btn" onclick="sendFeedback(${caseId}, 1, this.parentElement)">👍</button>
    <button class="feedback-btn" onclick="sendFeedback(${caseId}, -1, this.parentElement)">👎</button>
    <span class="feedback-sent hidden">Оценка сохранена</span>
  `;
  afterElement.parentElement.appendChild(bar);
  scrollChatToBottom();
}

function appendChatFeedbackBar(afterElement, userMessage) {
  const bar = document.createElement('div');
  bar.className = 'feedback-bar';

  const label = document.createElement('span');
  label.className = 'feedback-label';
  label.textContent = 'Оцените ответ:';

  const btnUp = document.createElement('button');
  btnUp.className = 'feedback-btn';
  btnUp.textContent = '👍';

  const btnDown = document.createElement('button');
  btnDown.className = 'feedback-btn';
  btnDown.textContent = '👎';

  const sent = document.createElement('span');
  sent.className = 'feedback-sent hidden';
  sent.textContent = 'Оценка сохранена';

  btnUp.addEventListener('click', () => sendChatFeedback(1, bar, userMessage));
  btnDown.addEventListener('click', () => sendChatFeedback(-1, bar, userMessage));

  bar.appendChild(label);
  bar.appendChild(btnUp);
  bar.appendChild(btnDown);
  bar.appendChild(sent);

  afterElement.parentElement.appendChild(bar);
  scrollChatToBottom();
}

// ===== CHAT =====

function handleChatKeydown(event) {
  if (event.key === 'Enter' && !event.shiftKey) {
    event.preventDefault();
    sendChatMessage();
  }
}

function autoResizeTextarea(el) {
  el.style.height = 'auto';
  el.style.height = Math.min(el.scrollHeight, 120) + 'px';
}

async function sendChatMessage() {
  const input = document.getElementById('chat-input');
  const text = input.value.trim();
  if (!text) return;
  if (!state.selectedObjectId && !state.selectedItemId) {
    alert('Выберите объект слева');
    return;
  }

  if (state.currentAbortController) state.currentAbortController.abort();
  state.currentAbortController = new AbortController();
  const signal = state.currentAbortController.signal;

  input.value = '';
  input.style.height = 'auto';

  const contextName = state.mode === 'item'
    ? state.selectedItemName
    : state.selectedObjectName;
  const displayText = contextName
    ? `${text}\n📋 ${contextName}`
    : text;

  state.chatMessages.push({ role: 'user', content: text });
  appendChatMessage('user', displayText);

  setInputDisabled(true);

  const assistantDiv = appendChatMessage('assistant', '');
  // IMPORTANT: Спиннер показываем ДО fetch. Скрывается в onFirstChunk — не при response.ok.
  const stopThinking = showThinkingInElement(assistantDiv);

  try {
    const resp = await fetch('/api/chat', {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'X-Session-Id': state.sessionId,
      },
      body: JSON.stringify({
        mode: state.mode,
        object_id: state.selectedObjectId,
        item_id: state.selectedItemId,
        messages: state.chatMessages,
        model_id: getSelectedModelId() || undefined,
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

    // onFirstChunk: спиннер скрывается только при ПЕРВОМ chunk-событии
    const fullText = await readSSEStreamToElement(resp, assistantDiv, signal, () => {
      stopThinking();
      assistantDiv.innerHTML = '';
    });
    state.chatMessages.push({ role: 'assistant', content: fullText });
    saveChatToSession();
    appendChatFeedbackBar(assistantDiv, text);

  } catch (e) {
    stopThinking();
    if (e.name === 'AbortError') {
      assistantDiv.innerHTML = '<em style="color:var(--color-text-muted)">Прервано</em>';
      return;
    }
    assistantDiv.innerHTML = `<p style="color:var(--color-danger)">Ошибка: ${e.message}</p>`;
  } finally {
    setInputDisabled(false);
    document.getElementById('chat-input').focus();
  }
}

function setInputDisabled(disabled) {
  document.getElementById('btn-chat-send').disabled = disabled;
  document.getElementById('chat-input').disabled = disabled;
}

function appendChatMessage(role, text) {
  const messages = document.getElementById('chat-messages');
  const div = document.createElement('div');
  div.className = `chat-message ${role}`;
  if (text) div.textContent = text;
  messages.appendChild(div);
  scrollChatToBottom();
  return div;
}

function _isAtBottom() {
  const el = document.getElementById('chat-messages');
  if (!el) return true;
  return el.scrollTop + el.clientHeight >= el.scrollHeight - 80;
}

function _setFollowBtnVisible(visible) {
  document.getElementById('scroll-to-bottom-btn')?.classList.toggle('hidden', !visible);
}

function scrollChatToBottom() {
  const el = document.getElementById('chat-messages');
  if (el) el.scrollTop = el.scrollHeight;
  _setFollowBtnVisible(false);
}

function resumeAutoScroll() {
  scrollChatToBottom();
}

function restoreChatMessages() {
  const container = document.getElementById('chat-messages');
  container.innerHTML = '';
  for (const msg of state.chatMessages) {
    const div = document.createElement('div');
    div.className = `chat-message ${msg.role}`;
    if (msg.role === 'assistant') {
      div.innerHTML = renderMarkdown(msg.content);
    } else {
      div.textContent = msg.content;
    }
    container.appendChild(div);
  }
}

function saveChatToSession() {
  sessionStorage.setItem('proto_chat', JSON.stringify(state.chatMessages));
}

function resetConversation() {
  // IMPORTANT: Порядок строго обязателен:
  // 1. Прерываем SSE-стрим ПЕРВЫМ — иначе он продолжит писать в очищенный DOM
  state.currentAbortController?.abort();
  state.currentAbortController = null;
  // 2. Разблокируем ввод — не ждём done-события (оно не придёт после abort)
  setInputDisabled(false);
  // 3. Только потом очищаем чат
  state.chatMessages = [];
  sessionStorage.removeItem('proto_chat');
  document.getElementById('chat-messages').innerHTML = `
    <div class="chat-message assistant">
      Здравствуйте! Выберите объект слева, затем задайте вопрос или нажмите кнопку кейса.
    </div>
  `;
}

// ===== FEEDBACK =====

async function sendFeedback(caseId, vote, bar) {
  try {
    await fetch('/api/feedback', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ case_id: caseId, session_id: state.sessionId, vote, model_label: getSelectedModelLabel() }),
    });
    const sent = bar.querySelector('.feedback-sent');
    if (sent) {
      sent.classList.remove('hidden');
      setTimeout(() => sent.classList.add('hidden'), 2000);
    }
  } catch (e) { /* ignore */ }
}

async function sendChatFeedback(vote, bar, userMessage) {
  // Блокируем кнопки сразу
  bar.querySelectorAll('.feedback-btn').forEach(btn => { btn.disabled = true; });

  try {
    const contextType = (state.mode === 'item') ? 'item' : 'object';
    const contextName = (state.mode === 'item')
      ? (state.selectedItemName || '')
      : (state.selectedObjectName || '');

    const saveResp = await fetch('/api/feedback/chat', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        session_id: state.sessionId,
        vote,
        user_message: userMessage,
        context_type: contextType,
        context_name: contextName || '',
        model_label: getSelectedModelLabel(),
      }),
    });

    const sent = bar.querySelector('.feedback-sent');
    if (sent) {
      sent.classList.remove('hidden');
      setTimeout(() => sent.classList.add('hidden'), 2000);
    }

    // Асинхронно генерируем саммари — не ждём, не блокируем UI.
    // IMPORTANT: Саммари только для бэк-офиса. В чате ничего не меняется.
    if (saveResp.ok) {
      const saveData = await saveResp.json();
      if (saveData.id) {
        fetch('/api/feedback/chat/summarize', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ id: saveData.id, user_message: userMessage }),
        }).catch(() => { /* ignore summarize errors silently */ });
      }
    }
  } catch (e) { /* ignore */ }
}

// ===== MARKDOWN, SSE, ПРОМПТЫ =====
// Вынесены в отдельные файлы: markdown.js, sse.js, prompts.js
// Подключаются через <script> в index.html

// Скачивание (.docx), промпты, модалы — см. prompts.js

// ===== INITIALIZATION =====

document.addEventListener('DOMContentLoaded', () => {
  initSession();
  loadModels();
  loadObjects();
  loadCustomPrompts();

  document.getElementById('chat-messages')?.addEventListener('scroll', () => {
    _setFollowBtnVisible(!_isAtBottom());
  });
});
