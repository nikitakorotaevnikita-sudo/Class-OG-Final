"""
appeals_logger.py — Логирование классификаций для накопления данных дообучения

Каждая классификация — карточка обращения в хранилище (Storage.appeals:
appeal_classifications + appeal_questions + appeal_verifications).
Оператор верифицирует результат → карточка обогащается статусом.
Подтверждённые/исправленные пары → обучающий датасет для fine-tuning.

read_all()/read_verified() отдают записи в формате старого appeals_log.jsonl:
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

from __future__ import annotations

from typing import Optional

from storage import get_storage
from storage.models import AppealQuestionRow, AppealResult, Storage



def _legacy_result(candidates: list, agent_questions: list,
                   overall_confidence: float) -> AppealResult:
    """AppealResult из аргументов старого log() (без полей, которых там не было)."""
    return AppealResult(
        summary=None, applicant_fio=None, applicant_email=None,
        vid_obrascheniya=None, tip_obrascheniya=None, is_ustnoe=None,
        overall_confidence=overall_confidence, needs_verification=None,
        full_fallback_used=None, candidates=list(candidates or []),
        llm_provider=None, llm_model=None, elapsed_seconds=None,
        questions=[
            AppealQuestionRow(
                ordinal=i,
                question_text=q.get("question_text", ""),
                selected_code=q.get("selected_code", ""),
                confidence=q.get("confidence", 0.0),
                alternative_codes=list(q.get("alternative_codes") or []),
            )
            for i, q in enumerate(agent_questions or [], 1)
        ],
    )


class AppealsLogger:

    def __init__(self, storage: Optional[Storage] = None):
        self.storage = storage or get_storage()

    # ── Запись новой классификации ────────────────────────────────────────────

    def log(
        self,
        appeal_text: str,
        candidates: list,
        agent_questions: list,
        overall_confidence: float,
        appeal_id: Optional[str] = None,
        result: Optional[AppealResult] = None,
        conn=None,
    ) -> str:
        """
        Записывает результат классификации в карточку обращения.
        Возвращает id обращения (log_id).

        appeal_id — карточка уже создана (воркер): только сохраняем результат;
                    None — создаём новую карточку.
        result    — полный результат; если не передан, собирается из
                    candidates / agent_questions / overall_confidence.
        conn      — соединение внешней транзакции (воркер); без него запись
                    идёт в собственной транзакции.

        agent_questions — список dict с ключами:
            question_text, selected_code, confidence, alternative_codes
        candidates — топ-K из векторного поиска, список dict:
            code, name, similarity
        """
        if result is None:
            result = _legacy_result(candidates, agent_questions, overall_confidence)
        appeals = self.storage.appeals

        def write(c) -> str:
            aid = appeal_id or appeals.create(appeal_text, conn=c)
            appeals.save_result(aid, result, conn=c)
            return aid

        if conn is not None:
            return write(conn)
        with self.storage.transaction() as c:
            return write(c)

    # ── Верификация оператором ────────────────────────────────────────────────

    def confirm(self, log_id: str, comment: str = None) -> bool:
        """
        Оператор подтвердил классификацию агента.
        Самый ценный сигнал: (appeal_text, agent_code) → позитивная пара.
        """
        return self.storage.appeals.set_verification(log_id, "confirmed", None, comment)

    def correct(self, log_id: str, operator_codes: list[str], comment: str = None) -> bool:
        """
        Оператор исправил классификацию.
        Даёт два сигнала:
          - (appeal_text, operator_code)  → позитивная пара
          - (appeal_text, agent_code)     → негативная пара
        operator_codes — список правильных кодов классификатора.
        comment — пояснение оператора (сохраняется как аннотация каждого кода)
        """
        return self.correct_with_annotations(log_id, operator_codes, comment)[0]

    def correct_with_annotations(self, log_id: str, operator_codes: list[str],
                                 comment: str = None) -> tuple[bool, list[int]]:
        """То же, что correct(), плюс id вставленных строк аннотаций.

        Строки создаются без эмбеддинга (ждут embed_annotation): /verify ставит
        в очередь ровно эти id, без сравнения «до/после» со всей таблицей.
        """
        updated = self.storage.appeals.set_verification(
            log_id, "corrected", operator_codes, comment)
        inserted: list[int] = []
        if updated and comment:
            from storage.annotations_storage import add_annotation
            for op_code in operator_codes:
                try:
                    inserted.append(add_annotation(op_code, comment, case_id=log_id,
                                                   storage=self.storage))
                except Exception as e:  # noqa: BLE001 — аннотация не должна терять исправление
                    print(f"  [logger] Аннотация для {op_code} не сохранена: {e}")
        return updated, inserted

    def reject(self, log_id: str, comment: str = None) -> bool:
        """
        Оператор отклонил обращение (некорректное, спам и т.п.).
        Запись исключается из обучения.
        """
        return self.storage.appeals.set_verification(log_id, "rejected", None, comment)

    # ── Чтение и фильтрация ───────────────────────────────────────────────────

    def read_all(self) -> list[dict]:
        """Все записи в формате старого лога (по времени создания)."""
        return self.storage.appeals.as_log_entries()

    def read_verified(self) -> list[dict]:
        """Возвращает только подтверждённые и исправленные записи (для обучения)."""
        return self.storage.appeals.as_log_entries(statuses=("confirmed", "corrected"))

    def stats(self) -> dict:
        """Статистика по логу."""
        return self.storage.appeals.stats()


# ── Глобальный экземпляр (опционально) ───────────────────────────────────────

_logger: AppealsLogger | None = None


def get_logger() -> AppealsLogger:
    """Возвращает глобальный экземпляр логгера (создаёт при первом вызове)."""
    global _logger
    if _logger is None:
        _logger = AppealsLogger(get_storage())
    return _logger
