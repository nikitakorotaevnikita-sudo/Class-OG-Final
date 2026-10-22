"""Обогащение запроса перед поиском кандидатов.

LLM пересказывает вопрос гражданина языком классификатора: краткая суть,
ключевые фразы, синонимы. Поиск идёт по этому тексту вместе с вопросом
(`append`) или вместо него (`replace`); модели на классификацию уходит
исходный вопрос. Режим задаёт `QUERY_ENRICHMENT` (`off` по умолчанию).

Развитие `ENABLE_QUERY_EXPANSION`: тот давал одну строку терминов.
"""

import json
import re

MODES = ("off", "append", "replace")

ENRICH_SYSTEM = """Ты — эксперт по Общероссийскому классификатору вопросов обращений граждан.
Тебе дают вопрос из обращения гражданина. Перескажи его так, чтобы по твоему
ответу нашлась нужная рубрика классификатора: рубрики написаны канцелярским
языком органов власти, а граждане пишут бытовым.

Ответь ТОЛЬКО JSON без пояснений:
{"summary": "суть вопроса одной-двумя фразами: что случилось и чего просит гражданин",
 "key_phrases": ["5-8 ключевых фраз: сфера, объект, действие"],
 "synonyms": ["3-6 официальных формулировок и синонимов, как в классификаторе"]}

Не выдумывай обстоятельств, которых нет в вопросе. Не включай ФИО, адреса,
телефоны и другие персональные данные."""

ENRICH_USER_TEMPLATE = """Вопрос: «{question}»"""


def normalize_mode(value: str | None) -> str:
    mode = (value or "").strip().lower()
    return mode if mode in MODES else "off"


def _strings(value) -> list[str]:
    if not isinstance(value, list):
        return []
    return [s.strip() for s in value if isinstance(s, str) and s.strip()]


def parse_enrichment(raw: str | None) -> dict | None:
    """Ответ модели → {summary, key_phrases, synonyms}; None, если пусто или не JSON."""
    text = (raw or "").strip()
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text).strip()
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if not match:
        return None
    try:
        data = json.loads(match.group(0))
    except json.JSONDecodeError:
        return None
    if not isinstance(data, dict):
        return None
    summary = data.get("summary")
    enr = {
        "summary": summary.strip() if isinstance(summary, str) else "",
        "key_phrases": _strings(data.get("key_phrases")),
        "synonyms": _strings(data.get("synonyms")),
    }
    if not (enr["summary"] or enr["key_phrases"] or enr["synonyms"]):
        return None
    return enr


def enrichment_text(enr: dict | None) -> str:
    """Одна строка для поиска: суть. ключевые фразы. синонимы."""
    if not enr:
        return ""
    parts = [enr.get("summary", "").rstrip(". ")]
    parts += [", ".join(enr.get(k) or []) for k in ("key_phrases", "synonyms")]
    return ". ".join(p for p in parts if p) + "."


def retrieval_inputs(segment_text: str, enr: dict | None, mode: str) -> tuple[str, str]:
    """(текст запроса, дополнение) для `_retrieve_for_segment` в данном режиме."""
    extra = enrichment_text(enr)
    if not extra or mode == "off":
        return segment_text, ""
    if mode == "replace":
        return extra, ""
    return segment_text, extra
