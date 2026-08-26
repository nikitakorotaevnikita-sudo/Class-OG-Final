"""
CrossEncoder reranker через transformers напрямую (sentence-transformers wrapper
саturate scores на BGE — переписано на AutoModelForSequenceClassification).

Применяется ПОСЛЕ heuristic rerank на top-N лучших кандидатов от dense+lexical+heuristic.
Вычисляет (query, candidate) логиты → смешивает с heuristic score → пересортировка.

Безопасный fallback: если модель не загружается — возвращает кандидатов как есть.
"""

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

import env_bootstrap  # noqa: F401  — .env до HF-библиотек

from config import CROSS_ENCODER_MODEL, CE_RERANK_TOP_N, CE_BLEND_WEIGHT


class CrossEncoderReranker:
    """Lazy-load CrossEncoder; rerank topN candidates by (query, candidate_text) logit."""

    def __init__(self, model_name: str | None = None):
        self.model_name = model_name or CROSS_ENCODER_MODEL
        self._tokenizer = None
        self._model = None
        self._failed = False

    def _ensure_loaded(self) -> bool:
        if self._model is not None and self._tokenizer is not None:
            return True
        if self._failed:
            return False
        try:
            import torch
            torch.set_num_threads(1)
            from transformers import AutoTokenizer, AutoModelForSequenceClassification
            print(f"  [CE] Загрузка CrossEncoder: {self.model_name}")
            self._tokenizer = AutoTokenizer.from_pretrained(self.model_name)
            self._model = AutoModelForSequenceClassification.from_pretrained(self.model_name)
            self._model.eval()
            self._torch = torch
            print(f"  [CE] Готово.")
            return True
        except Exception as e:
            print(f"  [CE] Ошибка загрузки {self.model_name}: {e}. Fallback на heuristic.")
            self._failed = True
            return False

    @staticmethod
    def _candidate_text(c: dict) -> str:
        return f"{c.get('name', '')}. {c.get('full_path', '')}"

    @staticmethod
    def _normalize_scores(scores: list[float]) -> list[float]:
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
        if not candidates:
            return []
        if not self._ensure_loaded():
            return candidates[:top_k]

        n = top_n if top_n and top_n > 0 else (CE_RERANK_TOP_N or len(candidates))
        n = min(n, len(candidates))
        head = candidates[:n]
        tail = candidates[n:]

        try:
            torch = self._torch
            queries = [query] * len(head)
            passages = [self._candidate_text(c) for c in head]
            with torch.no_grad():
                inputs = self._tokenizer(
                    queries, passages,
                    padding=True, truncation=True,
                    return_tensors="pt", max_length=512,
                )
                logits = self._model(**inputs, return_dict=True).logits.view(-1).float()
            scores = logits.tolist()
        except Exception as e:
            print(f"  [CE] Ошибка predict: {e}. Возвращаю heuristic-порядок.")
            return candidates[:top_k]

        # Сохраняем raw CE логиты
        for c, s in zip(head, scores):
            c["ce_score"] = float(s)

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
        result = reordered_head + tail
        return result[:top_k]
