// historical.js — загрузка исторических данных (Directum Design System)

(function() {
  'use strict';

  // DOM Elements
  var dropZone = document.getElementById('drop-zone');
  var fileInput = document.getElementById('file-input');
  var uploadSection = document.getElementById('upload-section');
  var resultsSection = document.getElementById('results-section');
  var uploadLoading = document.getElementById('upload-loading');
  var uploadError = document.getElementById('upload-error');
  var fileName = document.getElementById('file-name');
  var fileFormat = document.getElementById('file-format');
  var validCount = document.getElementById('valid-count');
  var invalidCount = document.getElementById('invalid-count');
  var totalCount = document.getElementById('total-count');
  var errorsList = document.getElementById('errors-list');
  var errorsUl = document.getElementById('errors-ul');
  var previewHeader = document.getElementById('preview-header');
  var previewBody = document.getElementById('preview-body');
  var btnConfirm = document.getElementById('btn-confirm');
  var btnCancel = document.getElementById('btn-cancel');
  var confirmLoading = document.getElementById('confirm-loading');
  var confirmSuccess = document.getElementById('confirm-success');
  var historicalCount = document.getElementById('historical-count');
  var btnFinetune = document.getElementById('btn-finetune');
  var finetuneLoading = document.getElementById('finetune-loading');
  var finetuneResult = document.getElementById('finetune-result');
  var toast = document.getElementById('toast');

  // State
  var lastResponse = null;

  // Toast notification
  function showToast(message) {
    toast.textContent = message;
    toast.classList.remove('hidden');
    clearTimeout(toast._tid);
    toast._tid = setTimeout(function() {
      toast.classList.add('hidden');
    }, 3000);
  }

  // Drag & Drop handlers
  dropZone.addEventListener('click', function() {
    fileInput.click();
  });

  dropZone.addEventListener('dragover', function(e) {
    e.preventDefault();
    dropZone.style.borderColor = 'var(--blue)';
    dropZone.style.background = 'var(--blue-light)';
    dropZone.style.color = 'var(--blue)';
  });

  dropZone.addEventListener('dragleave', function(e) {
    e.preventDefault();
    dropZone.style.borderColor = '';
    dropZone.style.background = '';
    dropZone.style.color = '';
  });

  dropZone.addEventListener('drop', function(e) {
    e.preventDefault();
    dropZone.style.borderColor = '';
    dropZone.style.background = '';
    dropZone.style.color = '';
    var files = e.dataTransfer.files;
    if (files.length > 0) {
      handleFile(files[0]);
    }
  });

  fileInput.addEventListener('change', function() {
    if (fileInput.files.length > 0) {
      handleFile(fileInput.files[0]);
    }
  });

  // Handle file upload
  function handleFile(file) {
    uploadError.classList.add('hidden');
    uploadLoading.classList.remove('hidden');

    var formData = new FormData();
    formData.append('file', file);

    fetch('/api/upload-historical', {
      method: 'POST',
      body: formData
    })
    .then(function(response) {
      if (!response.ok) {
        throw new Error('Upload failed: ' + response.status);
      }
      return response.json();
    })
    .then(function(data) {
      uploadLoading.classList.add('hidden');
      lastResponse = data;
      displayResults(data, file.name);
    })
    .catch(function(error) {
      uploadLoading.classList.add('hidden');
      uploadError.textContent = 'Ошибка загрузки: ' + error.message;
      uploadError.classList.remove('hidden');
    });
  }

  // Display validation results
  function displayResults(data, filename) {
    // Show file info
    fileName.textContent = filename;
    var suffix = filename.split('.').pop().toLowerCase();
    var formatMap = {
      'xlsx': 'Excel (XLSX)',
      'xls': 'Excel (XLS)',
      'csv': 'CSV',
      'json': 'JSON'
    };
    fileFormat.textContent = formatMap[suffix] || suffix.toUpperCase();

    // Update stats
    validCount.textContent = data.stats.valid;
    invalidCount.textContent = data.stats.invalid;
    totalCount.textContent = data.stats.total;

    // Show/hide errors
    if (data.errors && data.errors.length > 0) {
      errorsList.classList.remove('hidden');
      errorsUl.innerHTML = '';
      data.errors.forEach(function(err) {
        var li = document.createElement('li');
        li.textContent = 'Строка ' + err.row + ': ' + err.code + ' — ' + err.error;
        li.style.marginBottom = '4px';
        errorsUl.appendChild(li);
      });
    } else {
      errorsList.classList.add('hidden');
    }

    // Preview table
    if (data.preview && data.preview.length > 0) {
      document.getElementById('preview-section').classList.remove('hidden');
      renderPreviewTable(data.preview);
    }

    // Show results section
    uploadSection.classList.add('hidden');
    resultsSection.classList.remove('hidden');
  }

  // Render preview table
  function renderPreviewTable(records) {
    if (records.length === 0) return;

    var headers = Object.keys(records[0]);

    // Render header
    previewHeader.innerHTML = '';
    headers.forEach(function(header) {
      var th = document.createElement('th');
      th.textContent = header;
      th.style.border = '1px solid var(--border)';
      th.style.padding = '8px 12px';
      th.style.textAlign = 'left';
      th.style.fontWeight = '600';
      th.style.fontSize = '12px';
      th.style.color = 'var(--muted)';
      th.style.textTransform = 'uppercase';
      th.style.background = 'var(--surface2)';
      previewHeader.appendChild(th);
    });

    // Render body
    previewBody.innerHTML = '';
    records.forEach(function(record, idx) {
      var tr = document.createElement('tr');
      if (idx % 2 === 1) {
        tr.style.background = 'var(--surface2)';
      }
      headers.forEach(function(header) {
        var td = document.createElement('td');
        td.textContent = record[header] !== null ? record[header] : '';
        td.style.border = '1px solid var(--border)';
        td.style.padding = '8px 12px';
        td.style.fontSize = '13px';
        tr.appendChild(td);
      });
      previewBody.appendChild(tr);
    });
  }

  // Confirm button handler
  btnConfirm.addEventListener('click', function() {
    if (!lastResponse) return;

    confirmLoading.classList.remove('hidden');
    confirmSuccess.classList.add('hidden');

    fetch('/api/confirm-historical', {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json'
      },
      body: JSON.stringify({
        records: lastResponse.records || [],
        stats: lastResponse.stats
      })
    })
    .then(function(response) {
      if (!response.ok) {
        throw new Error('Confirm failed: ' + response.status);
      }
      return response.json();
    })
    .then(function(data) {
      confirmLoading.classList.add('hidden');
      confirmSuccess.classList.remove('hidden');
      showToast('Файл успешно сохранён');
      loadHistoricalCount();
    })
    .catch(function(error) {
      confirmLoading.classList.add('hidden');
      showToast('Ошибка сохранения: ' + error.message);
    });
  });

  // Cancel button handler
  btnCancel.addEventListener('click', function() {
    resultsSection.classList.add('hidden');
    uploadSection.classList.remove('hidden');
    lastResponse = null;
    fileInput.value = '';
  });

  // Load historical count
  function loadHistoricalCount() {
    fetch('/api/historical-count')
      .then(function(response) {
        if (!response.ok) return null;
        return response.json();
      })
      .then(function(data) {
        if (data && data.count !== undefined) {
          historicalCount.textContent = data.count;
        }
      })
      .catch(function() {});
  }

  // Fine-tune button handler
  btnFinetune.addEventListener('click', function() {
    if (!confirm('Запустить дообучение модели? Это может занять 10-15 минут.')) {
      return;
    }

    finetuneLoading.classList.remove('hidden');
    finetuneResult.classList.add('hidden');
    btnFinetune.disabled = true;

    fetch('/api/finetune', {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json'
      },
      body: JSON.stringify({
        source: 'combined',
        force: true
      })
    })
    .then(function(response) {
      if (!response.ok) {
        throw new Error('Fine-tune start failed: ' + response.status);
      }
      return response.json();
    })
    .then(function(data) {
      finetuneResult.classList.remove('hidden');
      finetuneResult.textContent = data.message || 'Дообучение запущено';
      pollFinetuneStatus();
    })
    .catch(function(error) {
      finetuneLoading.classList.add('hidden');
      finetuneResult.classList.remove('hidden');
      finetuneResult.textContent = 'Ошибка: ' + error.message;
      btnFinetune.disabled = false;
    });
  });

  function pollFinetuneStatus() {
    fetch('/api/finetune/status')
      .then(function(response) {
        if (!response.ok) {
          throw new Error('Status failed: ' + response.status);
        }
        return response.json();
      })
      .then(function(data) {
        finetuneResult.classList.remove('hidden');
        finetuneResult.textContent = data.message || data.status || 'Дообучение выполняется';

        if (data.status === 'completed') {
          finetuneLoading.classList.add('hidden');
          btnFinetune.disabled = false;
          showToast('Дообучение модели завершено');
          return;
        }

        if (data.status === 'failed') {
          finetuneLoading.classList.add('hidden');
          btnFinetune.disabled = false;
          finetuneResult.textContent = 'Ошибка: ' + (data.message || 'дообучение не выполнено');
          return;
        }

        setTimeout(pollFinetuneStatus, 3000);
      })
      .catch(function(error) {
        finetuneLoading.classList.add('hidden');
        btnFinetune.disabled = false;
        finetuneResult.classList.remove('hidden');
        finetuneResult.textContent = 'Ошибка получения статуса: ' + error.message;
      });
  }

  // Initial load
  loadHistoricalCount();

})();
