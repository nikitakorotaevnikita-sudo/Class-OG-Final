/* =============================================================================
   MARKDOWN RENDERER
   =============================================================================
   Встроенный рендерер markdown в HTML. Поддерживает: headers, bold, italic,
   code, lists, blockquotes, tables, hr.

   IMPORTANT: НЕ заменяй на marked.js — не работает в Docker без интернета.
   IMPORTANT: НЕ вызывай renderMarkdown() на каждый отдельный чанк SSE —
   незакрытые теги (**bold без закрытия) делают весь текст жирным.
   Вызывай на НАКАПЛИВАЕМЫЙ текст (fullText += chunk; renderMarkdown(fullText)).
   ============================================================================= */

function renderMarkdown(text) {
  const esc = (s) => s.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
  const lines = text.split('\n');
  let html = '';
  let inList = false;
  let inOrderedList = false;
  let tableBuffer = [];

  const closeList = () => {
    if (inList) { html += '</ul>'; inList = false; }
    if (inOrderedList) { html += '</ol>'; inOrderedList = false; }
  };

  const flushTable = () => {
    if (!tableBuffer.length) return;
    const parseRow = (row) =>
      row.replace(/^\||\|$/g, '').split('|').map(cell => cell.trim());

    const headers = parseRow(tableBuffer[0]);
    const aligns = tableBuffer[1]
      ? parseRow(tableBuffer[1]).map(cell => {
          if (/^:-+:$/.test(cell)) return 'center';
          if (/^-+:$/.test(cell))  return 'right';
          return 'left';
        })
      : [];

    let thtml = '<div class="md-table-wrap"><table class="md-table"><thead><tr>';
    headers.forEach((h, i) => {
      const align = aligns[i] ? ` style="text-align:${aligns[i]}"` : '';
      thtml += `<th${align}>${inlineFormat(h)}</th>`;
    });
    thtml += '</tr></thead><tbody>';

    for (let r = 2; r < tableBuffer.length; r++) {
      const cells = parseRow(tableBuffer[r]);
      thtml += '<tr>';
      headers.forEach((_, i) => {
        const align = aligns[i] ? ` style="text-align:${aligns[i]}"` : '';
        thtml += `<td${align}>${inlineFormat(cells[i] || '')}</td>`;
      });
      thtml += '</tr>';
    }
    thtml += '</tbody></table></div>';
    html += thtml;
    tableBuffer = [];
  };

  const inlineFormat = (s) => {
    s = esc(s);
    s = s.replace(/\*\*\*(.+?)\*\*\*/g, '<strong><em>$1</em></strong>');
    s = s.replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>');
    s = s.replace(/\*(.+?)\*/g, '<em>$1</em>');
    s = s.replace(/`([^`]+)`/g, '<code>$1</code>');
    return s;
  };

  const isTableRow = (line) => /^\|.+\|/.test(line.trim());

  for (const line of lines) {
    if (isTableRow(line)) {
      closeList();
      tableBuffer.push(line.trim());
      continue;
    } else if (tableBuffer.length) {
      flushTable();
    }

    if (/^### /.test(line)) { closeList(); html += `<h3>${inlineFormat(line.slice(4))}</h3>`; continue; }
    if (/^## /.test(line))  { closeList(); html += `<h2>${inlineFormat(line.slice(3))}</h2>`; continue; }
    if (/^# /.test(line))   { closeList(); html += `<h1>${inlineFormat(line.slice(2))}</h1>`; continue; }
    if (/^---+$/.test(line.trim())) { closeList(); html += '<hr>'; continue; }

    if (/^[-*] /.test(line)) {
      if (inOrderedList) { html += '</ol>'; inOrderedList = false; }
      if (!inList) { html += '<ul>'; inList = true; }
      html += `<li>${inlineFormat(line.slice(2))}</li>`;
      continue;
    }
    if (/^\d+\. /.test(line)) {
      if (inList) { html += '</ul>'; inList = false; }
      if (!inOrderedList) { html += '<ol>'; inOrderedList = true; }
      html += `<li>${inlineFormat(line.replace(/^\d+\. /, ''))}</li>`;
      continue;
    }
    if (/^> /.test(line)) {
      closeList();
      html += `<blockquote>${inlineFormat(line.slice(2))}</blockquote>`;
      continue;
    }

    if (line.trim() === '') { closeList(); html += '<br>'; continue; }

    closeList();
    html += `<p>${inlineFormat(line)}</p>`;
  }

  flushTable();
  closeList();
  return html;
}
