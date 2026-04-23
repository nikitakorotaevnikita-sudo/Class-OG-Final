# Historical Appeals Import

Папка `data/historical/` используется для массового импорта исторических обращений граждан.

## Структура папки

```
data/historical/              # пользователь кладёт файлы сюда
  processed/                  # успешно обработанные файлы
  errors/                     # файлы с ошибками валидации
```

## Поддерживаемые форматы

| Формат | Расширение | Колонки |
|--------|-----------|---------|
| Excel  | `.xlsx`, `.xls` | `appeal_text`, `assigned_code`, `specialist`, `date` |
| CSV    | `.csv` | те же |
| JSON   | `.json` | массив объектов с теми же полями |

Колонки могут называться по-разному — скрипт автоматически распознаёт варианты:
- `appeal_text`: `appeal_text`, `text`, `текст`, `обращение`
- `assigned_code`: `assigned_code`, `code`, `код`, `код_классификатора`
- `specialist`: `specialist`, `specialist_name`, `специалист`, `фио`
- `date`: `date`, `дата`, `registration_date`

## Запуск

```bash
# Однократный прогон — обрабатывает все файлы в папке
python src/auto_import_historical.py

# Режим наблюдения — автоматически обрабатывает новые файлы каждые 30 сек
python src/auto_import_historical.py --watch

# Кастомный интервал (например 60 секунд)
python src/auto_import_historical.py --watch --interval 60

# Справка
python src/auto_import_historical.py --help
```

## Что делает скрипт

1. **Сканирует** `data/historical/` на предмет файлов `.xlsx`, `.xls`, `.csv`, `.json`
2. **Парсит** каждый файл через `historical_loader.parse_file()`
3. **Валидирует** коды по `data/classifier_flat.json` через `historical_loader.validate_codes()`
4. **Сохраняет** валидные записи в `data/historical_verified.jsonl`
5. **Перемещает** обработанный файл:
   - `data/historical/processed/YYYY-MM-DD_filename.ext` — если файл был успешно обработан
   - `data/historical/errors/YYYY-MM-DD_filename.ext` — если есть ошибки валидации
6. **Создаёт отчёт** об ошибках: `data/historical/errors/YYYY-MM-DD_filename_report.json`

## Формат отчёта об ошибках

```json
{
  "processed_at": "2026-04-23T15:30:00",
  "source_file": "appeals_2025.xlsx",
  "stats": {"total": 1247, "valid": 1240, "invalid": 7},
  "errors": [
    {"row": 15, "code": "1234.5678.9012.3456", "error": "Код не найден в классификаторе"},
    {"row": 203, "code": "", "error": "Пустой код"}
  ],
  "saved_to": null
}
```

## Накопленные данные

Все валидные записи импортируются в `data/historical_verified.jsonl` — этот файл используется для последующего обучения и анализа.