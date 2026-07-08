"""
Debug script: retrieval-only diagnostic for the failing 5-question appeal.

Reproduces what candidates the LLM sees, WITHOUT calling the LLM.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from classifier_agent import ClassifierAgent


APPEAL = """Заявитель повторно обращается!!

1.Необходима доставка лекарств на дом (инсулин). Звоню в Городскую поликлинику №5, даже инсулин привести некому.

2. О бесплатном социально работнике. Заявитель сама себя обслуживает, на улицу выйти не может, так как ничего не видит (на днях вышла и упала, увезли в травмпункт, где провела несколько часов).

3. Об оказании помощи в настройке телефона и обучении пользованию цифровыми сервисами.

15 летний внук оставил меня, переехал в деревню, живет без копейки. У его мамы (Яны) онкозаболевание, она растит мальчика Защитника Отечества, который является гордостью Школы в Горьковке (имеет больше 40 медалей). Яну, сняли с группы инвалидности в городской поликлинике № 5.

Сейчас живем на мою пенсию. У внука (Кирилла) даже нет проездного.  За проезд оплачивает из моей пенсии."""


SEGMENTS = [
    "Необходима доставка лекарств на дом (инсулин). Звоню в Городскую поликлинику №5, даже инсулин привести некому.",
    "О бесплатном социальном работнике. Заявитель сама себя обслуживает, на улицу выйти не может, так как ничего не видит, упала, увезли в травмпункт.",
    "Об оказании помощи в настройке телефона и обучении пользованию цифровыми сервисами.",
    "У мамы внука (Яны) онкозаболевание, её сняли с группы инвалидности в городской поликлинике № 5.",
    "У внука 15-ти лет нет проездного, за проезд оплачиваю из пенсии. Прошу помощи семье с подростком.",
]


def print_candidates(label: str, candidates: list, limit: int = 15):
    print(f"\n=== {label} (top {limit}) ===")
    for i, c in enumerate(candidates[:limit], 1):
        sim = c.get("similarity", 0.0)
        src = c.get("source", "?")
        path = c.get("full_path", "")[:90]
        print(f"  {i:2d}. [{sim:.3f}] [{src:7s}] {c['code']}  {c['name'][:50]}")
        print(f"         {path}")


def main():
    agent = ClassifierAgent()

    print("\n" + "=" * 80)
    print("ШАГ 1: Retrieval на ПОЛНОМ тексте обращения (как делает агент сейчас)")
    print("=" * 80)
    print(f"Текст ({len(APPEAL)} символов):\n{APPEAL[:200]}...")

    dense = agent._search_candidates(APPEAL, top_k=50)
    lexical = agent._search_lexical_candidates(APPEAL, top_k=30)
    merged = agent._merge_candidate_pools(dense, lexical)
    reranked = agent._rerank_candidates(APPEAL, merged, top_k=10)

    print_candidates("DENSE top-50 (видит LLM)", dense, limit=15)
    print_candidates("LEXICAL top-30", lexical, limit=15)
    print_candidates("FINAL RERANKED top-10 (это уходит в LLM)", reranked, limit=10)

    # Check if fallback code is in candidates
    fallback = "0001.0002.0027.0126"
    in_dense = any(c["code"] == fallback for c in dense)
    in_lex = any(c["code"] == fallback for c in lexical)
    in_rer = any(c["code"] == fallback for c in reranked)
    print(f"\n>>> Код 'Отсутствует адресат обращения' ({fallback}):")
    print(f"    в dense top-50: {in_dense}")
    print(f"    в lexical top-30: {in_lex}")
    print(f"    в reranked top-10: {in_rer}")

    if in_rer:
        rank = next(i + 1 for i, c in enumerate(reranked) if c["code"] == fallback)
        print(f"    → ранг в reranked: {rank}")

    print("\n" + "=" * 80)
    print("ШАГ 2: Retrieval на КАЖДОМ вопросе ОТДЕЛЬНО")
    print("(показывает что бы вернула segmentation+per-question retrieval)")
    print("=" * 80)

    for i, seg in enumerate(SEGMENTS, 1):
        print(f"\n--- Вопрос {i}: {seg[:90]}...")
        seg_dense = agent._search_candidates(seg, top_k=10)
        seg_lex = agent._search_lexical_candidates(seg, top_k=10)
        seg_merged = agent._merge_candidate_pools(seg_dense, seg_lex)
        seg_reranked = agent._rerank_candidates(seg, seg_merged, top_k=5)
        for j, c in enumerate(seg_reranked, 1):
            print(f"   {j}. [{c.get('similarity', 0):.3f}] {c['code']}  {c['name'][:55]}")
            print(f"       {c.get('full_path','')[:90]}")


if __name__ == "__main__":
    main()
