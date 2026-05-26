"""
End-to-end verification: classifies the failing 5-question appeal through
the new per-question pipeline.

Expected: 5 distinct codes (NOT all "Отсутствует адресат обращения").
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from classifier_agent import ClassifierAgent


APPEAL = """Заявитель повторно обращается!!

1.Необходима доставка лекарств на дом (инсулин). Звоню в Городскую поликлинику №5, даже инсулин привести некому. П

2. О бесплатном социально работнике. Заявитель сама себя обслуживает, на улицу выйти не может, так как ничего не видит (на днях вышла и упала, увезли в травмпункт, где провела несколько часов).

3. Об оказании помощи в настройке телефона и обучении пользованию цифровыми сервисами.

15 летний внук оставил меня, переехал в деревню, живет без копейки. У его мамы (Яны) онкозаболевание, она растит мальчика Защитника Отечества, который является гордостью Школы в Горьковке (имеет больше 40 медалей). Яну, сняли с группы инвалидности в городской поликлинике № 5.

Сейчас живем на мою пенсию. У внука (Кирилла) даже нет проездного.  За проезд оплачивает из моей пенсии."""


def main():
    agent = ClassifierAgent()
    result = agent.classify(APPEAL)

    print("\n" + "=" * 80)
    print("РЕЗУЛЬТАТ КЛАССИФИКАЦИИ")
    print("=" * 80)
    print(f"Вид:         {result.vid_obrascheniya}")
    print(f"Тип:         {result.tip_obrascheniya}")
    print(f"Уверенность: {result.overall_confidence:.0%}")
    print(f"Требует верификации: {result.needs_verification}")
    print(f"Вопросов:    {len(result.questions)}")

    codes_seen = []
    for i, q in enumerate(result.questions, 1):
        print(f"\n--- Вопрос {i}: {q.question_text[:80]}")
        print(f"    Код:       {q.code}")
        print(f"    Тема:      {q.name}")
        print(f"    Путь:      {q.full_path[:90]}")
        print(f"    Уверенность: {q.confidence:.0%}")
        print(f"    Обоснование: {q.reasoning[:200]}")
        codes_seen.append(q.code)

    print("\n" + "=" * 80)
    print("ПРОВЕРКА")
    print("=" * 80)

    unique = set(codes_seen)
    fallback = "0001.0002.0027.0126"

    print(f"  Уникальных кодов: {len(unique)} из {len(codes_seen)}")
    print(f"  Содержит fallback '{fallback}': {fallback in unique}")

    if fallback in codes_seen:
        count = codes_seen.count(fallback)
        if count == len(codes_seen):
            print("  FAIL: все вопросы получили fallback-код (баг НЕ починен)")
            sys.exit(1)
        else:
            print(f"  PARTIAL: {count} из {len(codes_seen)} получили fallback")
    elif len(unique) >= max(2, len(codes_seen) - 1):
        print("  PASS: коды разные, fallback не использован")
    else:
        print(f"  WARNING: только {len(unique)} уникальных кодов")


if __name__ == "__main__":
    main()
