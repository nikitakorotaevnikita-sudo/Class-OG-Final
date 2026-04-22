"""
Агент классификации обращений граждан РФ
- Определяет вид и тип обращения по 59-ФЗ
- Классифицирует по Общероссийскому классификатору (v4)
- Определяет предмет ведения
- Поддерживает несколько вопросов в одном обращении
"""

import json
import numpy as np
import time
from pathlib import Path
from groq import Groq, RateLimitError, APIError
from google.genai import client as genai_client
from google.genai.types import GenerateContentConfig
from sentence_transformers import SentenceTransformer
from dataclasses import dataclass, field
from typing import Optional
from config import (
    GROQ_API_KEY, GROQ_MODEL,
    VECTOR_DB_DIR, EMBEDDING_MODEL,
    TOP_K_CANDIDATES, TOP_K_RESULT, MIN_CONFIDENCE,
    LLM_PROVIDER, GEMINI_API_KEY,
)
from appeals_logger import get_logger


# ── Модели данных ──────────────────────────────────────────────────────────────

@dataclass
class ClassifiedQuestion:
    """Классификация одного вопроса из обращения"""
    question_text: str
    code: str
    name: str
    level: int
    full_path: str
    predmet_vedeniya: str
    confidence: float
    reasoning: str
    alternatives: list


@dataclass
class ClassificationResult:
    """Полный результат классификации обращения"""
    vid_obrascheniya: str
    tip_obrascheniya: str
    is_ustное: bool
    questions: list
    overall_confidence: float
    needs_verification: bool
    raw_appeal: str
    log_id: Optional[str] = field(default=None)  # ID записи в appeals_log.jsonl


# ── Промпты ────────────────────────────────────────────────────────────────────

SYSTEM_PROMPT = """Ты — эксперт по классификации обращений граждан РФ в соответствии с Федеральным законом №59-ФЗ «О порядке рассмотрения обращений граждан Российской Федерации».

Твоя задача:
1. Определить вид обращения (Жалоба, Заявление или Предложение)
2. Определить тип обращения (Индивидуальное, Коллективное или Анонимное)
3. Выявить все вопросы, содержащиеся в обращении
4. Для каждого вопроса подобрать наиболее подходящую категорию из предложенных кандидатов классификатора
5. Определить предмет ведения для каждого вопроса

Определения по 59-ФЗ:
- Жалоба — просьба гражданина о восстановлении или защите его нарушенных прав
- Заявление — просьба о содействии в реализации прав, уведомление о нарушении законов
- Предложение — рекомендация по совершенствованию законов, деятельности органов власти

Предмет ведения — возможные значения:
- "Вопрос местного значения"
- "Предмет ведения Российской Федерации"
- "Предмет ведения субъектов Российской Федерации"
- "Предмет совместного ведения Российской Федерации и субъектов Российской Федерации"

Отвечай строго в формате JSON, без дополнительного текста."""


CLASSIFICATION_PROMPT_TEMPLATE = """Классифицируй следующее обращение гражданина.

ТЕКСТ ОБРАЩЕНИЯ:
{appeal_text}

КАНДИДАТЫ ИЗ КЛАССИФИКАТОРА (для каждого выявленного вопроса):
{candidates_json}

Верни JSON в следующем формате:
{{
  "vid_obrascheniya": "Жалоба|Заявление|Предложение",
  "tip_obrascheniya": "Индивидуальное|Коллективное|Анонимное",
  "is_ustnoe": false,
  "questions": [
    {{
      "question_text": "Краткая формулировка вопроса из обращения",
      "selected_code": "XXXX.XXXX.XXXX.XXXX",
      "predmet_vedeniya": "...",
      "confidence": 0.87,
      "reasoning": "Обоснование выбора категории (1-2 предложения)",
      "alternative_codes": ["XXXX.XXXX.XXXX.XXXX", "XXXX.XXXX.XXXX.XXXX"]
    }}
  ]
}}"""


# ── Класс агента ───────────────────────────────────────────────────────────────

class ClassifierAgent:
    def __init__(self):
        print("Инициализация агента классификации...")

        # LLM client (Groq или Gemini)
        if LLM_PROVIDER == "gemini":
            self.llm = "gemini"
            self.gemini = genai_client.Client(api_key=GEMINI_API_KEY)
            print(f"  Модель LLM: gemini-2.5-flash (Google Gemini)")
        else:
            self.llm = "groq"
            self.groq = Groq(api_key=GROQ_API_KEY)
            print(f"  Модель LLM: {GROQ_MODEL} (Groq)")

        # Модель эмбеддингов
        print(f"  Загрузка модели эмбеддингов: {EMBEDDING_MODEL}")
        self.embedder = SentenceTransformer(EMBEDDING_MODEL)

        # Загрузка векторной базы (numpy)
        db_dir = Path(VECTOR_DB_DIR)
        print(f"  Загрузка векторной базы: {db_dir}")
        self.embeddings = np.load(db_dir / "embeddings.npy")
        with open(db_dir / "metadata.json", encoding="utf-8") as f:
            self.metadata = json.load(f)

        self.code_index = {m["code"]: m for m in self.metadata}
        print(f"  Готово. База содержит {len(self.metadata)} записей.")

    def _embed_query(self, text: str) -> np.ndarray:
        """Векторизация запроса с префиксом для multilingual-e5"""
        return self.embedder.encode(f"query: {text}", normalize_embeddings=True)

    def _search_candidates(self, query_text: str) -> list:
        """Семантический поиск кандидатов через cosine similarity (numpy)"""
        query_vec = self._embed_query(query_text)
        similarities = self.embeddings @ query_vec
        top_indices = np.argsort(similarities)[::-1][:TOP_K_CANDIDATES]

        candidates = []
        for idx in top_indices:
            meta = self.metadata[idx]
            candidates.append({
                "code":       meta["code"],
                "name":       meta["name"],
                "level":      meta["level"],
                "full_path":  meta["full_path"],
                "similarity": round(float(similarities[idx]), 3),
            })
        return candidates

    def _classify_with_llm(self, appeal_text: str, all_candidates: list) -> dict:
        """Вызов LLM для финальной классификации с retry-логикой (Groq или Gemini)"""
        candidates_json = json.dumps(all_candidates, ensure_ascii=False, indent=2)
        user_message = CLASSIFICATION_PROMPT_TEMPLATE.format(
            appeal_text=appeal_text[:4000],
            candidates_json=candidates_json
        )

        max_retries = 3
        last_error = None

        for attempt in range(1, max_retries + 1):
            try:
                start_time = time.time()

                if self.llm == "gemini":
                    response = self.gemini.models.generate_content(
                        model="gemini-2.5-flash",
                        contents=user_message,
                        config=GenerateContentConfig(
                            system_instruction=SYSTEM_PROMPT,
                            temperature=0.1,
                        ),
                    )
                    raw = response.text.strip()
                else:
                    response = self.groq.chat.completions.create(
                        model=GROQ_MODEL,
                        messages=[
                            {"role": "system", "content": SYSTEM_PROMPT},
                            {"role": "user",   "content": user_message},
                        ],
                        temperature=0.1,
                        response_format={"type": "json_object"},
                    )
                    raw = response.choices[0].message.content.strip()

                elapsed = time.time() - start_time
                result = json.loads(raw)

                self._log_request(
                    success=True,
                    attempt=attempt,
                    elapsed=elapsed,
                    confidence=None,
                    error=None,
                    candidates_count=len(all_candidates)
                )
                return result

            except RateLimitError as e:
                last_error = f"Rate limit: {e}"
                wait_time = 2 ** attempt
                print(f"  [Groq] Rate limit, попытка {attempt}/{max_retries}. Ждём {wait_time}с...")
                time.sleep(wait_time)

            except APIError as e:
                last_error = f"API error: {e}"
                if attempt < max_retries:
                    wait_time = 2 ** attempt
                    print(f"  [Groq] API error, попытка {attempt}/{max_retries}. Ждём {wait_time}с...")
                    time.sleep(wait_time)

            except json.JSONDecodeError as e:
                last_error = f"JSON parse error: {e}"
                print(f"  [LLM] Ошибка парсинга JSON, попытка {attempt}/{max_retries}.")
                if attempt < max_retries:
                    time.sleep(1)

            except Exception as e:
                last_error = f"Unexpected error: {e}"
                err_str = str(e)
                if "gemini" in err_str.lower() or "429" in err_str or "503" in err_str:
                    wait_time = 2 ** attempt
                    print(f"  [Gemini] Ошибка {attempt}/{max_retries}: {err_str[:80]}. Ждём {wait_time}с...")
                    time.sleep(wait_time)
                else:
                    print(f"  [LLM] Неизвестная ошибка: {e}")
                    break

        self._log_request(
            success=False,
            attempt=max_retries,
            elapsed=None,
            confidence=None,
            error=last_error,
            candidates_count=len(all_candidates)
        )
        raise RuntimeError(f"LLM ({self.llm}) недоступен после {max_retries} попыток: {last_error}")

    def _log_request(self, success: bool, attempt: int, elapsed: float | None,
                     confidence: float | None, error: str | None, candidates_count: int):
        """Логирование каждого запроса к Groq API"""
        import datetime
        import json

        log_entry = {
            "timestamp": datetime.datetime.now().isoformat(),
            "event": "groq_request",
            "success": success,
            "attempt": attempt,
            "elapsed_seconds": round(elapsed, 3) if elapsed else None,
            "confidence": confidence,
            "candidates_count": candidates_count,
            "error": error,
        }

        log_path = Path("data/request_log.jsonl")
        log_path.parent.mkdir(parents=True, exist_ok=True)

        with open(log_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(log_entry, ensure_ascii=False) + "\n")

    def classify(self, appeal_text: str) -> ClassificationResult:
        """Основной метод классификации обращения."""

        # Шаг 1: Поиск кандидатов (с замером времени)
        search_start = time.time()
        candidates = self._search_candidates(appeal_text)
        search_elapsed = time.time() - search_start

        # Логируем поиск
        self._log_request(
            success=True,
            attempt=1,
            elapsed=search_elapsed,
            confidence=None,
            error=None,
            candidates_count=len(candidates)
        )
        print(f"  [Поиск] Найдено {len(candidates)} кандидатов за {search_elapsed:.2f}с")

        # Шаг 2: Классификация через Groq
        groq_result = self._classify_with_llm(appeal_text, candidates)

        # Шаг 3: Обогащение результатов
        classified_questions = []
        for q in groq_result.get("questions", []):
            code = q["selected_code"]
            entry = self.code_index.get(code)

            alt_entries = []
            for alt_code in q.get("alternative_codes", [])[:2]:
                alt_entry = self.code_index.get(alt_code)
                if alt_entry:
                    alt_entries.append({
                        "code":      alt_code,
                        "name":      alt_entry["name"],
                        "full_path": alt_entry["full_path"],
                    })

            classified_questions.append(ClassifiedQuestion(
                question_text=q.get("question_text", ""),
                code=code,
                name=entry["name"] if entry else code,
                level=entry["level"] if entry else 0,
                full_path=entry["full_path"] if entry else "",
                predmet_vedeniya=q.get("predmet_vedeniya", ""),
                confidence=float(q.get("confidence", 0.0)),
                reasoning=q.get("reasoning", ""),
                alternatives=alt_entries,
            ))

        # Шаг 4: Итоговая уверенность
        confidences = [q.confidence for q in classified_questions]
        overall_confidence = sum(confidences) / len(confidences) if confidences else 0.0
        needs_verification = overall_confidence < MIN_CONFIDENCE

        result = ClassificationResult(
            vid_obrascheniya=groq_result.get("vid_obrascheniya", ""),
            tip_obrascheniya=groq_result.get("tip_obrascheniya", ""),
            is_ustное=groq_result.get("is_ustnoe", False),
            questions=classified_questions,
            overall_confidence=round(overall_confidence, 3),
            needs_verification=needs_verification,
            raw_appeal=appeal_text,
        )

        # Логируем для накопления данных дообучения
        try:
            agent_questions = [
                {
                    "question_text":    q.question_text,
                    "selected_code":    q.code,
                    "confidence":       q.confidence,
                    "alternative_codes": [a["code"] for a in q.alternatives],
                }
                for q in classified_questions
            ]
            log_candidates = [
                {"code": c["code"], "name": c["name"], "similarity": c["similarity"]}
                for c in candidates
            ]
            result.log_id = get_logger().log(
                appeal_text=appeal_text,
                candidates=log_candidates,
                agent_questions=agent_questions,
                overall_confidence=result.overall_confidence,
            )
        except Exception as e:
            print(f"  [logger] Ошибка записи в лог: {e}")

        return result

    def format_for_operator(self, result: ClassificationResult) -> str:
        """Форматирование результата для показа оператору"""
        lines = [
            "╔══ РЕЗУЛЬТАТ КЛАССИФИКАЦИИ ИИ ══",
            f"║ Вид обращения:    {result.vid_obrascheniya}",
            f"║ Тип обращения:    {result.tip_obrascheniya}",
            f"║ Уверенность:      {result.overall_confidence*100:.0f}%",
        ]

        if result.needs_verification:
            lines.append("║ ⚠️  ТРЕБУЕТ ВЕРИФИКАЦИИ ОПЕРАТОРА")

        for i, q in enumerate(result.questions, 1):
            prefix = f"\n║ {'─'*40}"
            if len(result.questions) > 1:
                prefix += f"\n║ ВОПРОС {i}: {q.question_text}"
            lines.append(prefix)
            lines.append(f"║ Код:              {q.code}")
            lines.append(f"║ Наименование:     {q.name}")
            lines.append(f"║ Путь:             {q.full_path[:75]}")
            lines.append(f"║ Предмет ведения:  {q.predmet_vedeniya}")
            lines.append(f"║ Уверенность:      {q.confidence*100:.0f}%")
            lines.append(f"║ Обоснование:      {q.reasoning}")
            if q.alternatives:
                lines.append("║ Альтернативы:")
                for alt in q.alternatives:
                    lines.append(f"║   • {alt['code']} — {alt['name'][:50]}")

        lines.append("╚══════════════════════════════════")
        lines.append("   [ ПОДТВЕРДИТЬ ]  [ ИСПРАВИТЬ ]  [ ОТКЛОНИТЬ ]")
        return "\n".join(lines)
