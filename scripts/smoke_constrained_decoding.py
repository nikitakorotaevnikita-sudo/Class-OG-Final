"""Smoke-test constrained decoding via Ario response_format=json_schema.

Tests two scenarios:
1. Simple FINAL_PICK schema — should return valid JSON matching schema
2. Same call WITHOUT response_format (control) — for comparison
"""
import sys
import json
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import httpx
import jsonschema

from config import ARIO_API_KEY, ARIO_BASE_URL, ARIO_MODEL
from llm_schemas import FINAL_PICK_SCHEMA


SYSTEM = """Ты классификатор обращений граждан. По тексту обращения выбери ОДИН код из 4-уровневого классификатора (формат XXXX.XXXX.XXXX.XXXX). Верни только JSON."""

USER = """Текст обращения: Жалоба на бездействие управляющей компании по уборке двора.

Кандидаты:
- 0005.0005.0056.1162 — Управление многоквартирными домами / Бездействие УК
- 0005.0005.0055.1130 — Жилищный фонд / Капитальный ремонт
- 0002.0007.0074.0300 — Льготы инвалидам

Выбери лучший код."""


def call_ario_with_schema(schema):
    """Call Ario with response_format=json_schema."""
    client = httpx.Client(
        base_url=ARIO_BASE_URL,
        headers={"Authorization": f"Bearer {ARIO_API_KEY}"},
        timeout=60,
    )
    try:
        r = client.post(
            "/chat/completions",
            json={
                "model": ARIO_MODEL,
                "messages": [
                    {"role": "system", "content": SYSTEM},
                    {"role": "user", "content": USER},
                ],
                "temperature": 0.0,
                "max_tokens": 300,
                "response_format": {
                    "type": "json_schema",
                    "json_schema": {
                        "name": "final_pick",
                        "schema": schema,
                        "strict": True,
                    },
                },
            },
        )
        r.raise_for_status()
        return r.json()["choices"][0]["message"]["content"].strip()
    finally:
        client.close()


def call_ario_plain():
    """Call Ario without response_format (control)."""
    client = httpx.Client(
        base_url=ARIO_BASE_URL,
        headers={"Authorization": f"Bearer {ARIO_API_KEY}"},
        timeout=60,
    )
    try:
        r = client.post(
            "/chat/completions",
            json={
                "model": ARIO_MODEL,
                "messages": [
                    {"role": "system", "content": SYSTEM + ' Формат: {"code":"...","confidence":0.9,"reasoning":"..."}'},
                    {"role": "user", "content": USER},
                ],
                "temperature": 0.0,
                "max_tokens": 300,
            },
        )
        r.raise_for_status()
        return r.json()["choices"][0]["message"]["content"].strip()
    finally:
        client.close()


print("=" * 60)
print("Test 1: WITH response_format=json_schema")
print("=" * 60)
try:
    raw = call_ario_with_schema(FINAL_PICK_SCHEMA)
    print(f"Raw response:\n{raw}\n")
    parsed = json.loads(raw)
    jsonschema.validate(parsed, FINAL_PICK_SCHEMA)
    print(f"VALID JSON matching schema. Code: {parsed['code']}, conf: {parsed['confidence']}")
    success_with_schema = True
except Exception as e:
    print(f"FAILED: {type(e).__name__}: {e}")
    success_with_schema = False

print("\n" + "=" * 60)
print("Test 2: WITHOUT response_format (control)")
print("=" * 60)
try:
    raw = call_ario_plain()
    print(f"Raw response:\n{raw}\n")
    # Strip wrappers
    for prefix in ("```json", "```"):
        if raw.startswith(prefix):
            raw = raw[len(prefix):].strip()
    if raw.endswith("```"):
        raw = raw[:-3].strip()
    parsed = json.loads(raw)
    try:
        jsonschema.validate(parsed, FINAL_PICK_SCHEMA)
        print(f"Coincidentally valid. Code: {parsed.get('code')}")
        plain_valid = True
    except jsonschema.ValidationError as e:
        print(f"Plain mode parsed but doesn't match schema strictly: {e.message}")
        plain_valid = False
except Exception as e:
    print(f"FAILED: {type(e).__name__}: {e}")
    plain_valid = False

print("\n" + "=" * 60)
print("SUMMARY")
print("=" * 60)
print(f"With response_format: {'OK' if success_with_schema else 'FAIL'}")
print(f"Without response_format: {'lucky' if plain_valid else 'invalid'}")

if not success_with_schema:
    print("\nNOTE: vLLM/Qwen3 may have a known bug (#18819). Try setting enable_thinking=True or use fallback grammar-in-prompt.")
    sys.exit(1)
