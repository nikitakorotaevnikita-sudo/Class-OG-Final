"""
Hierarchy-aware utilities for classifier codes.

Code format: "XXXX.XXXX.XXXX.XXXX" (4-level), optionally with "XXXX.XXXX.XXXX.XXXX.XXXX" (5-level).
Each segment "0000" — placeholder, означает «нет данного уровня» (родительский код).

Sections (L1): 5 entries — Государство/Социальная сфера/Экономика/Оборона/ЖКХ
Subsections (L2): 21, L3: 206, L4: 1225, L5: 651.

Используется в reranker:
  - branch_agreement_scores(candidates): для каждого кандидата — сколько других кандидатов
    делят с ним L2 prefix. Высокая «густота» ветки = сильный сигнал.
  - parent_similarity_boost(candidates): если родительский код тоже найден в пуле — буст.
  - hierarchy_prune(candidates): если 1-2 L1-раздела доминируют по суммарной похожести,
    отсекаем кандидатов из других разделов (опционально, может быть слишком агрессивно).
"""

from __future__ import annotations


def code_parts(code: str) -> list[str]:
    """Возвращает сегменты кода: '0001.0002.0027.0124' → ['0001','0002','0027','0124']."""
    return (code or "").split(".")


def prefix_at_level(code: str, level: int) -> str:
    """Префикс кода первого `level` сегментов."""
    return ".".join(code_parts(code)[:max(level, 0)])


def parent_code(code: str) -> str:
    """Родитель = код без последнего непулевого сегмента.

    Простая реализация: код без последнего сегмента (если он не нулевой).
    Для строгой иерархии лучше использовать поле `parent_code` из metadata.
    """
    parts = code_parts(code)
    if len(parts) <= 1:
        return ""
    # Если последний сегмент 0000 — поднимаемся выше (но обычно metadata.parent_code достаточно)
    while parts and parts[-1] == "0000":
        parts = parts[:-1]
    if len(parts) <= 1:
        return ""
    return ".".join(parts[:-1])


def same_branch(a: str, b: str, level: int) -> bool:
    """Совпадают ли первые `level` сегментов кодов."""
    return prefix_at_level(a, level) == prefix_at_level(b, level)


def branch_agreement_scores(
    candidates: list[dict],
    level: int = 2,
    similarity_key: str = "similarity",
) -> dict[str, float]:
    """Для каждого кандидата считаем «density» его ветки на уровне `level`.

    Возвращает: code → score в [0, 1], где score =
      (сумма similarity других кандидатов в той же ветке) / (общая сумма similarity).
    Иначе говоря: какую долю «массы похожести» занимает ветка этого кандидата.

    Это даёт буст кандидатам из «густых» веток и НЕ-буст одиноким кодам.
    """
    if not candidates:
        return {}

    # Group by L<level> prefix
    branch_total: dict[str, float] = {}
    total = 0.0
    for c in candidates:
        sim = float(c.get(similarity_key, 0.0))
        prefix = prefix_at_level(c["code"], level)
        branch_total[prefix] = branch_total.get(prefix, 0.0) + sim
        total += sim

    if total <= 0:
        return {c["code"]: 0.0 for c in candidates}

    result: dict[str, float] = {}
    for c in candidates:
        prefix = prefix_at_level(c["code"], level)
        result[c["code"]] = branch_total[prefix] / total
    return result


def parent_similarity_boost(
    candidates: list[dict],
    code_index: dict[str, dict] | None = None,
    similarity_key: str = "similarity",
) -> dict[str, float]:
    """Если parent_code кандидата тоже в пуле с высоким score — буст.

    Возвращает: code → boost в [0, 1] = similarity родителя в пуле, если он есть.
    Если родителя в пуле нет → 0.
    """
    if not candidates:
        return {}

    pool_scores: dict[str, float] = {
        c["code"]: float(c.get(similarity_key, 0.0)) for c in candidates
    }

    result: dict[str, float] = {}
    for c in candidates:
        # Сначала пробуем поле parent_code из metadata
        parent = c.get("parent_code") or parent_code(c["code"])
        if not parent or parent == c["code"]:
            result[c["code"]] = 0.0
            continue
        # Ищем родителя в пуле (среди кандидатов) или вне пула — берём 0
        if parent in pool_scores:
            result[c["code"]] = pool_scores[parent]
        else:
            result[c["code"]] = 0.0
    return result


def dominant_l1_sections(
    candidates: list[dict],
    threshold: float = 0.60,
    similarity_key: str = "similarity",
) -> set[str]:
    """L1-разделы, которые в сумме занимают ≥ threshold от общей массы похожести.

    Возвращает set из L1 prefix-ов. Если пуст или результат включает ВСЕ L1 разделы,
    pruning делать не имеет смысла.
    """
    if not candidates:
        return set()

    total = 0.0
    section_total: dict[str, float] = {}
    for c in candidates:
        sim = float(c.get(similarity_key, 0.0))
        l1 = prefix_at_level(c["code"], 1)
        section_total[l1] = section_total.get(l1, 0.0) + sim
        total += sim

    if total <= 0:
        return set()

    # Сортируем разделы по их массе и берём пока не наберём threshold
    sorted_sections = sorted(section_total.items(), key=lambda x: -x[1])
    dominant: set[str] = set()
    cumulative = 0.0
    for l1, mass in sorted_sections:
        dominant.add(l1)
        cumulative += mass
        if cumulative / total >= threshold:
            break

    # Если pruning не имеет смысла (все разделы вошли) — возвращаем пустой set
    if len(dominant) >= len(section_total):
        return set()
    return dominant
