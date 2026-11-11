"""Общая логика AppealStore для memory и PostgreSQL бэкендов."""

from __future__ import annotations

import ipaddress
import uuid
from collections import Counter
from datetime import datetime, timedelta, timezone
from typing import Iterable, Optional

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
    """Валидный IP (без маски) или None (например, 'unknown')."""
    if not value:
        return None
    try:
        return str(ipaddress.ip_address(str(value).strip()))
    except ValueError:
        return None


REAL_DIGITS = 6


def real(value):
    """REAL (float32) из PostgreSQL -> float без хвоста расширения (0.8, а не 0.800000011920929)."""
    return None if value is None else round(float(value), REAL_DIGITS)


def hist_bin(conf: float) -> str:
    # round: REAL в PostgreSQL даёт 0.699999988 вместо 0.7
    idx = min(max(int(round(conf * 10, 6)), 0), 9)
    return f"{idx / 10:.1f}-{(idx + 1) / 10:.1f}"


def compute_backoffice_stats(cards: Iterable[tuple], days: int) -> dict:
    """cards: (created_at, overall_confidence|None, status, ip|None, selected_codes).

    selected_codes передаются для всех карточек, но учитываются только у confirmed/corrected.
    """
    status_counts: Counter = Counter()
    code_counter: Counter = Counter()
    ip_counter: Counter = Counter()
    date_counts: Counter = Counter()
    hist = histogram_template()
    conf_sum, conf_n, total = 0.0, 0, 0
    for created_at, conf, status, ip, codes in cards:
        conf = real(conf)
        total += 1
        status_counts[status] += 1
        if conf is not None:
            conf_sum += conf
            conf_n += 1
            hist[hist_bin(conf)] += 1
        if status in ("confirmed", "corrected"):
            for c in codes:
                if c:
                    code_counter[c] += 1
        if ip:
            ip_counter[ip] += 1
        date_counts[local_iso(created_at)[:10]] += 1
    today = datetime.now().date()
    daily = []
    for i in range(days - 1, -1, -1):
        d = (today - timedelta(days=i)).isoformat()
        daily.append({"date": d, "count": date_counts.get(d, 0)})
    return {
        "total_classifications": total,
        "total_verifications": status_counts["confirmed"] + status_counts["corrected"],
        "confirmed": status_counts["confirmed"],
        "corrected": status_counts["corrected"],
        "rejected": status_counts["rejected"],
        "pending": status_counts["pending"],
        "avg_confidence": round(conf_sum / conf_n, 2) if conf_n else 0.0,
        "confidence_histogram": hist,
        "top_codes": [
            {"code": c, "count": n}
            for c, n in sorted(code_counter.items(), key=lambda kv: (-kv[1], kv[0]))[:10]
        ],
        "daily_usage": daily,
        "ip_stats": [
            {"ip": ip, "count": n}
            for ip, n in sorted(ip_counter.items(), key=lambda kv: (-kv[1], kv[0]))
        ],
    }
