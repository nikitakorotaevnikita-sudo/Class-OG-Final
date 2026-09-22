"""Шаг Б: Laya выбирает код из top-10 кандидатов ретривера.

Гипотеза: System One-модель переупорядочивает кандидатов лучше, чем ретривер,
и даёт калиброванную вероятность — её можно использовать как второе мнение и
как признак «на проверку оператору».

Меряем: Top-1 против базовой линии (top-1 ретривера) и против потолка (эталон
вообще присутствует в top-10), калибровку и задержку на CPU.
"""
import io
import json
import os
import statistics
import sys
import time

sys.stdout.reconfigure(encoding="utf-8")

IN = os.environ["TEMP"] + "/laya_candidates.json"
OUT = os.environ["TEMP"] + "/laya_result_%s.json" % os.environ.get("LAYA_TAG", "base")
MODEL = os.environ.get("LAYA_MODEL", "convaiinnovations/laya-multilingual")
LIMIT = int(os.environ.get("LAYA_LIMIT", "0"))       # 0 = все

rows = json.load(io.open(IN, encoding="utf-8"))
if LIMIT:
    rows = rows[:LIMIT]
print(f"обращений: {len(rows)}, модель: {MODEL}")

import laya                                                   # noqa: E402

t0 = time.time()
agent = laya.load(MODEL)
print(f"модель загружена за {time.time() - t0:.0f}с")

# Бюджет вариантов: 10 наименований по ~16 токенов ≈ 160 — в лимит 256 влезает,
# но поднимем явно, чтобы длинные наименования не обрезались.
try:
    agent.cfg["head_max_len"] = 512
    agent.cfg["max_len"] = 1024
except Exception as exc:                                       # noqa: BLE001
    print("не удалось поднять бюджет:", exc)

results, latencies = [], []
for i, row in enumerate(rows, 1):
    # Ключ варианта — наименование кода (его и видит модель), значение —
    # путь по иерархии как пояснение. Дубли наименований разводим индексом.
    options, codes = {}, []
    for j, c in enumerate(row["candidates"]):
        label = c["name"][:120]
        if label in options:
            label = f"{label} ({j + 1})"
        options[label] = c.get("full_path") if os.environ.get("LAYA_PATHS") else None
        codes.append(c["code"])
    questions = {
        "code": {
            "type": "choice",
            "instructions": (
                "Which category of the citizens' appeals classifier does this appeal belong to?"
                if os.environ.get("LAYA_EN")
                else "Определи, к какой категории классификатора относится вопрос обращения гражданина."),
            "criteria": options,
        }
    }
    started = time.time()
    try:
        answer = agent.predict(row["appeal_text"][:4000], questions)
    except Exception as exc:                                   # noqa: BLE001
        print(f"  [{i}] ошибка предсказания: {type(exc).__name__}: {exc}")
        if i == 1:
            raise
        continue
    latencies.append((time.time() - started) * 1000)

    a = answer["answers"]["code"]
    labels = list(options.keys())
    picked_idx = labels.index(a["choice"]) if a.get("choice") in labels else None
    prob = max(a.get("probabilities", {}).values()) if a.get("probabilities") else a.get("confidence")
    if picked_idx is None:
        if i == 1:
            print("структура ответа:", json.dumps(a, ensure_ascii=False, default=str)[:400])
        continue

    picked = codes[picked_idx]
    results.append({
        "id": row["id"], "gold": row["gold"], "picked": picked,
        "retriever_top1": codes[0], "prob": prob,
        "gold_in_pool": row["gold"] in codes,
        "correct": picked == row["gold"],
    })
    if i % 10 == 0:
        print(f"  {i}/{len(rows)}")

if not results:
    print("ни одного разобранного ответа — см. структуру выше")
    sys.exit(1)

n = len(results)
laya_top1 = sum(r["correct"] for r in results)
base_top1 = sum(r["retriever_top1"] == r["gold"] for r in results)
ceiling = sum(r["gold_in_pool"] for r in results)

print("\n=== ИТОГ ===")
print(f"обращений с ответом : {n}")
print(f"потолок (эталон в top-10): {ceiling}/{n} = {ceiling / n:.1%}")
print(f"Laya Top-1          : {laya_top1}/{n} = {laya_top1 / n:.1%}")
print(f"ретривер Top-1      : {base_top1}/{n} = {base_top1 / n:.1%}")
if ceiling:
    print(f"Laya внутри достижимого: {laya_top1}/{ceiling} = {laya_top1 / ceiling:.1%}")

with_prob = [r for r in results if isinstance(r["prob"], (int, float))]
if with_prob:
    right = [r["prob"] for r in with_prob if r["correct"]]
    wrong = [r["prob"] for r in with_prob if not r["correct"]]
    print(f"уверенность когда права : {statistics.mean(right):.3f} (n={len(right)})" if right else "верных нет")
    print(f"уверенность когда ошиблась: {statistics.mean(wrong):.3f} (n={len(wrong)})" if wrong else "ошибок нет")

if latencies:
    print(f"задержка CPU: медиана {statistics.median(latencies):.0f} мс, "
          f"p90 {sorted(latencies)[int(len(latencies) * 0.9)]:.0f} мс")

io.open(OUT, "w", encoding="utf-8").write(json.dumps(results, ensure_ascii=False, indent=2))
print(f"\nподробности: {OUT}")
