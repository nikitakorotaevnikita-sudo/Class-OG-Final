"""Шаг A: собрать по каждому обращению top-10 кандидатов ретривера и эталон.

Работает в основном venv (там sentence-transformers и векторная база).
Результат кладём файлом, чтобы шаг Б (Laya в отдельном окружении) не тянул
тяжёлые зависимости. Тексты обращений — ПДн, файл остаётся во временной папке.
"""
import io
import json
import os
import sys
import time

sys.path.insert(0, "src")
sys.stdout.reconfigure(encoding="utf-8")

from classifier_agent import ClassifierAgent

DATASET = "data/ii25_test.jsonl"
OUT = os.environ["TEMP"] + "/laya_candidates.json"
TOP_K = 10

rows = [json.loads(line) for line in io.open(DATASET, encoding="utf-8").read().splitlines() if line.strip()]
print(f"обращений в наборе: {len(rows)}")

agent = ClassifierAgent()
prepared, started = [], time.time()

for i, row in enumerate(rows, 1):
    text = row.get("appeal_text") or ""
    gold = row.get("assigned_code") or ""
    if not text.strip() or not gold:
        continue
    candidates = agent._retrieve_for_segment(text)[:TOP_K]
    prepared.append({
        "id": row.get("id"),
        "appeal_text": text,
        "gold": gold,
        "gold_name": row.get("code_name", ""),
        "candidates": [
            {"code": c["code"], "name": c.get("name", ""), "similarity": float(c.get("similarity", 0))}
            for c in candidates
        ],
    })
    if i % 10 == 0:
        print(f"  {i}/{len(rows)} за {time.time() - started:.0f}с")

io.open(OUT, "w", encoding="utf-8").write(json.dumps(prepared, ensure_ascii=False))

hit = sum(1 for p in prepared if any(c["code"] == p["gold"] for c in p["candidates"]))
top1 = sum(1 for p in prepared if p["candidates"] and p["candidates"][0]["code"] == p["gold"])
print(f"\nготово: {len(prepared)} обращений → {OUT}")
print(f"эталон вообще есть в top-{TOP_K}: {hit}/{len(prepared)} = {hit / len(prepared):.1%}  (потолок для Laya)")
print(f"top-1 самого ретривера:          {top1}/{len(prepared)} = {top1 / len(prepared):.1%}  (базовая линия)")
