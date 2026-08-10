"""
Агент классификации обращений граждан РФ
- Определяет вид и тип обращения по 59-ФЗ
- Классифицирует по Общероссийскому классификатору (v4)
- Определяет предмет ведения
- Поддерживает несколько вопросов в одном обращении
"""

import sys
import io
# Fix UTF-8 output on Windows cp1251 console
sys.stdout.reconfigure(encoding='utf-8', errors='replace')
sys.stderr.reconfigure(encoding='utf-8', errors='replace')

import json
import math
import os
import re
# Set HF mirror before importing sentence_transformers (fixes SSL issues in corporate networks)
os.environ["HF_ENDPOINT"] = "https://hf-mirror.com"
os.environ["HF_HUB_DISABLE_SSL"] = "1"
# Fix Groq API connection - corporate proxy breaks Bearer auth
os.environ["NO_PROXY"] = "api.groq.com,*.groq.com"
os.environ["no_proxy"] = "api.groq.com,*.groq.com"

import numpy as np
import torch
# Limit memory to prevent OSError 1455 (page file exhaustion)
torch.set_num_threads(2)
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
    TOP_K_CANDIDATES, TOP_K_RESULT, MIN_CONFIDENCE, RETRIEVAL_POOL_SIZE, LEXICAL_POOL_SIZE,
    LLM_PROVIDER, GEMINI_API_KEY,
    OLLAMA_MODEL, OLLAMA_BASE_URL,
    ARIO_API_KEY, ARIO_BASE_URL, ARIO_MODEL,
    ENABLE_CROSS_ENCODER_RERANKER, CROSS_ENCODER_MODEL,
    ENABLE_HEURISTIC_RERANKER,
    ENABLE_QUERY_EXPANSION,
    HIERARCHY_BRANCH_WEIGHT, HIERARCHY_PARENT_WEIGHT,
    ENABLE_HIERARCHY_PRUNING, HIERARCHY_PRUNE_THRESHOLD,
    ENABLE_SECTION_ROUTING, SECTION_ROUTING_MAX_TOPICS,
    ENABLE_LTR_RERANKER, LTR_MODEL_PATH, LTR_WEIGHT,
    ENABLE_EMBEDDING_ADAPTER, ADAPTER_PATH,
    ENABLE_FULL_CLASSIFIER_FALLBACK, FULL_FALLBACK_SIM_THRESHOLD,
    ENABLE_REPEAT_DETECTION, REPEAT_APPEAL_CODE,
    ENABLE_WITHDRAWAL_DETECTION, WITHDRAWAL_APPEAL_CODE,
    ENABLE_L3_ROUTING, L3_ROUTING_MAX_THEMES,
    ENABLE_MULTI_QUERY_EXPAND, MQE_N_VARIANTS,
    ENABLE_ALLOWED_CODES, ALLOWED_CODES_PATH,
)
from hierarchy import (
    branch_agreement_scores, parent_similarity_boost,
    dominant_l1_sections, prefix_at_level,
)
from section_router import (
    build_routing_prompt, parse_routing_response,
    filter_candidates_by_l2, filter_candidates_by_l3,
    build_l3_routing_prompt, parse_l3_routing_response,
)
from appeals_logger import get_logger
from fio_extractor import normalize_fio


_WORD_RE = re.compile(r"[a-zа-яё0-9]{3,}", re.IGNORECASE)
_STOP_WORDS = {
    "это", "как", "или", "для", "при", "над", "под", "без", "уже", "еще",
    "прошу", "жалуюсь", "жалоба", "заявление", "обращение", "сообщить",
    "разъяснить", "провести", "проверку", "принять", "меры", "ситуацию",
    "дом", "дома", "улица", "улице", "года", "год", "лет", "день", "дней",
}

# ── Маркеры вопросов в обращении ───────────────────────────────────────────────
# Numbered: "1." "2)" "3 -" в начале строки
_NUMBERED_MARKER_RE = re.compile(r"(?m)^[ \t]*(\d{1,2})[ \t]*[\.\)][ \t]*")
# Bullets: "• * - ·" в начале строки
_BULLET_MARKER_RE = re.compile(r"(?m)^[ \t]*[•·∙\*\-][ \t]+")
# Вербальные маркеры начала нового вопроса
_VERBAL_MARKER_RE = re.compile(
    r"(?i)(?:^|[\.!\?]\s+|\n\s*)("
    r"во-первых|во-вторых|в-третьих|в-четвёртых|в-четвертых|в-пятых"
    r"|кроме того|помимо этого|также прошу|также жалуюсь|также сообщ"
    r"|во-первых,|во-вторых,|в-третьих,"
    r")",
    re.MULTILINE,
)
_PARAGRAPH_BREAK_RE = re.compile(r"\n[ \t]*\n+")
# Если фрагмент содержит хотя бы один из этих корней — это «содержательный» вопрос
_VERB_INDICATORS = (
    "прошу", "жалу", "жалоб", "заявл", "сообщ", "разъясн", "разобрат",
    "необходим", "требу", "помоч", "помощ", "оказан", "оказать", "оказыва",
    "вышл", "доставит", "доставк", "выясн", "уведомл", "обращ", "содейств",
    "снят", "лиш", "восстанов", "включ", "исключ", "выдать", "выдач",
    "пенс", "льгот", "субсид", "пособ", "оплат", "проезд", "транспорт",
    "лекарств", "инсулин", "инвалид", "телефон", "цифров", "обслуж",
)
_RUSSIAN_SUFFIXES = (
    "иями", "ями", "ами", "ого", "его", "ому", "ему", "ыми", "ими",
    "ая", "яя", "ое", "ее", "ые", "ие", "ых", "их", "ой", "ей",
    "ам", "ям", "ах", "ях", "ов", "ев", "ий", "ый", "ом", "ем",
    "а", "я", "ы", "и", "е", "у", "ю",
)
_QUERY_ALIASES = (
    ({"детск", "сад"}, {"дошкольн", "образован", "поступлен", "мест"}),
    ({"детск", "саду"}, {"дошкольн", "образован", "поступлен", "мест"}),
    ({"очеред", "сад"}, {"дошкольн", "образован", "поступлен", "мест"}),
    ({"очередь", "сад"}, {"дошкольн", "образован", "поступлен", "мест"}),
    ({"ижс"}, {"индивидуальн", "жилищн", "строительств"}),
    ({"земельн", "участк"}, {"земл", "геолог", "геодез"}),
    ({"стройк"}, {"строительств", "реконструкц"}),
)


# ── Сегментация обращения ──────────────────────────────────────────────────────

def _has_verb_indicator(text: str) -> bool:
    lower = text.lower()
    return any(v in lower for v in _VERB_INDICATORS)


def split_appeal_questions(text: str, max_segments: int = 7) -> list["AppealQuestion"]:
    """Conservative deterministic segmenter.

    Возвращает список вопросов. Один вопрос — если явных маркеров нет.
    Маркеры: "1." "2)" "•", "во-первых", "кроме того", и т.п.
    Преамбулу без глаголов-просьб клеит к первому вопросу.
    Хвост без явного маркера, если содержит ≥2 содержательных абзаца — разбивает на абзацы.
    """
    if not text or not text.strip():
        return []

    text = text.strip()

    primary_positions: set[int] = set()
    for m in _NUMBERED_MARKER_RE.finditer(text):
        primary_positions.add(m.start())
    for m in _BULLET_MARKER_RE.finditer(text):
        primary_positions.add(m.start())
    for m in _VERBAL_MARKER_RE.finditer(text):
        primary_positions.add(m.start(1))

    positions = sorted(primary_positions)

    if not positions:
        paragraphs = [p.strip() for p in _PARAGRAPH_BREAK_RE.split(text) if p.strip()]
        substantive = [p for p in paragraphs if len(p) >= 30 and _has_verb_indicator(p)]
        if len(substantive) >= 2:
            segs = substantive[:max_segments]
            return [
                AppealQuestion(text=s, ordinal=i + 1, evidence="paragraph")
                for i, s in enumerate(segs)
            ]
        return [AppealQuestion(text=text, ordinal=1, evidence="single")]

    # Каждый chunk начинается ОТ позиции маркера и идёт до следующего
    preamble = text[: positions[0]].strip() if positions[0] > 0 else ""
    segments_raw: list[str] = []
    for i, pos in enumerate(positions):
        end = positions[i + 1] if i + 1 < len(positions) else len(text)
        segments_raw.append(text[pos:end].strip())

    if preamble:
        has_verb = _has_verb_indicator(preamble)
        if not has_verb or len(preamble) < 40:
            if segments_raw:
                segments_raw[0] = (preamble + " " + segments_raw[0]).strip()
            else:
                segments_raw = [preamble]
        else:
            segments_raw = [preamble] + segments_raw

    # Если последний сегмент длинный — пробуем разбить его по абзацам
    if segments_raw and len(segments_raw[-1]) > 200 and "\n\n" in segments_raw[-1]:
        last = segments_raw[-1]
        sub_parts = [p.strip() for p in _PARAGRAPH_BREAK_RE.split(last) if p.strip()]
        substantive_subs = [p for p in sub_parts if len(p) >= 30 and _has_verb_indicator(p)]
        if len(substantive_subs) >= 2:
            segments_raw = segments_raw[:-1] + substantive_subs

    cleaned: list[str] = []
    for raw in segments_raw:
        s = raw.strip()
        s = _NUMBERED_MARKER_RE.sub("", s, count=1)
        s = _BULLET_MARKER_RE.sub("", s, count=1)
        s = s.strip()
        if len(s) >= 5:
            cleaned.append(s)

    if not cleaned:
        return [AppealQuestion(text=text, ordinal=1, evidence="single")]

    if len(cleaned) > max_segments:
        head = cleaned[: max_segments - 1]
        tail = " ".join(cleaned[max_segments - 1 :])
        cleaned = head + [tail]

    return [
        AppealQuestion(text=s, ordinal=i + 1, evidence="marker")
        for i, s in enumerate(cleaned)
    ]


# ── Модели данных ──────────────────────────────────────────────────────────────

@dataclass
class AppealQuestion:
    """Один выделенный вопрос в обращении (после сегментации)."""
    text: str
    ordinal: int
    evidence: str  # "single" | "marker" | "paragraph"


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
    # Служебные причины снижения confidence (для логов/отладки, НЕ показывать оператору)
    verification_reasons: list = field(default_factory=list)


def dedupe_questions(questions: list) -> list:
    """Схлопнуть вопросы с одинаковым кодом в одну карточку.

    LLM нередко дробит одно обращение на несколько «вопросов» и присваивает им
    один и тот же код — оператор видит 2-4 идентичные карточки (12 % обращений
    в выборке Заказчика на 68 обр.). Порядок первого появления кода сохраняется,
    уверенность берётся максимальная, тексты и обоснования склеиваются.
    """
    merged: dict = {}
    order: list = []
    for q in questions:
        kept = merged.get(q.code)
        if kept is None:
            merged[q.code] = q
            order.append(q.code)
            continue
        kept.confidence = max(kept.confidence, q.confidence)
        for attr, sep in (("question_text", "; "), ("reasoning", " ")):
            addition = (getattr(q, attr) or "").strip()
            current = (getattr(kept, attr) or "").strip()
            if addition and addition not in current:
                setattr(kept, attr, f"{current}{sep}{addition}" if current else addition)
        for reason in q.verification_reasons:
            if reason not in kept.verification_reasons:
                kept.verification_reasons.append(reason)
        if "merged_duplicate_questions" not in kept.verification_reasons:
            kept.verification_reasons.append("merged_duplicate_questions")
    return [merged[code] for code in order]


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
    llm_provider: str = field(default="")
    llm_model: str = field(default="")
    applicant_fio: Optional[str] = field(default=None)  # «Фамилия И.О.» или None
    summary: str = field(default="")                    # краткая суть, ≤250 символов
    full_fallback_used: bool = field(default=False)     # сработал ли full-classifier fallback


def extract_extra_fields(llm_result: dict) -> tuple[Optional[str], str]:
    """Из ответа LLM достаёт (applicant_fio «Фамилия И.О.», summary ≤250)."""
    fio = normalize_fio(llm_result.get("applicant_fio"))
    summary = (llm_result.get("summary") or "").strip()[:250]
    return fio, summary


# ── Промпты ────────────────────────────────────────────────────────────────────

SYSTEM_PROMPT = """Ты — эксперт по классификации обращений граждан РФ в соответствии с Федеральным законом №59-ФЗ «О порядке рассмотрения обращений граждан Российской Федерации».

Твоя задача:
1. Определить вид обращения (Жалоба, Заявление или Предложение)
2. Определить тип обращения (Индивидуальное, Коллективное или Анонимное)
3. Выявить все вопросы, содержащиеся в обращении
4. Для каждого вопроса подобрать наиболее подходящую категорию из предложенных кандидатов классификатора
5. Определить предмет ведения для каждого вопроса
6. Извлечь ФИО заявителя (если указано в тексте) и составить краткую суть обращения (до 250 символов)

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


QUERY_EXPANSION_SYSTEM = """Ты — эксперт по Общероссийскому классификатору вопросов обращений граждан.
Из вопроса гражданина извлеки официальную терминологию, которая используется в классификаторе.
Отвечай ОДНОЙ строкой через запятую — без вводных слов, без объяснений, без кавычек.
3-7 терминов или коротких словосочетаний, типичных для государственно-административной речи.
Примеры:
Вопрос: «Хочу в детский сад, очередь не двигается»
Ответ: Дошкольное образование, поступление детей в МДОУ, очередь в детский сад, муниципальные дошкольные образовательные учреждения

Вопрос: «Соседи шумят, участковый бездействует»
Ответ: Бездействие должностных лиц, рассмотрение обращений граждан, охрана общественного порядка, участковый уполномоченный полиции

Вопрос: «Нужна доставка лекарств на дом, инсулин»
Ответ: Лекарственное обеспечение, льготные лекарства, инвалидность, медицинская помощь на дому"""


QUERY_EXPANSION_USER_TEMPLATE = """Вопрос: «{question}»
Ответ:"""


# ── Multi-Query expansion: 3-5 разных формулировок одним LLM-вызовом ──────────
MULTI_QUERY_SYSTEM = """Ты — эксперт по Общероссийскому классификатору обращений граждан.

Твоя задача — сгенерировать N РАЗЛИЧНЫХ ФОРМУЛИРОВОК одного обращения, чтобы поиск по векторной базе классификатора нашёл правильный код с любого ракурса.

Каждая формулировка — короткая (5-15 слов), фокусируется на своём аспекте:
1. **ТЕМА** — о чём конкретно вопрос (предмет: дорога, лекарство, школа, инвалидность, и т.п.)
2. **ДЕЙСТВИЕ** — что просит/требует гражданин (отремонтировать, предоставить, разъяснить, ...)
3. **КЛАССИФИКАТОРНЫЙ СТИЛЬ** — формальная формулировка как в государственном перечне (например «Обращение с твёрдыми коммунальными отходами», «Установление группы инвалидности», «Благоустройство тротуаров»)
4. **СФЕРА** — отрасль (ЖКХ, здравоохранение, образование, соцобеспечение, ...)
5. (опц.) **ВЕДОМСТВО** — кому адресовано (УК, МСЭ, Минздрав, прокуратура, ...)

Формулировки не повторяются. Без воды, без «обращение гражданина о...».

Отвечай СТРОГО в JSON: {"variants": ["формулировка 1", "формулировка 2", ...]}"""


MULTI_QUERY_USER_TEMPLATE = """Обращение: «{question}»

Сгенерируй {n} различных формулировок. JSON-ответ:"""


CLASSIFICATION_PROMPT_TEMPLATE_L3GROUP = """Классифицируй следующее обращение гражданина.

ТЕКСТ ОБРАЩЕНИЯ:
{appeal_text}

КАНДИДАТЫ ПО ВОПРОСАМ (сгруппированы по L3-теме):
{questions_json}

ЭТАП 1 — ВЫБОР L3-ТЕМЫ (обязательно):
Для каждого вопроса СНАЧАЛА определи, к какой L3-теме относится вопрос, выбирая из списка "l3_options".
L3-тема — это тематическое направление (например "Благоустройство территорий", "Обеспечение лекарствами").
В поле "selected_l3_code" запиши код выбранной L3-темы.

ЭТАП 2 — ВЫБОР КОНКРЕТНОГО КОДА (из выбранной L3):
Затем выбери наиболее подходящий код классификатора из кандидатов выбранной L3-темы.
Поле "selected_code" — финальный код из списка candidates.

ПРАВИЛА:

1. L3-ТЕМА определяется первым — это тематика вопроса (а не конкретный вопрос).
   Пример: вопрос "в доме протекает крыша" → L3 "Благоустройство" (0003.0009.0097),
   а код — "Ремонт кровли" (конкретный leaf).

2. Если в questions_json нет candidates с correct_l3 или candidates пустые —
   выбери наиболее близкую L3-тему из l3_options и поставь confidence <= 0.5.

3. ТИПОВЫЕ РАЗГРАНИЧЕНИЯ (как отличить):
   • 0096 vs 0097: новое строительство/реконструкция → 0096; ремонт/благоустройство → 0097
   • 0007 vs 0014: пенсии/льготы/соцработник → 0007; лекарства/поликлиники/медпомощь → 0014
   • 0004 vs 0013: опека/алименты/маткапитал → 0004; детсад/школа/поступление → 0013
   • ЖКХ (0005.0005.0056) vs инфраструктура поселений (0003.0009.0097):
     проблема внутри дома, с УК/ТСЖ, начислениями, внутридомовыми сетями → ЖКХ 0005.0005.0056;
     сети и объекты поселения в целом (водоснабжение посёлка, уличные колонки, магистрали) → 0003.0009.0097
   • Автодороги (0003.0009.0099) vs благоустройство (0003.0009.0097):
     дороги общего пользования, улицы, трассы — их состояние, содержание, расчистка снега → 0099;
     дворы, придомовые и подъездные территории, тротуары → 0097

4. Запрещено: один код на разные вопросы; fallback "0001.0002.0027.0126" при наличии альтернатив.

4b. ПРИЗНАК НЕДОСТАТОЧНОСТИ КАНДИДАТОВ: если для какого-либо вопроса среди
   предложенных кандидатов НЕТ нужной тематической ветки (например, обращение о
   здравоохранении, а в кандидатах только соцобеспечение/ЖКХ) — установи
   верхнеуровневое поле "candidates_insufficient": true. Это значит, что векторный
   поиск промахнулся и нужен повторный проход по полному классификатору.

4c. ПРИЗНАК ПОВТОРНОГО ОБРАЩЕНИЯ: если из текста видно, что гражданин обращается
   НЕ впервые (например: «обращаюсь повторно», «не в первый раз», «ответа так и не
   получил», «ранее обращался», «на моё предыдущее обращение») — установи
   верхнеуровневое поле "is_repeat_appeal": true. Тематическую классификацию при
   этом НЕ меняй (повторность — отдельный признак, обрабатывается отдельно).

4d. ПРОСЬБА ПРЕКРАТИТЬ РАССМОТРЕНИЕ: если гражданин просит прекратить рассмотрение,
   отозвать обращение/письмо/заявление, снять его с рассмотрения или сообщает, что
   вопрос снят и рассмотрение не требуется — установи верхнеуровневое поле
   "is_withdrawal_request": true. Тематическую классификацию при этом НЕ меняй
   (просьба о прекращении — отдельный признак, обрабатывается отдельно).

5. АЛЬТЕРНАТИВНЫЕ КОДЫ (обязательно 3 — страховка оператора, он выбирает из трёх).
   Портфель альтернатив должен покрывать РАЗНЫЕ типы возможной ошибки selected_code:
   - Альтернатива 1: ближайший по смыслу СОСЕД selected_code в той же L3-теме
     (страхует случай: тема верна, но конкретный вопрос выбран не тот).
   - Альтернатива 2: лучший кандидат из ДРУГОЙ правдоподобной L3-темы — твой второй выбор Этапа 1
     (страхует случай: тема выбрана неверно).
   - Альтернатива 3: если selected_code — уточняющий код 5-го уровня (5 сегментов) —
     обязательно его родительский код 4-го уровня; иначе ещё один сильный кандидат из любой темы.
   Пустой массив — только если candidates содержит единственный код.

Верни JSON строго в формате:
{{
  "vid_obrascheniya": "Жалоба|Заявление|предложение",
  "tip_obrascheniya": "Индивидуальное|Коллективное|Анонимное",
  "is_ustnoe": false,
  "candidates_insufficient": false,
  "is_repeat_appeal": false,
  "is_withdrawal_request": false,
  "applicant_fio": "Фамилия Имя Отчество заявителя из текста, либо null",
  "summary": "Краткая суть обращения, не более 250 символов",
  "questions": [
    {{
      "ordinal": 1,
      "question_text": "...",
      "l3_options": ["XXXX.XXXX.XXXX ...", ...],
      "selected_l3_code": "XXXX.XXXX.XXXX",
      "selected_code": "XXXX.XXXX.XXXX.XXXX",
      "alternative_codes": ["XXXX.XXXX.XXXX.XXXX", "XXXX.XXXX.XXXX.XXXX"],
      "predmet_vedeniya": "...",
      "confidence": 0.87,
      "reasoning": "..."
    }}
  ]
}}"""


# ── Full-classifier fallback: весь классификатор в контексте ────────────────────
# Используется, когда ретривер промахнулся (нет близких кандидатов). Вместо узкого
# пула показываем модели ПОЛНЫЙ список листьев классификатора; она сегментирует
# обращение и выбирает selected_code напрямую из полного перечня.
FULL_CLASSIFIER_FALLBACK_TEMPLATE = """Классифицируй обращение гражданина.

ВНИМАНИЕ: векторный поиск не нашёл близких кандидатов, поэтому ниже — ПОЛНЫЙ
перечень кодов классификатора (листья). Выбирай selected_code ТОЛЬКО из него.

ТЕКСТ ОБРАЩЕНИЯ:
{appeal_text}

ПОЛНЫЙ КЛАССИФИКАТОР (код — название — путь):
{full_classifier}

ПРАВИЛА:
1. Раздели обращение на отдельные вопросы (если их несколько).
2. Для каждого вопроса выбери НАИБОЛЕЕ точный код из полного перечня выше.
   selected_code ОБЯЗАН быть кодом из перечня (скопируй точь-в-точь).
3. alternative_codes — до 3 запасных кодов из перечня (страховка оператора).
4. Один код не должен повторяться для разных вопросов.

Верни JSON строго в формате:
{{
  "vid_obrascheniya": "Жалоба|Заявление|предложение",
  "tip_obrascheniya": "Индивидуальное|Коллективное|Анонимное",
  "is_ustnoe": false,
  "applicant_fio": "Фамилия Имя Отчество заявителя из текста, либо null",
  "summary": "Краткая суть обращения, не более 250 символов",
  "questions": [
    {{
      "ordinal": 1,
      "question_text": "...",
      "selected_code": "XXXX.XXXX.XXXX.XXXX",
      "alternative_codes": ["XXXX.XXXX.XXXX.XXXX", "XXXX.XXXX.XXXX.XXXX"],
      "predmet_vedeniya": "...",
      "confidence": 0.87,
      "reasoning": "..."
    }}
  ]
}}"""


# ── Детекция повторного обращения ───────────────────────────────────────────────
import re as _re

# Маркеры повторного обращения (гражданин уже обращался / не получил ответа).
_REPEAT_MARKERS = _re.compile(
    r"повторн"
    r"|не\s+в\s+перв\w+\s+раз"
    r"|ответ\w*\s+(?:так\s+и\s+)?не\s+(?:получ|дал|присл|поступ|пришл)"
    r"|ране[ей]\s+(?:уже\s+)?(?:обраща|писа|направля|подава)"
    r"|уже\s+(?:обраща|писа|направля|подава)"
    r"|(?:предыдущ|прошл)\w*\s+обращени"
    r"|на\s+(?:мо[её]|наше)\s+(?:предыдущее|прошлое|прежнее)\s+обращени"
    r"|обраща\w+\s+(?:к\s+вам\s+)?(?:вновь|снова)",
    _re.IGNORECASE,
)


def detect_repeat_markers(text: str) -> bool:
    """True, если в тексте есть явные маркеры повторного обращения
    («повторно», «не в первый раз», «ответа не получил», «ранее обращался» и т.п.)."""
    return bool(text and _REPEAT_MARKERS.search(text))


# ── Детекция просьбы прекратить рассмотрение / отозвать обращение ───────────────

# Объект действия обязателен: «прекратить»/«отозвать» сами по себе встречаются
# и в обычных обращениях («прекратить незаконное строительство», «отозвать
# лицензию»), поэтому маркер срабатывает только рядом с обращением/заявлением.
_APPEAL_OBJ = r"(?:обращени|заявлени|письм|жалоб|запрос)"
_WITHDRAWAL_MARKERS = _re.compile(
    rf"прекрат\w*\s+рассмотрени"
    rf"|снят[ьи]?\s+с\s+рассмотрени"
    rf"|не\s+рассматрива\w*\s+(?:мо[её]\w*\s+|сво[её]\w*\s+)?{_APPEAL_OBJ}"
    rf"|отозва\w*\s+(?:мо[её]\w*\s+|сво[её]\w*\s+|ранее\s+)?{_APPEAL_OBJ}"
    rf"|отзыва\w*\s+(?:мо[её]\w*\s+|сво[её]\w*\s+)?{_APPEAL_OBJ}"
    rf"|аннулиров\w*\s+(?:мо[её]\w*\s+|сво[её]\w*\s+)?{_APPEAL_OBJ}"
    rf"|отказыва\w*\s+от\s+(?:ранее\s+)?(?:поданн\w*\s+|направленн\w*\s+)?{_APPEAL_OBJ}"
    rf"|закрыть\s+(?:мо[её]\w*\s+|сво[её]\w*\s+)?{_APPEAL_OBJ}",
    _re.IGNORECASE,
)


def detect_withdrawal_markers(text: str) -> bool:
    """True, если гражданин просит прекратить рассмотрение или отозвать обращение
    («прошу прекратить рассмотрение», «отзываю заявление», «снять с рассмотрения»)."""
    return bool(text and _WITHDRAWAL_MARKERS.search(text))


# ── Класс агента ───────────────────────────────────────────────────────────────

class ClassifierAgent:
    def __init__(self):
        print("Инициализация агента классификации...")

        # LLM client (Groq, Gemini, Ollama или Ario)
        self.llm = LLM_PROVIDER if LLM_PROVIDER in {"groq", "gemini", "ollama", "ario"} else "groq"
        if self.llm == "gemini":
            self.gemini = genai_client.Client(api_key=GEMINI_API_KEY)
            print(f"  Model LLM: gemini-2.5-flash (Google Gemini)")
        elif self.llm == "ollama":
            import httpx
            self._ollama_client = httpx.Client(base_url=OLLAMA_BASE_URL, timeout=120)
            print(f"  Model LLM: {OLLAMA_MODEL} (Ollama)")
        elif self.llm == "ario":
            import httpx
            # Per-call httpx client (avoid sharing state with HF Hub)
            print(f"  Model LLM: {ARIO_MODEL} (Ario)")
        else:
            self.groq = Groq(api_key=GROQ_API_KEY)
            print(f"  Model LLM: {GROQ_MODEL} (Groq)")

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

        # CrossEncoder reranker — lazy-load, только если флаг включён
        self.ce_reranker = None
        if ENABLE_CROSS_ENCODER_RERANKER:
            from reranker import CrossEncoderReranker
            self.ce_reranker = CrossEncoderReranker(CROSS_ENCODER_MODEL)
            print(f"  CE reranker enabled: {CROSS_ENCODER_MODEL} (lazy load at first use)")

        # Learning-to-Rank weights — sklearn LogReg обученный на ii25_train
        self.ltr_weights: dict | None = None
        if ENABLE_LTR_RERANKER:
            ltr_path = Path(LTR_MODEL_PATH)
            if ltr_path.exists():
                with open(ltr_path, encoding="utf-8") as f:
                    self.ltr_weights = json.load(f)
                print(f"  LtR reranker enabled: {ltr_path.name} (weight={LTR_WEIGHT})")
            else:
                print(f"  LtR enabled but model not found at {ltr_path} — disabled")

        # Allowed codes whitelist — ограничение пула «горячими» кодами (60% обращений в НОР)
        self.allowed_codes: set[str] | None = None
        if ENABLE_ALLOWED_CODES:
            allowed_path = Path(ALLOWED_CODES_PATH)
            if allowed_path.exists():
                with open(allowed_path, encoding="utf-8") as f:
                    data = json.load(f)
                codes_list = data.get("codes", []) if isinstance(data, dict) else data
                self.allowed_codes = {c for c in codes_list if isinstance(c, str)}
                print(f"  Allowed codes whitelist enabled: {len(self.allowed_codes)} кодов из {allowed_path.name}")
            else:
                print(f"  Allowed codes enabled but file not found at {allowed_path}")

        # Embedding adapter — Linear(768, 768) обученный на ii25_train с InfoNCE
        # Применяется к query embedding ПОСЛЕ e5. Vector DB должна быть пересобрана
        # через тот же адаптер (см. scripts/apply_adapter.py).
        self.adapter_W: np.ndarray | None = None
        self.adapter_b: np.ndarray | None = None
        if ENABLE_EMBEDDING_ADAPTER:
            adapter_path = Path(ADAPTER_PATH)
            if adapter_path.exists():
                data = np.load(adapter_path)
                self.adapter_W = data["W"].astype(np.float32)
                self.adapter_b = data["b"].astype(np.float32)
                print(f"  Embedding adapter enabled: {adapter_path.name} (W={self.adapter_W.shape})")
            else:
                print(f"  Adapter enabled but file not found at {adapter_path} — disabled")

    def _resolve_llm(self, llm_provider: str | None = None, llm_model: str | None = None) -> tuple[str, str]:
        """Return provider/model for a request, falling back to .env defaults."""
        provider = (llm_provider or self.llm or "groq").strip().lower()
        defaults = {
            "groq": GROQ_MODEL,
            "gemini": "gemini-2.5-flash",
            "ollama": OLLAMA_MODEL,
            "ario": ARIO_MODEL,
        }
        if provider not in defaults:
            raise ValueError(f"Неподдерживаемый LLM provider: {provider}")
        model = (llm_model or defaults[provider]).strip()
        return provider, model

    def _get_groq_client(self):
        if not hasattr(self, "groq"):
            self.groq = Groq(api_key=GROQ_API_KEY)
        return self.groq

    def _get_gemini_client(self):
        if not hasattr(self, "gemini"):
            self.gemini = genai_client.Client(api_key=GEMINI_API_KEY)
        return self.gemini

    def _get_ollama_client(self):
        if not hasattr(self, "_ollama_client"):
            import httpx
            self._ollama_client = httpx.Client(base_url=OLLAMA_BASE_URL, timeout=120)
        return self._ollama_client

    def _embed_query(self, text: str) -> np.ndarray:
        """Векторизация запроса. Префикс "query: " только для e5-семейства
        (BGE-M3 и др. современные модели префиксов не требуют — см. модель-карту).
        Если включён embedding adapter — применяется его проекция."""
        formatted = f"query: {text}" if "e5" in EMBEDDING_MODEL.lower() else text
        emb = self.embedder.encode(formatted, normalize_embeddings=True)
        if self.adapter_W is not None:
            emb = emb @ self.adapter_W.T + self.adapter_b
            n = float(np.linalg.norm(emb))
            if n > 1e-12:
                emb = emb / n
        return emb

    def _ario_call(
        self,
        *,
        messages: list[dict],
        model: str | None = None,
        max_tokens: int | None = 800,
        temperature: float = 0.0,
        timeout: int = 60,
        schema: dict | None = None,
    ) -> str:
        """Centralized Ario httpx call with optional post-hoc schema validation.

        Calls Ario (vLLM-backed) chat completions endpoint, strips common ```json
        wrappers from the response, and optionally validates parsed JSON against the
        given jsonschema. Validation failure logs a warning but still returns the raw
        string — caller decides whether to retry or fallback.

        Args:
            messages: OpenAI-style messages list.
            model: model name (defaults to ARIO_MODEL).
            max_tokens: max tokens to generate. Pass None to omit (use server default).
            temperature: sampling temperature.
            timeout: httpx client timeout in seconds.
            schema: optional jsonschema dict; if provided, parsed JSON is validated.

        Returns:
            Raw string content from the LLM (with wrappers stripped if any).
        """
        import httpx
        import json as _json

        payload: dict = {
            "model": model or ARIO_MODEL,
            "messages": messages,
            "temperature": temperature,
        }
        if max_tokens is not None:
            payload["max_tokens"] = max_tokens

        client = httpx.Client(
            base_url=ARIO_BASE_URL,
            headers={"Authorization": f"Bearer {ARIO_API_KEY}"},
            timeout=timeout,
        )
        try:
            r = client.post("/chat/completions", json=payload)
            if r.status_code >= 400:
                # Capture body BEFORE raise_for_status discards it
                body_preview = (r.text or "")[:500]
                msgs_len = sum(len(m.get("content", "")) for m in messages)
                print(
                    f"  [Ario] HTTP {r.status_code} on /chat/completions "
                    f"(payload: {len(messages)} msgs, ~{msgs_len} chars total). "
                    f"Response body: {body_preview}"
                )
            r.raise_for_status()
            raw = r.json()["choices"][0]["message"]["content"].strip()
        finally:
            client.close()

        # Strip common ```json wrappers
        for prefix in ("```json", "```"):
            if raw.startswith(prefix):
                raw = raw[len(prefix):].strip()
        if raw.endswith("```"):
            raw = raw[:-3].strip()

        # Optional post-hoc schema validation (best-effort)
        if schema is not None:
            try:
                import jsonschema
                parsed = _json.loads(raw)
                jsonschema.validate(parsed, schema)
            except _json.JSONDecodeError as exc:
                print(f"  [Ario] WARN: response is not valid JSON: {exc}")
            except jsonschema.ValidationError as exc:
                print(f"  [Ario] WARN: response does not match schema: {exc.message}")
            except ImportError:
                pass  # jsonschema not installed, skip validation

        return raw

    def _split_appeal_questions(self, text: str, max_segments: int = 7) -> list[AppealQuestion]:
        """Делегирует module-level функции — позволяет тестировать без агента."""
        return split_appeal_questions(text, max_segments=max_segments)

    def _normalize_token(self, token: str) -> str:
        token = token.lower().replace("ё", "е")
        for suffix in _RUSSIAN_SUFFIXES:
            if len(token) > len(suffix) + 3 and token.endswith(suffix):
                return token[: -len(suffix)]
        return token

    def _tokenize_for_rerank(self, text: str) -> set[str]:
        tokens = set()
        for raw in _WORD_RE.findall(text or ""):
            token = self._normalize_token(raw)
            if token and token not in _STOP_WORDS:
                tokens.add(token)
        return tokens

    def _query_tokens_for_rerank(self, query_text: str) -> set[str]:
        tokens = self._tokenize_for_rerank(query_text)
        for required, aliases in _QUERY_ALIASES:
            if required.issubset(tokens):
                tokens.update(aliases)
        return tokens

    def _lexical_score(self, query_tokens: set[str], candidate: dict) -> float:
        if not query_tokens:
            return 0.0

        candidate_text = " ".join([
            str(candidate.get("name", "")),
            str(candidate.get("full_path", "")),
        ])
        candidate_tokens = self._tokenize_for_rerank(candidate_text)
        if not candidate_tokens:
            return 0.0

        overlap = query_tokens & candidate_tokens
        if not overlap:
            return 0.0

        name_tokens = self._tokenize_for_rerank(str(candidate.get("name", "")))
        weighted_overlap = sum(2.0 if token in name_tokens else 1.0 for token in overlap)
        return min(1.0, weighted_overlap / max(len(query_tokens), 1))

    def _ltr_score(self, features: dict) -> float:
        """Sigmoid(LogReg(features)) ∈ [0, 1]. Возвращает 0 если LtR не загружен."""
        if not self.ltr_weights:
            return 0.0
        w = self.ltr_weights
        z = float(w.get("intercept", 0.0))
        for key, coef in zip(w["features"], w["coef"]):
            z += float(features.get(key, 0.0)) * float(coef)
        if z >= 0:
            return 1.0 / (1.0 + math.exp(-z))
        ez = math.exp(z)
        return ez / (1.0 + ez)

    def _rerank_candidates(self, query_text: str, candidates: list, top_k: int = TOP_K_CANDIDATES) -> list:
        """Compress wider pool with lexical + hierarchy signals before LLM."""
        if not candidates:
            return []

        # Optional L1-pruning: если 1-2 раздела доминируют — отсекаем другие.
        # Должно идти ДО boost-расчётов, чтобы branch_scores считались уже на отфильтрованном пуле.
        working = candidates
        if ENABLE_HIERARCHY_PRUNING:
            dominant = dominant_l1_sections(candidates, threshold=HIERARCHY_PRUNE_THRESHOLD)
            if dominant:
                kept = [c for c in candidates if prefix_at_level(c["code"], 1) in dominant]
                if len(kept) >= max(top_k, 5):  # не оставляем меньше top-k кандидатов
                    working = kept

        query_tokens = self._query_tokens_for_rerank(query_text)
        lexical_weight = float(os.getenv("LEXICAL_RERANK_WEIGHT", "0.12"))

        # Hierarchy scores (one pass over working)
        branch_scores = branch_agreement_scores(working, level=2) if HIERARCHY_BRANCH_WEIGHT > 0 else {}
        parent_boosts = parent_similarity_boost(working) if HIERARCHY_PARENT_WEIGHT > 0 else {}

        scored = []
        for idx, candidate in enumerate(working):
            dense_score = float(candidate.get("similarity", 0.0))
            lexical_score = self._lexical_score(query_tokens, candidate)
            level_bonus = 0.01 if int(candidate.get("level", 0) or 0) >= 4 else 0.0
            branch_score = branch_scores.get(candidate["code"], 0.0)
            parent_score = parent_boosts.get(candidate["code"], 0.0)

            combined_score = (
                dense_score
                + lexical_weight * lexical_score
                + level_bonus
                + HIERARCHY_BRANCH_WEIGHT * branch_score
                + HIERARCHY_PARENT_WEIGHT * parent_score
            )

            # LtR score — sklearn-LogReg обученный на ии25_train
            ltr_score_val = 0.0
            if self.ltr_weights is not None:
                level_val = int(candidate.get("level", 0) or 0)
                ltr_features = {
                    "dense_score": dense_score,
                    "lexical_score": lexical_score,
                    "branch_score": branch_score,
                    "parent_score": parent_score,
                    "level": level_val,
                    "is_l4": 1 if level_val == 4 else 0,
                    "is_l5": 1 if level_val == 5 else 0,
                }
                ltr_score_val = self._ltr_score(ltr_features)
                combined_score += LTR_WEIGHT * ltr_score_val

            enriched = dict(candidate)
            enriched["rerank_score"] = round(float(combined_score), 6)
            enriched["lexical_score"] = round(float(lexical_score), 6)
            enriched["branch_score"] = round(float(branch_score), 6)
            enriched["parent_score"] = round(float(parent_score), 6)
            if self.ltr_weights is not None:
                enriched["ltr_score"] = round(float(ltr_score_val), 6)
            scored.append((combined_score, idx, enriched))

        scored.sort(key=lambda item: (-item[0], item[1]))
        return [candidate for _, _, candidate in scored[:top_k]]

    def _candidate_from_metadata(self, meta: dict, similarity: float, source: str = "dense") -> dict:
        return {
            "code":       meta["code"],
            "name":       meta["name"],
            "level":      meta["level"],
            "full_path":  meta["full_path"],
            "parent_code": meta.get("parent_code", ""),
            "similarity": round(float(similarity), 3),
            "source":     source,
        }

    def _search_lexical_candidates(self, query_text: str, top_k: int = LEXICAL_POOL_SIZE) -> list:
        """Find candidates by token overlap across the full classifier."""
        query_tokens = self._query_tokens_for_rerank(query_text)
        if not query_tokens:
            return []

        scored = []
        for idx, meta in enumerate(self.metadata):
            score = self._lexical_score(query_tokens, meta)
            if score <= 0:
                continue
            scored.append((score, idx, meta))

        scored.sort(key=lambda item: (-item[0], item[1]))
        candidates = []
        for score, _, meta in scored[:top_k]:
            synthetic_similarity = 0.76 + 0.25 * score
            candidates.append(self._candidate_from_metadata(meta, synthetic_similarity, source="lexical"))
        return candidates

    def _merge_candidate_pools(self, *pools: list) -> list:
        """Merge candidate pools by code while keeping the strongest score."""
        merged: dict[str, dict] = {}
        order: list[str] = []
        for pool in pools:
            for candidate in pool:
                code = candidate["code"]
                if code not in merged:
                    merged[code] = candidate
                    order.append(code)
                    continue
                if float(candidate.get("similarity", 0.0)) > float(merged[code].get("similarity", 0.0)):
                    merged[code] = candidate
        return [merged[code] for code in order]

    def _candidate_score(self, candidate: dict | None) -> float:
        if not candidate:
            return 0.0
        return float(candidate.get("rerank_score", candidate.get("similarity", 0.0)) or 0.0)

    def _calibrate_question_confidence(
        self,
        *,
        llm_confidence: float,
        selected_code: str,
        candidates: list[dict],
        invalid_code: bool = False,
        duplicate_code: bool = False,
    ) -> tuple[float, list[str]]:
        """Make confidence reflect retrieval evidence, not only LLM self-score."""
        confidence = max(0.0, min(1.0, float(llm_confidence or 0.0)))
        reasons: list[str] = []

        if invalid_code:
            confidence = min(confidence, 0.45)
            reasons.append("llm_code_outside_candidates")

        if duplicate_code:
            confidence = min(confidence, 0.50)
            reasons.append("same_code_for_multiple_questions")

        if not candidates:
            confidence = min(confidence, 0.50)
            reasons.append("empty_candidate_pool")
            return confidence, reasons

        selected_idx = next(
            (idx for idx, candidate in enumerate(candidates) if candidate.get("code") == selected_code),
            None,
        )
        if selected_idx is None:
            confidence = min(confidence, 0.45)
            reasons.append("selected_code_not_in_final_candidates")
            return confidence, reasons

        rank = selected_idx + 1
        if rank > 1:
            confidence = min(confidence, 0.62 if rank <= 3 else 0.55)
            reasons.append(f"selected_candidate_rank_{rank}")

        selected_score = self._candidate_score(candidates[selected_idx])
        top_score = self._candidate_score(candidates[0])
        margin_to_top = top_score - selected_score
        if rank > 1 and margin_to_top >= 0.03:
            confidence = min(confidence, 0.55)
            reasons.append("selected_below_top_with_margin")

        if len(candidates) > 1:
            top_margin = self._candidate_score(candidates[0]) - self._candidate_score(candidates[1])
            if rank == 1 and top_margin < 0.015:
                confidence = min(confidence, 0.62)
                reasons.append("low_top_margin")

        return confidence, reasons

    def _search_candidates(self, query_text: str, top_k: int | None = None) -> list:
        """Семантический поиск кандидатов через cosine similarity (numpy)"""
        query_vec = self._embed_query(query_text)
        similarities = self.embeddings @ query_vec
        limit = top_k or TOP_K_CANDIDATES
        top_indices = np.argsort(similarities)[::-1][:limit]

        candidates = []
        for idx in top_indices:
            meta = self.metadata[idx]
            candidates.append(self._candidate_from_metadata(meta, similarities[idx]))
        return candidates

    def _route_to_sections(self, appeal_text: str, provider: str, model: str) -> list[str]:
        """Coarse-to-fine: LLM выбирает 1-3 L2-тематик на основе официальной методички.

        Returns list of L2 codes (XXXX.XXXX). Empty list = router failed → no filtering.
        Один дешёвый LLM-вызов с фиксированным catalog в промпте.
        """
        sys_msg, user_msg = build_routing_prompt(appeal_text, max_topics=SECTION_ROUTING_MAX_TOPICS)
        try:
            if provider == "gemini":
                response = self._get_gemini_client().models.generate_content(
                    model=model,
                    contents=user_msg,
                    config=GenerateContentConfig(
                        system_instruction=sys_msg,
                        temperature=0.0,
                        max_output_tokens=200,
                    ),
                )
                raw = (response.text or "").strip()
            elif provider == "ollama":
                r = self._get_ollama_client().post(
                    "/chat/completions",
                    json={
                        "model": model,
                        "messages": [
                            {"role": "system", "content": sys_msg},
                            {"role": "user", "content": user_msg},
                        ],
                        "temperature": 0.0,
                        "max_tokens": 200,
                    },
                )
                r.raise_for_status()
                raw = r.json()["choices"][0]["message"]["content"].strip()
            elif provider == "ario":
                raw = self._ario_call(
                    messages=[
                        {"role": "system", "content": sys_msg},
                        {"role": "user", "content": user_msg},
                    ],
                    model=model,
                    max_tokens=200,
                )
            else:
                r = self._get_groq_client().chat.completions.create(
                    model=model,
                    messages=[
                        {"role": "system", "content": sys_msg},
                        {"role": "user", "content": user_msg},
                    ],
                    temperature=0.0,
                    max_tokens=200,
                    response_format={"type": "json_object"},
                )
                raw = r.choices[0].message.content.strip()
        except Exception as e:
            print(f"  [Router] error: {e}")
            return []

        topics = parse_routing_response(raw)
        if topics:
            print(f"  [Router] выбрано: {topics}")
        else:
            print(f"  [Router] не распознан ответ → fallback без фильтрации: {raw[:120]}")
        return topics

    def _route_to_l3_themes(
        self,
        appeal_text: str,
        allowed_l2: list[str],
        provider: str,
        model: str,
    ) -> list[str]:
        """Внутри выбранных L2-тематик выбрать 1-3 L3-темы. Возвращает XXXX.XXXX.XXXX коды.

        Empty list if router fails — no L3 filtering applied.
        """
        if not allowed_l2:
            return []

        # Собираем L3-опции внутри allowed_l2 (level=3 записи)
        allowed_set = set(allowed_l2)
        l3_options: list[tuple[str, str]] = []
        for meta in self.metadata:
            if str(meta.get("level", "")) != "3":
                continue
            code = meta["code"]
            parts = code.split(".")
            if len(parts) >= 3:
                l2 = ".".join(parts[:2])
                if l2 in allowed_set:
                    l3_code = ".".join(parts[:3])
                    l3_options.append((l3_code, meta["name"]))
        if not l3_options:
            return []

        sys_msg, user_msg = build_l3_routing_prompt(
            appeal_text, l3_options, max_themes=L3_ROUTING_MAX_THEMES
        )
        try:
            if provider == "gemini":
                response = self._get_gemini_client().models.generate_content(
                    model=model,
                    contents=user_msg,
                    config=GenerateContentConfig(
                        system_instruction=sys_msg,
                        temperature=0.0,
                        max_output_tokens=200,
                    ),
                )
                raw = (response.text or "").strip()
            elif provider == "ollama":
                r = self._get_ollama_client().post(
                    "/chat/completions",
                    json={
                        "model": model,
                        "messages": [
                            {"role": "system", "content": sys_msg},
                            {"role": "user", "content": user_msg},
                        ],
                        "temperature": 0.0,
                        "max_tokens": 200,
                    },
                )
                r.raise_for_status()
                raw = r.json()["choices"][0]["message"]["content"].strip()
            elif provider == "ario":
                raw = self._ario_call(
                    messages=[
                        {"role": "system", "content": sys_msg},
                        {"role": "user", "content": user_msg},
                    ],
                    model=model,
                    max_tokens=200,
                )
            else:
                r = self._get_groq_client().chat.completions.create(
                    model=model,
                    messages=[
                        {"role": "system", "content": sys_msg},
                        {"role": "user", "content": user_msg},
                    ],
                    temperature=0.0,
                    max_tokens=200,
                    response_format={"type": "json_object"},
                )
                raw = r.choices[0].message.content.strip()
        except Exception as e:
            print(f"  [L3-Router] error: {e}")
            return []

        themes = parse_l3_routing_response(raw)
        # Validate — оставить только те L3 которые в нашем allowed_set
        themes_valid = [
            t for t in themes
            if len(t.split(".")) >= 3 and ".".join(t.split(".")[:2]) in allowed_set
        ]
        if themes_valid:
            print(f"  [L3-Router] выбрано: {themes_valid}")
        else:
            print(f"  [L3-Router] не распознан / не валиден ответ: {raw[:120]}")
        return themes_valid

    def _multi_query_expand(self, question_text: str, provider: str, model: str, n: int = MQE_N_VARIANTS) -> list[str]:
        """LLM генерирует N разных формулировок одного обращения (тема/действие/классификаторный
        стиль/сфера). Возвращает список строк. На ошибке — пустой список."""
        user_msg = MULTI_QUERY_USER_TEMPLATE.format(question=question_text[:500], n=n)
        max_tokens = 60 * n + 40

        try:
            if provider == "gemini":
                response = self._get_gemini_client().models.generate_content(
                    model=model,
                    contents=user_msg,
                    config=GenerateContentConfig(
                        system_instruction=MULTI_QUERY_SYSTEM,
                        temperature=0.3,
                        max_output_tokens=max_tokens,
                    ),
                )
                raw = (response.text or "").strip()
            elif provider == "ollama":
                r = self._get_ollama_client().post(
                    "/chat/completions",
                    json={
                        "model": model,
                        "messages": [
                            {"role": "system", "content": MULTI_QUERY_SYSTEM},
                            {"role": "user", "content": user_msg},
                        ],
                        "temperature": 0.3,
                        "max_tokens": max_tokens,
                    },
                )
                r.raise_for_status()
                raw = r.json()["choices"][0]["message"]["content"].strip()
            elif provider == "ario":
                raw = self._ario_call(
                    messages=[
                        {"role": "system", "content": MULTI_QUERY_SYSTEM},
                        {"role": "user", "content": user_msg},
                    ],
                    model=model,
                    max_tokens=max_tokens,
                    temperature=0.3,
                )
            else:
                r = self._get_groq_client().chat.completions.create(
                    model=model,
                    messages=[
                        {"role": "system", "content": MULTI_QUERY_SYSTEM},
                        {"role": "user", "content": user_msg},
                    ],
                    temperature=0.3,
                    max_tokens=max_tokens,
                    response_format={"type": "json_object"},
                )
                raw = r.choices[0].message.content.strip()
        except Exception as e:
            print(f"  [MQE] expand failed: {e}")
            return []

        try:
            data = json.loads(raw)
            variants = data.get("variants", [])
            if isinstance(variants, list):
                out = [str(v).strip() for v in variants if isinstance(v, str) and v.strip()]
                return out[:n]
        except (json.JSONDecodeError, KeyError, TypeError):
            pass
        # Fallback: split by lines
        lines = [l.strip(' "-•*\t') for l in raw.split("\n") if l.strip()]
        return [l for l in lines if len(l) > 5][:n]

    def _expand_query(self, question_text: str, provider: str, model: str) -> str:
        """LLM query expansion — переформулировать вопрос в терминах классификатора.

        Возвращает строку с дополнительными терминами; пустая строка при ошибке.
        Дешёвый LLM-вызов с маленьким max_tokens — должен быть быстрым.
        """
        user_msg = QUERY_EXPANSION_USER_TEMPLATE.format(question=question_text[:500])
        try:
            if provider == "gemini":
                response = self._get_gemini_client().models.generate_content(
                    model=model,
                    contents=user_msg,
                    config=GenerateContentConfig(
                        system_instruction=QUERY_EXPANSION_SYSTEM,
                        temperature=0.0,
                        max_output_tokens=120,
                    ),
                )
                return (response.text or "").strip()
            elif provider == "ollama":
                r = self._get_ollama_client().post(
                    "/chat/completions",
                    json={
                        "model": model,
                        "messages": [
                            {"role": "system", "content": QUERY_EXPANSION_SYSTEM},
                            {"role": "user", "content": user_msg},
                        ],
                        "temperature": 0.0,
                        "max_tokens": 120,
                    },
                )
                r.raise_for_status()
                return r.json()["choices"][0]["message"]["content"].strip()
            elif provider == "ario":
                return self._ario_call(
                    messages=[
                        {"role": "system", "content": QUERY_EXPANSION_SYSTEM},
                        {"role": "user", "content": user_msg},
                    ],
                    model=model,
                    max_tokens=120,
                )
            else:
                r = self._get_groq_client().chat.completions.create(
                    model=model,
                    messages=[
                        {"role": "system", "content": QUERY_EXPANSION_SYSTEM},
                        {"role": "user", "content": user_msg},
                    ],
                    temperature=0.0,
                    max_tokens=120,
                )
                return r.choices[0].message.content.strip()
        except Exception as e:
            print(f"  [QE] expansion failed: {e}")
            return ""

    def _retrieve_for_segment(
        self,
        segment_text: str,
        expanded: str = "",
        allowed_l2: list[str] | None = None,
        allowed_l3: list[str] | None = None,
        multi_queries: list[str] | None = None,
    ) -> list[dict]:
        """Per-question retrieval: dense + lexical + heuristic rerank → (CE rerank?) → top-K.

        Если allowed_l2 непустой — кандидаты фильтруются по этим L2-тематикам (coarse-to-fine).
        Если expanded query задан — embedding ищется по объединённому тексту.
        Если multi_queries задан — для каждого варианта делается отдельный dense+lexical retrieval,
            пулы объединяются (max similarity per code).
        """
        query = segment_text if not expanded else f"{segment_text}. {expanded}"

        # Расширяем пулы если фильтрация активна, чтобы было что отбирать
        dense_top_k = max(RETRIEVAL_POOL_SIZE * 2, 100) if allowed_l2 else max(RETRIEVAL_POOL_SIZE, TOP_K_CANDIDATES)
        lex_top_k = max(LEXICAL_POOL_SIZE * 2, 60) if allowed_l2 else LEXICAL_POOL_SIZE

        # 1) Retrieval по основному query
        dense_pool = self._search_candidates(query, top_k=dense_top_k)
        lexical_pool = self._search_lexical_candidates(query, top_k=lex_top_k)
        merged = self._merge_candidate_pools(dense_pool, lexical_pool)

        # 2) Multi-query union (опц.): для каждого variant — отдельный retrieval
        if multi_queries:
            # Меньший размер на variant — суммарно пул не должен взорваться
            mq_dense_k = max(RETRIEVAL_POOL_SIZE // 2, 30)
            mq_lex_k = max(LEXICAL_POOL_SIZE // 2, 15)
            all_pools = [merged]
            for variant in multi_queries:
                v_dense = self._search_candidates(variant, top_k=mq_dense_k)
                v_lex = self._search_lexical_candidates(variant, top_k=mq_lex_k)
                all_pools.append(v_dense)
                all_pools.append(v_lex)
            before_mqe = len(merged)
            merged = self._merge_candidate_pools(*all_pools)
            print(f"  [MQE] union {len(multi_queries)+1} запросов: {before_mqe} -> {len(merged)} кандидатов")

        # 1) Coarse-to-fine routing FIRST — сужаем пул до релевантных L2/L3 тем
        #    Затем whitelist применяется КО ВСЕМУ already-routed пулу
        if allowed_l2:
            merged = filter_candidates_by_l2(merged, allowed_l2)
            print(f"  [Router] фильтр L2 {allowed_l2}: {len(merged)} кандидатов")

        if allowed_l3:
            l3_filtered = filter_candidates_by_l3(merged, allowed_l3)
            if len(l3_filtered) >= TOP_K_CANDIDATES:
                merged = l3_filtered
                print(f"  [L3-Router] фильтр L3 {allowed_l3}: {len(merged)} кандидатов")
            else:
                print(f"  [L3-Router] L3-фильтр дал бы {len(l3_filtered)}<{TOP_K_CANDIDATES}, оставляем L2-пул")

        # Заполняем до TOP_K_CANDIDATES кодами из той же L2-ветки (если routing сузил)
        if len(merged) < TOP_K_CANDIDATES:
            allowed = set(allowed_l2) if allowed_l2 else set()
            seen = {c["code"] for c in merged}
            direct_l2 = []
            for meta in self.metadata:
                code = meta["code"]
                parts = code.split(".")
                l2_prefix = ".".join(parts[:2]) if len(parts) >= 2 else code
                if l2_prefix in allowed and code not in seen:
                    direct_l2.append(self._candidate_from_metadata(meta, 0.5, source="l2-direct"))
            if direct_l2:
                merged = merged + direct_l2
                print(f"  [Router] добавлено {len(direct_l2)} кодов из L2 напрямую → {len(merged)}")

        # 2) Whitelist (опц.): оставить только «горячие» коды + их L4-родителей в случае L5-кодов
        #    Применяется ПОСЛЕ routing — так whitelist фильтрует уже narrow-пул
        if self.allowed_codes:
            before_wl = len(merged)
            filtered = []
            for c in merged:
                code = c["code"]
                parts = code.split(".")
                l4_prefix = ".".join(parts[:4]) if len(parts) >= 4 else code
                if code in self.allowed_codes or l4_prefix in self.allowed_codes:
                    filtered.append(c)
            if len(filtered) >= TOP_K_CANDIDATES:
                merged = filtered
                print(f"  [Whitelist] {before_wl} -> {len(merged)} кодов из топ-{len(self.allowed_codes)}")
            else:
                # Если whitelist срезал слишком много — добавляем allowed-коды напрямую из метаданных
                seen_codes = {c["code"] for c in filtered}
                direct_codes = []
                for meta in self.metadata:
                    code = meta["code"]
                    parts = code.split(".")
                    l4_prefix = ".".join(parts[:4]) if len(parts) >= 4 else code
                    if (code in self.allowed_codes or l4_prefix in self.allowed_codes) and code not in seen_codes:
                        direct_codes.append(self._candidate_from_metadata(meta, 0.5, source="whitelist-direct"))
                merged = filtered + direct_codes
                print(f"  [Whitelist] dense={before_wl}, фильтрован={len(filtered)}, добавлено direct-fetch={len(direct_codes)}, итого={len(merged)}")

        if self.ce_reranker is not None:
            heuristic_top = self._rerank_candidates(query, merged, top_k=30)
            final = self.ce_reranker.rerank(query, heuristic_top, top_k=TOP_K_CANDIDATES)
        elif not ENABLE_HEURISTIC_RERANKER:
            # Чистый dense-ранкинг: merged используется только как фильтр
            # (routing/whitelist могли сузить пул), порядок — по dense similarity
            merged_codes = {c["code"] for c in merged}
            final = [c for c in dense_pool if c["code"] in merged_codes][:TOP_K_CANDIDATES]
        else:
            final = self._rerank_candidates(query, merged, top_k=TOP_K_CANDIDATES)

        # Anti-cascade-error: если top-3 все из одного L1, инжектируем лучшего из другого L1.
        # Защита от ситуации, когда retrieval "залипает" на неправильной L1-теме и LLM
        # не имеет шанса выбрать что-то из верной области.
        return self._ensure_l1_diversity(final, dense_pool=dense_pool)

    def _ensure_l1_diversity(
        self,
        candidates: list,
        *,
        dense_pool: list,
    ) -> list:
        """Anti-cascade-error: if the candidate pool is dominated by a single L1
        (first 4-digit segment of the code), inject the best-scoring candidate from
        a DIFFERENT L1 — so the LLM is guaranteed to see at least one alternative
        from another top-level section.

        Triggers when ALL three top-3 share L1, OR ≥4 of top-5 share L1, OR ≥7 of
        top-10 share L1. We pick the diverse candidate from the broader dense pool
        and insert it at position 4 in the final list (visible to LLM in top-K).

        This addresses the failure mode where retrieval locks onto a wrong L1 theme
        and the LLM has no escape — every "alternative" the LLM could pick is also
        wrong because they're all siblings of the same wrong category.

        Args:
            candidates: final reranked pool (in score order).
            dense_pool: broader dense retrieval results to draw alternative L1 from.

        Returns:
            Same list (potentially with one cross-L1 candidate injected at position 4),
            trimmed to TOP_K_CANDIDATES so the LLM pool doesn't grow.
        """
        if len(candidates) < 3:
            return candidates

        l1_of = lambda c: c["code"].split(".")[0] if c.get("code") else ""

        # Compute dominance at multiple top-N windows
        from collections import Counter
        windows = [
            ("top-3", 3, 3),    # all 3 must share
            ("top-5", 5, 4),    # at least 4 of 5
            ("top-10", 10, 7),  # at least 7 of 10
        ]
        triggered_window = None
        dominant_l1 = None
        for label, n, threshold in windows:
            if len(candidates) < n:
                continue
            counts = Counter(l1_of(c) for c in candidates[:n])
            top_l1, top_count = counts.most_common(1)[0]
            if top_count >= threshold:
                triggered_window = label
                dominant_l1 = top_l1
                break

        # Diagnostic: always log L1 distribution of top-10
        diag_counts = Counter(l1_of(c) for c in candidates[:10])
        diag_str = ", ".join(f"L1={k}:{v}" for k, v in diag_counts.most_common())
        print(f"  [L1-diversity] top-10 distribution: {diag_str}")

        if triggered_window is None:
            return candidates  # pool is sufficiently diverse

        existing_codes = {c["code"] for c in candidates}
        # Find best-scoring candidate from broader dense pool with different L1
        diverse = next(
            (c for c in dense_pool if l1_of(c) != dominant_l1 and c["code"] not in existing_codes),
            None,
        )
        if diverse is None:
            print(f"  [L1-diversity] {triggered_window} dominated by L1={dominant_l1}, no cross-L1 candidate in dense_pool")
            return candidates

        diverse = dict(diverse)  # copy to avoid mutating dense_pool
        diverse["source"] = (diverse.get("source") or "dense") + "+l1-diversity"
        diverse_l1 = l1_of(diverse)
        print(
            f"  [L1-diversity] {triggered_window} dominated by L1={dominant_l1}; "
            f"инжектирован {diverse['code']} (L1={diverse_l1}, sim={diverse.get('similarity', 0):.3f}) в позицию 4"
        )

        # Insert at position 4 (0-indexed = 3) so top-3 ordering stays intact
        out = candidates[:3] + [diverse] + candidates[3:]
        return out[:TOP_K_CANDIDATES]

    def _classify_with_llm(
        self,
        appeal_text: str,
        questions_with_candidates: list[dict],
        llm_provider: str | None = None,
        llm_model: str | None = None,
    ) -> dict:
        """LLM classification across multiple pre-segmented questions.

        `questions_with_candidates` — список {ordinal, question_text, candidates[]} —
        каждый вопрос имеет свой пул кандидатов из per-question retrieval.
        """
        provider, model = self._resolve_llm(llm_provider, llm_model)

        # Сжимаем кандидатов до минимума (code, name, full_path, level) — экономия токенов.
        # Добавляем l3_theme (родительская L3-тема) и помечаем sibling-группы (одинаковый L3).
        def l3_prefix(code: str) -> str:
            parts = code.split(".")
            return ".".join(parts[:3]) if len(parts) >= 3 else code

        def l3_name(code: str) -> str:
            """L3-имя темы — родительская категория. ищется через code_index."""
            l3 = l3_prefix(code) + ".0000"
            entry = self.code_index.get(l3)
            return entry["name"] if entry else ""

        # Dynamic per-question candidate cap. Qwen3.6-35B-A3B has 128K context window.
        # Each candidate ~180-220 tokens; appeal_text ~1500; JSON structure ~1000; LLM output ~3000.
        # Budget for candidates: ~120K tokens = ~550 cands total. Safety: cap at 400.
        # (Previous Qwen3-32B had 32K → cap was 100. Quadrupled headroom on new model.)
        n_q = max(len(questions_with_candidates), 1)
        per_q_cap = max(8, min(TOP_K_CANDIDATES, 400 // n_q))
        if per_q_cap < TOP_K_CANDIDATES:
            print(
                f"  [Context] {n_q} вопросов × TOP_K={TOP_K_CANDIDATES} > headroom; "
                f"урезаю до {per_q_cap} кандидатов на вопрос."
            )

        compact_questions = []
        total_candidates = 0
        for q in questions_with_candidates:
            # Группируем по L3-теме (3 сегмента префикса)
            l3_counts: dict[str, int] = {}
            for c in q["candidates"]:
                l3_counts[l3_prefix(c["code"])] = l3_counts.get(l3_prefix(c["code"]), 0) + 1

            # Build l3_options: unique L3 themes in this question's candidate pool
            l3_options = []
            for l3_code, l3_code_count in l3_counts.items():
                l3_with_zeros = l3_code + ".0000"
                entry = self.code_index.get(l3_with_zeros)
                l3_name_str = entry["name"] if entry and entry.get("name") else l3_name(q["candidates"][0]["code"])
                l3_options.append({
                    "l3_code": l3_code,
                    "l3_name": l3_name_str,
                    "candidates_in_l3": l3_code_count,
                })

            compact_candidates = []
            for c in q["candidates"][:per_q_cap]:
                code = c["code"]
                l3 = l3_prefix(code)
                entry = {
                    "code": code,
                    "name": c["name"],
                    "level": c.get("level", 0),
                    "full_path": c.get("full_path", ""),
                    "l3_theme": l3_name(code),
                }
                if l3_counts.get(l3, 0) > 1:
                    entry["siblings_in_l3"] = l3_counts[l3]
                compact_candidates.append(entry)

            total_candidates += len(compact_candidates)
            compact_questions.append({
                "ordinal": q["ordinal"],
                "question_text": q["question_text"],
                "l3_options": l3_options,
                "candidates": compact_candidates,
            })

        questions_json = json.dumps(compact_questions, ensure_ascii=False, indent=2)
        user_message = CLASSIFICATION_PROMPT_TEMPLATE_L3GROUP.format(
            appeal_text=appeal_text[:4000],
            questions_json=questions_json,
        )

        max_retries = 5
        last_error = None

        for attempt in range(1, max_retries + 1):
            try:
                start_time = time.time()

                if provider == "gemini":
                    response = self._get_gemini_client().models.generate_content(
                        model=model,
                        contents=user_message,
                        config=GenerateContentConfig(
                            system_instruction=SYSTEM_PROMPT,
                            temperature=0.1,
                        ),
                    )
                    raw = response.text.strip()
                elif provider == "ollama":
                    response = self._get_ollama_client().post(
                        "/chat/completions",
                        json={
                            "model": model,
                            "messages": [
                                {"role": "system", "content": SYSTEM_PROMPT},
                                {"role": "user",   "content": user_message},
                            ],
                            "temperature": 0.1,
                        },
                    )
                    response.raise_for_status()
                    raw = response.json()["choices"][0]["message"]["content"].strip()
                elif provider == "ario":
                    # Qwen3.6 generates verbose reasoning per question (~800-1500 tokens
                    # incl. l3_options array and explanations). With 128K context window
                    # we have plenty of headroom; be generous to avoid mid-JSON truncation.
                    n_q = max(len(compact_questions), 1)
                    explicit_max_tokens = min(16000, n_q * 2500 + 2000)
                    raw = self._ario_call(
                        messages=[
                            {"role": "system", "content": SYSTEM_PROMPT},
                            {"role": "user",   "content": user_message},
                        ],
                        model=model,
                        max_tokens=explicit_max_tokens,
                        temperature=0.1,
                        timeout=max(120, n_q * 40),
                        schema=None,
                    )
                else:
                    response = self._get_groq_client().chat.completions.create(
                        model=model,
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
                    candidates_count=total_candidates,
                )
                return result

            except RateLimitError as e:
                last_error = f"Rate limit: {e}"
                wait_time = min(2 ** attempt, 60)  # не более 60 секунд
                print(f"  [{provider}] Rate limit, попытка {attempt}/{max_retries}. Ждём {wait_time}с...")
                time.sleep(wait_time)

            except APIError as e:
                last_error = f"API error: {e}"
                if attempt < max_retries:
                    wait_time = 2 ** attempt
                    print(f"  [{provider}] API error, попытка {attempt}/{max_retries}. Ждём {wait_time}с...")
                    time.sleep(wait_time)

            except json.JSONDecodeError as e:
                last_error = f"JSON parse error: {e}"
                tail = raw[-200:] if 'raw' in dir() and isinstance(raw, str) else "<empty>"
                print(
                    f"  [LLM] JSON parse error, попытка {attempt}/{max_retries}: {e}. "
                    f"Last 200 chars of response: {tail!r}"
                )
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
            candidates_count=total_candidates,
        )
        raise RuntimeError(f"LLM ({provider}/{model}) недоступен после {max_retries} попыток: {last_error}")

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

    def _full_leaf_listing(self) -> str:
        """Полный перечень листьев классификатора (L4+L5) как текст 'код — название'.
        Кэшируется на инстансе (строится один раз). Путь (full_path) НЕ включаем:
        он удваивает объём (~123k токенов → перелёт за 131k контекст модели);
        код+название ≈ 61k токенов — с запасом под вывод."""
        cached = getattr(self, "_full_leaf_cache", None)
        if cached is not None:
            return cached
        lines = [
            f"{m['code']} — {m['name']}"
            for m in self.metadata
            if m.get("level") in (4, 5)
        ]
        self._full_leaf_cache = "\n".join(lines)
        return self._full_leaf_cache

    @staticmethod
    def _max_candidate_similarity(questions_with_candidates: list[dict]) -> float:
        """Максимальная DENSE cosine-similarity среди кандидатов всех вопросов.
        Учитываются только dense-кандидаты (source == 'dense'): лексические имеют
        синтетическую similarity ~0.76+ и исказили бы сигнал промаха.
        Низкое значение ⇒ векторный поиск не нашёл ничего близкого (промах)."""
        best = 0.0
        for q in questions_with_candidates:
            for c in q.get("candidates", []):
                if c.get("source", "dense") != "dense":
                    continue
                best = max(best, float(c.get("similarity", 0.0)))
        return best

    def _classify_with_full_classifier(
        self,
        appeal_text: str,
        llm_provider: str | None = None,
        llm_model: str | None = None,
    ) -> dict:
        """Фоллбэк-классификация: показываем LLM весь классификатор (листья) и
        просим выбрать коды напрямую. Возвращает dict в том же формате, что
        _classify_with_llm (questions[] + vid/tip/is_ustnoe + fio/summary)."""
        provider, model = self._resolve_llm(llm_provider, llm_model)
        user_message = FULL_CLASSIFIER_FALLBACK_TEMPLATE.format(
            appeal_text=appeal_text[:4000],
            full_classifier=self._full_leaf_listing(),
        )
        if provider == "ario":
            raw = self._ario_call(
                messages=[
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": user_message},
                ],
                model=model,
                max_tokens=8000,
                temperature=0.1,
                timeout=180,
                schema=None,
            )
        elif provider == "gemini":
            response = self._get_gemini_client().models.generate_content(
                model=model,
                contents=user_message,
                config=GenerateContentConfig(system_instruction=SYSTEM_PROMPT, temperature=0.1),
            )
            raw = response.text.strip()
        elif provider == "ollama":
            response = self._get_ollama_client().post(
                "/chat/completions",
                json={
                    "model": model,
                    "messages": [
                        {"role": "system", "content": SYSTEM_PROMPT},
                        {"role": "user", "content": user_message},
                    ],
                    "temperature": 0.1,
                },
            )
            response.raise_for_status()
            raw = response.json()["choices"][0]["message"]["content"].strip()
        else:
            response = self._get_groq_client().chat.completions.create(
                model=model,
                messages=[
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": user_message},
                ],
                temperature=0.1,
                response_format={"type": "json_object"},
            )
            raw = response.choices[0].message.content.strip()
        return json.loads(raw)

    def classify(
        self,
        appeal_text: str,
        llm_provider: str | None = None,
        llm_model: str | None = None,
    ) -> ClassificationResult:
        """Основной метод классификации обращения с per-question retrieval."""
        provider, model = self._resolve_llm(llm_provider, llm_model)

        # Шаг 1: Сегментация обращения на отдельные вопросы
        segments = split_appeal_questions(appeal_text)
        if not segments:
            segments = [AppealQuestion(text=appeal_text.strip(), ordinal=1, evidence="single")]
        print(f"  [Сегментация] Выделено {len(segments)} вопрос(ов)")

        # Шаг 1b: Опциональное LLM query expansion — переформулировать сегменты в терминах классификатора
        expansions: dict[int, str] = {}
        if ENABLE_QUERY_EXPANSION:
            qe_start = time.time()
            for seg in segments:
                expansions[seg.ordinal] = self._expand_query(seg.text, provider, model)
            print(
                f"  [QE] Query expansion для {len(segments)} сегментов за {time.time() - qe_start:.2f}с"
            )

        # Шаг 1b': Опциональное Multi-Query expansion — 1 LLM-вызов даёт N формулировок,
        # для каждой делается отдельный retrieval, потом union пулов.
        multi_queries_by_ord: dict[int, list[str]] = {}
        if ENABLE_MULTI_QUERY_EXPAND:
            mqe_start = time.time()
            for seg in segments:
                variants = self._multi_query_expand(seg.text, provider, model)
                if variants:
                    multi_queries_by_ord[seg.ordinal] = variants
                    print(f"  [MQE] seg {seg.ordinal}: {len(variants)} вариантов: {variants[:2]}")
            print(f"  [MQE] expansion для {len(segments)} сегментов за {time.time() - mqe_start:.2f}с")

        # Шаг 1c: Опциональное section routing — coarse-to-fine выбор тематик из методички
        allowed_l2: list[str] = []
        allowed_l3: list[str] = []
        if ENABLE_SECTION_ROUTING:
            route_start = time.time()
            allowed_l2 = self._route_to_sections(appeal_text, provider, model)
            print(f"  [Router] выбор L2 за {time.time() - route_start:.2f}с")

            # Шаг 1d: L3-router — сужение по конкретной L3-теме внутри выбранных L2
            if ENABLE_L3_ROUTING and allowed_l2:
                l3_start = time.time()
                allowed_l3 = self._route_to_l3_themes(appeal_text, allowed_l2, provider, model)
                print(f"  [L3-Router] выбор L3 за {time.time() - l3_start:.2f}с")

        # Шаг 2: Per-question retrieval — для каждого вопроса свой пул кандидатов
        search_start = time.time()
        questions_with_candidates: list[dict] = []
        valid_code_sets: dict[int, set[str]] = {}
        for seg in segments:
            seg_candidates = self._retrieve_for_segment(
                seg.text,
                expanded=expansions.get(seg.ordinal, ""),
                allowed_l2=allowed_l2 or None,
                allowed_l3=allowed_l3 or None,
                multi_queries=multi_queries_by_ord.get(seg.ordinal) or None,
            )
            questions_with_candidates.append({
                "ordinal": seg.ordinal,
                "question_text": seg.text,
                "candidates": seg_candidates,
            })
            valid_code_sets[seg.ordinal] = {c["code"] for c in seg_candidates}
        search_elapsed = time.time() - search_start

        total_candidates = sum(len(q["candidates"]) for q in questions_with_candidates)
        self._log_request(
            success=True,
            attempt=1,
            elapsed=search_elapsed,
            confidence=None,
            error=None,
            candidates_count=total_candidates,
        )
        print(
            f"  [Поиск] Per-question retrieval: {total_candidates} кандидатов "
            f"для {len(segments)} вопросов за {search_elapsed:.2f}с"
        )

        # Шаг 3: Классификация через LLM (один вызов, кандидаты разнесены по вопросам)
        llm_result = self._classify_with_llm(appeal_text, questions_with_candidates, provider, model)

        # Шаг 3b: Full-classifier fallback — если ретривер промахнулся (в пуле нет
        # близких по смыслу кодов), переклассифицируем, показав LLM весь классификатор.
        full_fallback_used = False
        if ENABLE_FULL_CLASSIFIER_FALLBACK:
            max_sim = self._max_candidate_similarity(questions_with_candidates)
            llm_flagged = bool(llm_result.get("candidates_insufficient"))
            low_sim = max_sim < FULL_FALLBACK_SIM_THRESHOLD
            if llm_flagged or low_sim:
                trigger = "LLM: нет подходящей ветки" if llm_flagged else f"max sim {max_sim:.3f} < {FULL_FALLBACK_SIM_THRESHOLD}"
                print(
                    f"  [Fallback] промах ретривера ({trigger}) "
                    f"→ повторная классификация по ПОЛНОМУ классификатору"
                )
                try:
                    fb = self._classify_with_full_classifier(appeal_text, provider, model)
                    if fb and fb.get("questions"):
                        llm_result = fb
                        full_fallback_used = True
                        # При фоллбэке допустимы ЛЮБЫЕ коды классификатора — снимаем
                        # per-question ограничение, чтобы strict-валидация не подменяла.
                        all_codes = set(self.code_index.keys())
                        valid_code_sets = {
                            int(q.get("ordinal") or i + 1): all_codes
                            for i, q in enumerate(fb["questions"])
                        }
                except Exception as e:
                    print(f"  [Fallback] ошибка полного прохода: {type(e).__name__}: {e}")

        # Шаг 4: Обогащение результатов + строгая валидация выбранных кодов
        classified_questions = []
        duplicate_code_counts: dict[str, int] = {}
        raw_selected_codes = [
            str(q.get("selected_code", ""))
            for q in llm_result.get("questions", [])
            if q.get("selected_code")
        ]
        if len(raw_selected_codes) > 1:
            for raw_code in raw_selected_codes:
                duplicate_code_counts[raw_code] = duplicate_code_counts.get(raw_code, 0) + 1

        for q in llm_result.get("questions", []):
            ordinal = int(q.get("ordinal") or len(classified_questions) + 1)
            code = q.get("selected_code", "")
            allowed_codes = valid_code_sets.get(ordinal, set())
            question_candidates = next(
                (qc["candidates"] for qc in questions_with_candidates if qc["ordinal"] == ordinal),
                [],
            )
            original_code = code

            # Strict validation: если LLM выбрал код, которого нет в кандидатах ЭТОГО вопроса —
            # подменяем на топ-1 кандидата и помечаем низкой confidence
            invalid_code = False
            if allowed_codes and code not in allowed_codes:
                invalid_code = True
                # Найти топ-1 кандидата этого вопроса
                fallback_candidate = next(
                    (qc for qc in questions_with_candidates if qc["ordinal"] == ordinal),
                    None,
                )
                if fallback_candidate and fallback_candidate["candidates"]:
                    code = fallback_candidate["candidates"][0]["code"]

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

            # Детерминированная страховка: если выбран уточняющий L5-код, его
            # L4-родитель обязан быть в альтернативах (LLM это правило выполняет
            # нестабильно, а аннотаторы часто используют родительский L4).
            code_parts = code.split(".") if code else []
            if len(code_parts) >= 5:
                parent4 = ".".join(code_parts[:4])
                parent_entry = self.code_index.get(parent4)
                if parent_entry and all(a["code"] != parent4 for a in alt_entries):
                    alt_entries.insert(0, {
                        "code":      parent4,
                        "name":      parent_entry["name"],
                        "full_path": parent_entry["full_path"],
                    })
                    alt_entries = alt_entries[:2]

            # Anti-cascade-error post-process: if selected_code + all alternatives share
            # the same L1 (first 4-digit segment), inject the best cross-L1 candidate
            # from this question's retrieval pool as an extra alternative. This protects
            # against LLM locking onto a wrong top-level section when other plausible
            # candidates from different sections were already in the candidate pool.
            picks_l1 = {code.split(".")[0]} | {a["code"].split(".")[0] for a in alt_entries}
            if len(picks_l1) == 1 and code:
                dominant_l1 = code.split(".")[0]
                question_pool = next(
                    (qc["candidates"] for qc in questions_with_candidates if qc["ordinal"] == ordinal),
                    [],
                )
                picked_codes = {code} | {a["code"] for a in alt_entries}
                cross_l1 = next(
                    (c for c in question_pool
                     if c["code"].split(".")[0] != dominant_l1 and c["code"] not in picked_codes),
                    None,
                )
                if cross_l1:
                    cross_entry = self.code_index.get(cross_l1["code"])
                    if cross_entry:
                        alt_entries.append({
                            "code":      cross_l1["code"],
                            "name":      cross_entry["name"],
                            "full_path": cross_entry["full_path"],
                        })
                        print(
                            f"  [L1-diversity-post] вопрос {ordinal}: top-3 все из L1={dominant_l1}; "
                            f"добавлен cross-L1 {cross_l1['code']} (L1={cross_l1['code'].split('.')[0]}) "
                            f"как 3-я альтернатива"
                        )

            confidence_val = float(q.get("confidence", 0.0))
            duplicate_code = duplicate_code_counts.get(original_code, 0) > 1
            confidence_val, verification_reasons = self._calibrate_question_confidence(
                llm_confidence=confidence_val,
                selected_code=code,
                candidates=question_candidates,
                invalid_code=invalid_code,
                duplicate_code=duplicate_code,
            )
            reasoning = (q.get("reasoning") or "").strip()
            # Служебные маркеры в отдельное поле — НЕ в reasoning для оператора
            tech_reasons = list(verification_reasons)
            if invalid_code:
                tech_reasons.append("llm_code_replaced_with_top1")

            classified_questions.append(ClassifiedQuestion(
                question_text=q.get("question_text", ""),
                code=code,
                name=entry["name"] if entry else code,
                level=entry["level"] if entry else 0,
                full_path=entry["full_path"] if entry else "",
                predmet_vedeniya=q.get("predmet_vedeniya", ""),
                confidence=confidence_val,
                reasoning=reasoning,
                alternatives=alt_entries,
                verification_reasons=tech_reasons,
            ))

        # Защита: если LLM всем вопросам присвоил один и тот же код, а вопросов >1 → нужен оператор
        if len(classified_questions) > 1:
            unique_codes = {q.code for q in classified_questions}
            if len(unique_codes) == 1:
                for q in classified_questions:
                    q.confidence = min(q.confidence, 0.5)
                    if "all_questions_same_code" not in q.verification_reasons:
                        q.verification_reasons.append("all_questions_same_code")

        # Схлопываем карточки-дубли: один код — один вопрос для оператора
        before_dedupe = len(classified_questions)
        classified_questions = dedupe_questions(classified_questions)
        if len(classified_questions) < before_dedupe:
            print(f"  [Дедуп] карточек: {before_dedupe} -> {len(classified_questions)}")

        # Шаг 3c: Детекция повторного обращения — доп. вопрос «Результаты рассмотрения».
        if ENABLE_REPEAT_DETECTION:
            is_repeat = detect_repeat_markers(appeal_text) or bool(llm_result.get("is_repeat_appeal"))
            already = any(q.code == REPEAT_APPEAL_CODE for q in classified_questions)
            if is_repeat and not already:
                entry = self.code_index.get(REPEAT_APPEAL_CODE)
                if entry:
                    by_marker = detect_repeat_markers(appeal_text)
                    classified_questions.append(ClassifiedQuestion(
                        question_text="Признак повторного обращения",
                        code=REPEAT_APPEAL_CODE,
                        name=entry["name"],
                        level=entry["level"],
                        full_path=entry["full_path"],
                        predmet_vedeniya="",
                        confidence=0.9,
                        reasoning=("Обнаружены признаки повторного обращения "
                                   f"({'явные маркеры в тексте' if by_marker else 'по оценке модели'}): "
                                   "гражданин обращается не впервые / не получил ответа. "
                                   "Присвоен код «Результаты рассмотрения обращения»."),
                        alternatives=[],
                        verification_reasons=["repeat_appeal_detected"],
                    ))
                    print(f"  [Повтор] обнаружено повторное обращение → +{REPEAT_APPEAL_CODE}")

        # Шаг 3d: Просьба прекратить рассмотрение / отозвать обращение.
        # Такой код ставится ПЕРВЫМ: если гражданин просит отозвать обращение,
        # процедурное решение важнее тематики (тематика остаётся доп. вопросом).
        if ENABLE_WITHDRAWAL_DETECTION:
            by_marker = detect_withdrawal_markers(appeal_text)
            is_withdrawal = by_marker or bool(llm_result.get("is_withdrawal_request"))
            already = any(q.code == WITHDRAWAL_APPEAL_CODE for q in classified_questions)
            if is_withdrawal and not already:
                entry = self.code_index.get(WITHDRAWAL_APPEAL_CODE)
                if entry:
                    classified_questions.insert(0, ClassifiedQuestion(
                        question_text="Просьба прекратить рассмотрение обращения",
                        code=WITHDRAWAL_APPEAL_CODE,
                        name=entry["name"],
                        level=entry["level"],
                        full_path=entry["full_path"],
                        predmet_vedeniya="",
                        confidence=0.9,
                        reasoning=("Обнаружена просьба прекратить рассмотрение "
                                   f"({'явные маркеры в тексте' if by_marker else 'по оценке модели'}): "
                                   "гражданин просит отозвать обращение / снять его с рассмотрения. "
                                   "Присвоен код «Прекращение рассмотрения обращения»."),
                        alternatives=[],
                        verification_reasons=["withdrawal_request_detected"],
                    ))
                    print(f"  [Отзыв] просьба прекратить рассмотрение → +{WITHDRAWAL_APPEAL_CODE}")

        # Шаг 4: Итоговая уверенность
        confidences = [q.confidence for q in classified_questions]
        overall_confidence = sum(confidences) / len(confidences) if confidences else 0.0
        needs_verification = overall_confidence < MIN_CONFIDENCE

        applicant_fio, summary = extract_extra_fields(llm_result)

        result = ClassificationResult(
            vid_obrascheniya=llm_result.get("vid_obrascheniya", ""),
            tip_obrascheniya=llm_result.get("tip_obrascheniya", ""),
            is_ustное=llm_result.get("is_ustnoe", False),
            questions=classified_questions,
            overall_confidence=round(overall_confidence, 3),
            needs_verification=needs_verification,
            raw_appeal=appeal_text,
            llm_provider=provider,
            llm_model=model,
            applicant_fio=applicant_fio,
            summary=summary,
            full_fallback_used=full_fallback_used,
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
            # Логируем union всех кандидатов по всем вопросам (для дообучения)
            seen_codes: set[str] = set()
            log_candidates: list[dict] = []
            for qc in questions_with_candidates:
                for c in qc["candidates"]:
                    if c["code"] not in seen_codes:
                        seen_codes.add(c["code"])
                        log_candidates.append({
                            "code": c["code"],
                            "name": c["name"],
                            "similarity": c.get("similarity", 0.0),
                        })
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
