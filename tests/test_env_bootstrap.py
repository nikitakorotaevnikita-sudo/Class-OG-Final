"""Офлайн-флаги должны доходить до HF-библиотек.

Проверка через подпроцесс — иначе бессмысленно: `huggingface_hub` читает
`HF_HUB_OFFLINE` один раз, при импорте, и в процессе pytest он уже импортирован
другими тестами. Регресс, который здесь ловится: модуль проекта импортирует
`sentence_transformers` раньше, чем читается `.env`, и на стенде без интернета
получается сетевой запрос вместо загрузки локальной модели.
"""

import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "src"

HF_MODULES = ("sentence_transformers", "transformers", "huggingface_hub", "torch")


def run_probe(code: str, env_extra: dict | None = None) -> dict:
    import os

    env = dict(os.environ)
    for key in ("HF_HUB_OFFLINE", "TRANSFORMERS_OFFLINE", "HF_ENDPOINT", "HF_HUB_DISABLE_SSL"):
        env.pop(key, None)
    env.update(env_extra or {})
    proc = subprocess.run([sys.executable, "-c", code], cwd=str(ROOT), env=env,
                          capture_output=True, text=True, encoding="utf-8", errors="replace")
    line = next((l for l in proc.stdout.splitlines() if l.startswith("JSON ")), "")
    assert line, f"проба не отдала результат:\n{proc.stdout}\n{proc.stderr}"
    return json.loads(line[len("JSON "):])


def test_offline_flag_reaches_huggingface_hub():
    """`HF_HUB_OFFLINE=1` в окружении доходит до констант библиотеки."""
    result = run_probe(
        'import json, sys; sys.path.insert(0, "src");'
        ' import env_bootstrap;'
        ' import huggingface_hub.constants as c;'
        ' import os;'
        ' print("JSON " + json.dumps({"offline": bool(c.HF_HUB_OFFLINE),'
        ' "transformers": os.environ.get("TRANSFORMERS_OFFLINE"),'
        ' "endpoint": os.environ.get("HF_ENDPOINT")}))',
        {"HF_HUB_OFFLINE": "1"},
    )
    assert result["offline"] is True
    # Смежные флаги подтягиваются, чтобы промах по кешу падал сразу.
    assert result["transformers"] == "1"
    # Зеркало HF в офлайне только путает диагностику.
    assert result["endpoint"] is None


ENDPOINT_PROBE = (
    'import json, os, sys; sys.path.insert(0, "src");'
    ' import env_bootstrap;'
    ' import huggingface_hub.constants as c;'
    ' print("JSON " + json.dumps({"offline": bool(c.HF_HUB_OFFLINE),'
    ' "endpoint": os.environ.get("HF_ENDPOINT"), "library_endpoint": c.ENDPOINT}))'
)


def test_online_mode_does_not_force_a_mirror():
    """Адрес HF не навязывается — библиотека идёт на huggingface.co.

    Регресс, который здесь закрыт: раньше подставлялось зеркало hf-mirror.com.
    На стенде с обычным интернетом это ломало запуск — до huggingface.co доступ
    был, до зеркала нет, и сервис падал при загрузке модели эмбеддингов.
    """
    result = run_probe(ENDPOINT_PROBE)
    assert result["offline"] is False
    assert result["endpoint"] is None
    assert result["library_endpoint"] == "https://huggingface.co"


def test_explicit_mirror_is_respected():
    """Кому зеркало нужно — задаёт его в .env, и оно применяется."""
    result = run_probe(ENDPOINT_PROBE, {"HF_ENDPOINT": "https://hf-mirror.com"})
    assert result["endpoint"] == "https://hf-mirror.com"
    assert result["library_endpoint"] == "https://hf-mirror.com"


@pytest.mark.parametrize("module", ["classifier_agent", "build_vectordb", "finetune_model", "reranker"])
def test_env_bootstrap_imported_before_hf_libraries(module):
    """В исходнике `env_bootstrap` должен стоять выше любой HF-библиотеки."""
    text = (SRC / f"{module}.py").read_text(encoding="utf-8")
    lines = text.splitlines()

    bootstrap_at = next((i for i, l in enumerate(lines)
                         if l.strip().startswith("import env_bootstrap")), None)
    assert bootstrap_at is not None, f"{module}.py не импортирует env_bootstrap"

    for i, line in enumerate(lines):
        stripped = line.strip()
        if not (stripped.startswith("import ") or stripped.startswith("from ")):
            continue
        for hf in HF_MODULES:
            if stripped.startswith(f"import {hf}") or stripped.startswith(f"from {hf}"):
                assert bootstrap_at < i, (
                    f"{module}.py:{i + 1} импортирует {hf} раньше env_bootstrap "
                    f"(строка {bootstrap_at + 1}) — офлайн-флаги из .env не подействуют"
                )
