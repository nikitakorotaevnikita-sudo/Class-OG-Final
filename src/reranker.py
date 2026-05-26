"""
CrossEncoder reranker (опциональный).

Применяется ПОСЛЕ heuristic rerank — на топ-N лучших кандидатов от dense+lexical+heuristic.
Делает CE-предсказание (query, candidate_text) → score, смешивает с heuristic score
и пересортирует.

Безопасный fallback: если модель не загружается — возвращает кандидатов в исходном порядке.
"""

import os
import sys
from pathlib import Path

# Окружение для HF mirror (так же как в classifier_agent)
os.environ.setdefault("HF_ENDPOINT", "https://hf-mirror.com")
os.environ.setdefault("HF_HUB_DISABLE_SSL", "1")

sys.path.insert(0, str(Path(__file__).parent))

from config import CROSS_ENCODER_MODEL, CE_RERANK_TOP_N, CE_BLEND_WEIGHT


class CrossEncoderReranker:
    """Lazy-load CrossEncoder; rerank topN candidates by (query, candidate_text) score."""

    def __init__(self, model_name: str | None = None):
        self.model_name = model_name or CROSS_ENCODER_MODEL
        self._model = None
        self._failed = False

    def _ensure_loaded(self) -> bool:
        """Lazy-load. Return True if model is ready."""
        if self._model is not None:
            return True
        if self._failed:
            return False
        try:
            from sentence_transformers import CrossEncoder
            print(f"  [CE] Загрузка CrossEncoder: {self.model_name}")
            self._model = CrossEncoder(self.model_name, max_length=512)
            print(f"  [CE] Готово.")
            return True
        except Exception as e:
            print(f"  [CE] Ошибка загрузки {self.model_name}: {e}. Fallback на heuristic.")
            self._failed = True
            return False

    @staticmethod
    def _candidate_text(c: dict) -> str:
        """Build text to feed CrossEncoder for a candidate."""
        return f"{c.get('name', '')}. {c.get('full_path', '')}"

    @staticmethod
    def _normalize_scores(scores: list[float]) -> list[float]:
        """Min-max normalize to [0, 1]; if all equal — returns 0.5."""
        if not scores:
            return []
        lo, hi = min(scores), max(scores)
        if hi - lo < 1e-9:
            return [0.5] * len(scores)
        return [(s - lo) / (hi - lo) for s in scores]

    def rerank(
        self,
        query: str,
        candidates: list[dict],
        top_k: int,
        top_n: int | None = None,
        blend_weight: float | None = None,
    ) -> list[dict]:
        """Rerank candidates with CrossEncoder.

        Args:
            query: текст вопроса
            candidates: уже отсортированные heuristic-rerank кандидаты
            top_k: сколько вернуть
            top_n: сколько передать в CE (default — CE_RERANK_TOP_N или len(candidates))
            blend_weight: вес CE в смеси с heuristic (default CE_BLEND_WEIGHT)

        Returns:
            top_k кандидатов, пересортированных с учётом CE
        """
        if not candidates:
            return []
        if not self._ensure_loaded():
            return candidates[:top_k]

        n = top_n if top_n and top_n > 0 else (CE_RERANK_TOP_N or len(candidates))
        n = min(n, len(candidates))
        head = candidates[:n]
        tail = candidates[n:]

        pairs = [(query, self._candidate_text(c)) for c in head]
        try:
            scores = self._model.predict(pairs, show_progress_bar=False).tolist()
        except Exception as e:
            print(f"  [CE] Ошибка predict: {e}. Возвращаю heuristic-порядок.")
            return candidates[:top_k]

        # Сохраняем CE score в кандидата
        for c, s in zip(head, scores):
            c["ce_score"] = float(s)

        # Получаем heuristic score (для смеси). Используем 1.0 - rank/n как прокси если нет similarity.
        heuristic_scores = [float(c.get("similarity", 0.0)) for c in head]
        h_norm = self._normalize_scores(heuristic_scores)
        ce_norm = self._normalize_scores(scores)

        w = blend_weight if blend_weight is not None else CE_BLEND_WEIGHT
        blended = [(1.0 - w) * h + w * ce for h, ce in zip(h_norm, ce_norm)]

        ranked = sorted(
            zip(blended, range(len(head)), head),
            key=lambda x: (-x[0], x[1]),
        )
        reordered_head = [c for _, _, c in ranked]

        # Финальный список: пересортированный head + хвост (если в top_k не помещается head)
        result = reordered_head + tail
        return result[:top_k]
