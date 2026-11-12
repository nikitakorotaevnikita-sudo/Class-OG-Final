"""Общая логика AppealStore для memory и PostgreSQL бэкендов."""

from __future__ import annotations

import ipaddress
import logging
import uuid
from collections import Counter
from datetime import datetime, timedelta, timezone
from typing import Iterable, Optional

logger = logging.getLogger(__name__)

STATUSES = ("pending", "confirmed", "corrected", "rejected")


def histogram_template() -> dict:
    return {f"{i / 10:.1f}-{(i + 1) / 10:.1f}": 0 for i in range(10)}


def aware(dt: Optional[datetime]) -> Optional[datetime]:
    """naive -> локальное время сервера (как в старом логе); aware не трогаем."""
    if dt is None:
        return None
    return dt.astimezone() if dt.tzinfo is None else dt


def local_iso(dt: Optional[datetime]) -> Optional[str]:
    """ISO с секундами, naive локальное время (формат старого appeals_log.jsonl)."""
    if dt is None:
        return None
    return aware(dt).astimezone().replace(tzinfo=None).isoformat(timespec="seconds")


def now_aware() -> datetime:
    return datetime.now(timezone.utc).astimezone()


def norm_uuid(value: Optional[str]) -> Optional[str]:
    """Каноничная строка UUID или None, если значение не UUID."""
    try:
        return str(uuid.UUID(str(value)))
    except (ValueError, AttributeError, TypeError):
        return None


def norm_ip(value: Optional[str]) -> Optional[str]:
    """Валидный IP (без маски) или None (например, 'unknown').

    Значение через прокси ("клиент, прокси1, ...") -> первый валидный IP списка.
    """
    if not value:
        return None
    for part in str(value).split(","):
        try:
            return str(ipaddress.ip_address(part.strip()))
        except ValueError:
            continue
    logger.debug("IP-адрес не распознан, сохраняется NULL: %r", value)
    return None


REAL_DIGITS = 6


def real(value):
    """REAL (float32) из PostgreSQL -> float без хвоста расширения (0.8, а не 0.800000011920929)."""
    return None if value is None else round(float(value), REAL_DIGITS)


def hist_index(conf: float) -> int:
    """Корзина гистограммы 0..9; в SQL то же самое считает HIST_INDEX_SQL."""
    # round: REAL в PostgreSQL даёт 0.699999988 вместо 0.7
    return min(max(int(round(conf * 10, 6)), 0), 9)


def hist_bin(conf: float) -> str:
    idx = hist_index(conf)
    return f"{idx / 10:.1f}-{(idx + 1) / 10:.1f}"


# SQL-двойники real() и hist_index(): округление до 6 знаков, *10, округление, отсечение.
REAL_SQL = "round({col}::float8::numeric, 6)"
HIST_INDEX_SQL = "least(greatest(trunc(round(" + REAL_SQL + " * 10, 6)), 0), 9)::int"


def stats_payload(counts: dict) -> dict:
    """Ответ stats() из {статус: число}; неизвестные статусы не учитываются (и в total).

    В PostgreSQL неизвестного статуса не бывает: CHECK у appeal_verifications его не пропустит,
    так что расхождения с прежним sum(counts.values()) на практике нет.
    """
    c = {s: int(counts.get(s, 0)) for s in STATUSES}
    return {
        "total": sum(c.values()),
        "verified": c["confirmed"] + c["corrected"],
        "pending": c["pending"],
        "confirmed": c["confirmed"],
        "corrected": c["corrected"],
        "rejected": c["rejected"],
    }


def question_entry(text, code, confidence, alternative_codes) -> dict:
    """Вопрос карточки в формате старого appeals_log.jsonl."""
    return {
        "question_text": text,
        "selected_code": code,
        "confidence": real(confidence),
        "alternative_codes": list(alternative_codes or []),
    }


def log_entry(aid, created_at, appeal_text, candidates, overall_confidence, questions,
              status, verified_at, operator_codes, comment) -> dict:
    """Карточка в формате старого appeals_log.jsonl (as_log_entries)."""
    return {
        "id": str(aid),
        "timestamp": local_iso(created_at),
        "appeal_text": appeal_text,
        "candidates": candidates or [],
        "agent_questions": questions,
        "overall_confidence": real(overall_confidence) or 0.0,
        "verification": {
            "status": status,
            "verified_at": local_iso(verified_at),
            "operator_codes": None if operator_codes is None else list(operator_codes),
            "comment": comment,
        },
    }


def window_start(days: int) -> datetime:
    """Нижняя граница выборки для daily_usage (с запасом в сутки на часовой пояс)."""
    return now_aware() - timedelta(days=days + 1)


def backoffice_payload(*, status_counts: dict, conf_sum: float, conf_n: int,
                       hist_counts: dict, code_counts: dict, ip_counts: dict,
                       recent_created: Iterable[datetime], days: int) -> dict:
    """Ответ backoffice_stats из агрегатов (общий для memory и PostgreSQL).

    hist_counts: {корзина 0..9: число}; code_counts — только по confirmed/corrected;
    recent_created — created_at карточек не старше window_start(days).
    Сортировка top_codes / ip_stats здесь, в Python: не зависит от collation БД.
    """
    hist = histogram_template()
    for idx, n in hist_counts.items():
        idx = min(max(int(idx), 0), 9)
        hist[f"{idx / 10:.1f}-{(idx + 1) / 10:.1f}"] += int(n)
    date_counts: Counter = Counter(local_iso(dt)[:10] for dt in recent_created)
    today = datetime.now().date()
    daily = []
    for i in range(days - 1, -1, -1):
        d = (today - timedelta(days=i)).isoformat()
        daily.append({"date": d, "count": date_counts.get(d, 0)})
    sc = {s: int(n) for s, n in status_counts.items()}
    return {
        "total_classifications": sum(sc.values()),
        "total_verifications": sc.get("confirmed", 0) + sc.get("corrected", 0),
        "confirmed": sc.get("confirmed", 0),
        "corrected": sc.get("corrected", 0),
        "rejected": sc.get("rejected", 0),
        "pending": sc.get("pending", 0),
        "avg_confidence": round(float(conf_sum) / conf_n, 2) if conf_n else 0.0,
        "confidence_histogram": hist,
        "top_codes": [
            {"code": c, "count": int(n)}
            for c, n in sorted(code_counts.items(), key=lambda kv: (-kv[1], kv[0]))[:10]
        ],
        "daily_usage": daily,
        "ip_stats": [
            {"ip": ip, "count": int(n)}
            for ip, n in sorted(ip_counts.items(), key=lambda kv: (-kv[1], kv[0]))
        ],
    }


def compute_backoffice_stats(cards: Iterable[tuple], days: int) -> dict:
    """cards: (created_at, overall_confidence|None, status, ip|None, selected_codes).

    selected_codes передаются для всех карточек, но учитываются только у confirmed/corrected.
    """
    status_counts: Counter = Counter()
    code_counter: Counter = Counter()
    ip_counter: Counter = Counter()
    hist_counts: Counter = Counter()
    recent = []
    since = window_start(days)
    conf_sum, conf_n = 0.0, 0
    for created_at, conf, status, ip, codes in cards:
        conf = real(conf)
        status_counts[status] += 1
        if conf is not None:
            conf_sum += conf
            conf_n += 1
            hist_counts[hist_index(conf)] += 1
        if status in ("confirmed", "corrected"):
            for c in codes:
                if c:
                    code_counter[c] += 1
        if ip:
            ip_counter[ip] += 1
        if aware(created_at) >= since:
            recent.append(created_at)
    return backoffice_payload(
        status_counts=status_counts, conf_sum=conf_sum, conf_n=conf_n,
        hist_counts=hist_counts, code_counts=code_counter, ip_counts=ip_counter,
        recent_created=recent, days=days,
    )
