"""
appeals_logger.py — Логирование классификаций для накопления данных дообучения

Каждая классификация пишется в data/appeals_log.jsonl.
Оператор верифицирует результат → запись обогащается статусом.
Подтверждённые/исправленные пары → обучающий датасет для fine-tuning.

Формат записи:
{
  "id": "uuid",
  "timestamp": "2026-04-20T10:30:00",
  "appeal_text": "...",
  "candidates": [{"code": "...", "name": "...", "similarity": 0.92}, ...],
  "agent_questions": [
    {"question_text": "...", "selected_code": "...", "confidence": 0.87,
     "alternative_codes": [...]}
  ],
  "overall_confidence": 0.87,
  "verification": {
    "status": "pending",          # pending | confirmed | corrected | rejected
    "verified_at": null,
    "operator_codes": null,       # список кодов оператора (только для "corrected")
    "comment": null
  }
}
"""

import json
import uuid
from datetime import datetime
from pathlib import Path


LOG_FILE = Path(__file__).parent.parent / "data" / "appeals_log.jsonl"


class AppealsLogger:

    def __init__(self, log_path: Path = LOG_FILE):
        self.log_path = log_path
        self.log_path.parent.mkdir(parents=True, exist_ok=True)

    # ── Запись новой классификации ────────────────────────────────────────────

    def log(
        self,
        appeal_text: str,
        candidates: list,
        agent_questions: list,
        overall_confidence: float,
    ) -> str:
        """
        Записывает результат классификации в лог.
        Возвращает log_id — уникальный идентификатор записи.

        agent_questions — список dict с ключами:
            question_text, selected_code, confidence, alternative_codes
        candidates — топ-K из векторного поиска, список dict:
            code, name, similarity
        """
        log_id = str(uuid.uuid4())
        entry = {
            "id":                 log_id,
            "timestamp":          datetime.now().isoformat(timespec="seconds"),
            "appeal_text":        appeal_text,
            "candidates":         candidates,
            "agent_questions":    agent_questions,
            "overall_confidence": overall_confidence,
            "verification": {
                "status":         "pending",
                "verified_at":    None,
                "operator_codes": None,
                "comment":        None,
            },
        }
        self._append(entry)
        return log_id

    # ── Верификация оператором ────────────────────────────────────────────────

    def confirm(self, log_id: str, comment: str = None) -> bool:
        """
        Оператор подтвердил классификацию агента.
        Самый ценный сигнал: (appeal_text, agent_code) → позитивная пара.
        """
        return self._update_verification(log_id, {
            "status":         "confirmed",
            "verified_at":    datetime.now().isoformat(timespec="seconds"),
            "operator_codes": None,
            "comment":        comment,
        })

    def correct(self, log_id: str, operator_codes: list[str], comment: str = None) -> bool:
        """
        Оператор исправил классификацию.
        Даёт два сигнала:
          - (appeal_text, operator_code)  → позитивная пара
          - (appeal_text, agent_code)     → негативная пара
        operator_codes — список правильных кодов классификатора.
        """
        return self._update_verification(log_id, {
            "status":         "corrected",
            "verified_at":    datetime.now().isoformat(timespec="seconds"),
            "operator_codes": operator_codes,
            "comment":        comment,
        })

    def reject(self, log_id: str, comment: str = None) -> bool:
        """
        Оператор отклонил обращение (некорректное, спам и т.п.).
        Запись исключается из обучения.
        """
        return self._update_verification(log_id, {
            "status":         "rejected",
            "verified_at":    datetime.now().isoformat(timespec="seconds"),
            "operator_codes": None,
            "comment":        comment,
        })

    # ── Чтение и фильтрация ───────────────────────────────────────────────────

    def read_all(self) -> list[dict]:
        """Читает весь лог."""
        if not self.log_path.exists():
            return []
        entries = []
        with open(self.log_path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    try:
                        entries.append(json.loads(line))
                    except json.JSONDecodeError:
                        continue
        return entries

    def read_verified(self) -> list[dict]:
        """Возвращает только подтверждённые и исправленные записи (для обучения)."""
        return [
            e for e in self.read_all()
            if e["verification"]["status"] in ("confirmed", "corrected")
        ]

    def stats(self) -> dict:
        """Статистика по логу."""
        all_entries = self.read_all()
        counts = {"pending": 0, "confirmed": 0, "corrected": 0, "rejected": 0}
        for e in all_entries:
            status = e["verification"]["status"]
            counts[status] = counts.get(status, 0) + 1
        return {
            "total":     len(all_entries),
            "verified":  counts["confirmed"] + counts["corrected"],
            "pending":   counts["pending"],
            "confirmed": counts["confirmed"],
            "corrected": counts["corrected"],
            "rejected":  counts["rejected"],
        }

    # ── Внутренние методы ─────────────────────────────────────────────────────

    def _append(self, entry: dict):
        """Дописывает запись в конец JSONL-файла."""
        with open(self.log_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")

    def _update_verification(self, log_id: str, verification: dict) -> bool:
        """
        Обновляет поле verification записи с указанным id.
        JSONL не поддерживает изменение на месте — перезаписываем весь файл.
        Для небольших логов (тысячи строк) это нормально.
        """
        entries = self.read_all()
        updated = False
        for entry in entries:
            if entry["id"] == log_id:
                entry["verification"] = verification
                updated = True
                break

        if updated:
            with open(self.log_path, "w", encoding="utf-8") as f:
                for entry in entries:
                    f.write(json.dumps(entry, ensure_ascii=False) + "\n")

        return updated


# ── Глобальный экземпляр (опционально) ───────────────────────────────────────

_logger: AppealsLogger | None = None


def get_logger() -> AppealsLogger:
    """Возвращает глобальный экземпляр логгера (создаёт при первом вызове)."""
    global _logger
    if _logger is None:
        _logger = AppealsLogger()
    return _logger
