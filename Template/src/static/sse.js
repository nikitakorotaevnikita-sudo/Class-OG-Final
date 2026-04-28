/* =============================================================================
   SSE STREAM READER
   =============================================================================
   Читает Server-Sent Events из fetch Response, рендерит markdown в DOM-элемент.

   IMPORTANT: НЕ модифицируй этот код — он проверен и работает стабильно.
   Обязательно используй AbortController для отмены предыдущих запросов.
   onFirstChunk вызывается один раз при первом chunk или error — используй для
   скрытия thinking indicator.

   Зависимости: renderMarkdown() из markdown.js, scrollChatToBottom() из app.js.
   ============================================================================= */

async function readSSEStreamToElement(resp, targetElement, signal, onFirstChunk) {
  const reader = resp.body.getReader();
  const decoder = new TextDecoder();
  let buffer = '';
  let fullText = '';
  let firstChunkFired = false;

  const fireFirstChunk = () => {
    if (!firstChunkFired) {
      firstChunkFired = true;
      if (onFirstChunk) onFirstChunk();
    }
  };

  if (signal) signal.addEventListener('abort', () => reader.cancel());

  while (true) {
    const { done, value } = await reader.read();
    if (done) break;

    buffer += decoder.decode(value, { stream: true });
    const lines = buffer.split('\n');
    buffer = lines.pop();

    for (const line of lines) {
      if (!line.startsWith('data: ')) continue;
      const data = line.slice(6);
      if (data === '[DONE]') return fullText;
      try {
        const chunk = JSON.parse(data);
        if (typeof chunk === 'string' && chunk.startsWith('[ERROR]')) {
          fireFirstChunk();
          fullText = chunk.replace('[ERROR] ', '\u041e\u0448\u0438\u0431\u043a\u0430: ');
        } else {
          fireFirstChunk();
          fullText += chunk;
        }
        const atBottom = _isAtBottom();
        targetElement.innerHTML = renderMarkdown(fullText);
        if (atBottom) {
          scrollChatToBottom();
        } else {
          _setFollowBtnVisible(true);
        }
      } catch (e) { /* skip invalid JSON */ }
    }
  }

  return fullText;
}
