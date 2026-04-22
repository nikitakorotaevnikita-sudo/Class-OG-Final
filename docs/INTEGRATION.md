# Интеграция с Directum RX

## API-эндпоинты

### POST /classify

**Request (JSON):**
```json
{
  "appeal_text": "Текст обращения гражданина (до 5000 символов)",
  "appeal_id": "опциональный ID для вашей системы"
}
```

**Response (200):**
```json
{
  "log_id": "uuid-верификации",
  "appeal_id": "ваш-ID",
  "vid_obrascheniya": "Жалоба",
  "tip_obrascheniya": "Индивидуальное",
  "is_ustnoe": false,
  "questions": [
    {
      "question_text": "Выявленный вопрос из обращения",
      "code": "0005.0005.0056.1160",
      "name": "Обращение с твёрдыми коммунальными отходами",
      "level": 4,
      "full_path": "Жилищно-коммунальная сфера / Обращение с ТКО / ...",
      "predmet_vedeniya": "Вопрос местного значения",
      "confidence": 0.87,
      "reasoning": "Обоснование выбора",
      "alternatives": [
        {"code": "...", "name": "...", "full_path": "..."}
      ]
    }
  ],
  "overall_confidence": 0.87,
  "needs_verification": false,
  "operator_card": "Текстовая карточка для оператора"
}
```

### GET /health

```bash
curl http://localhost:8000/health
# Response: {"status": "ok", "agent_ready": true, "classifier_entries": 2108}
```

### POST /verify

Верификация результата оператором:
```bash
curl -X POST http://localhost:8000/verify \
  -H "Content-Type: application/json" \
  -d '{"log_id": "<uuid>", "action": "confirm"}'
# action: confirm | correct | reject
# Для correct: {"log_id": "<uuid>", "action": "correct", "operator_codes": ["0005.0005.0056.1160"]}
```

### GET /examples

10 тестовых обращений:
```bash
curl http://localhost:8000/examples
```

## Поля для Directum RX

Из ответа `/classify` в карточку обращения:

| Поле Directum | Источник |
|---|---|
| Вид обращения | `vid_obrascheniya` |
| Код вопроса | `questions[0].code` |
| Наименование вопроса | `questions[0].name` |
| Предмет ведения | `questions[0].predmet_vedeniya` |
| Уровень классификатора | `questions[0].level` |
| Уверенность | `overall_confidence` |
| Требует верификации | `needs_verification` |

## Лимиты

- Максимальный размер файла: 5 МБ
- Максимальная длина текста: 5000 символов
- Поддерживаемые форматы файлов: `.txt`, `.pdf` (только текстовые, не сканы)