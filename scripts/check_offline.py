"""Проверка готовности офлайн-установки.

Две стадии:

    python scripts/check_offline.py --stage pre    # до установки, только stdlib
    venv\\Scripts\\python.exe scripts/check_offline.py --stage post

`pre` запускается любым найденным интерпретатором и отвечает на вопрос
«хватит ли того, что лежит в бандле, чтобы установка прошла без интернета».
`post` запускается уже интерпретатором venv и проверяет, что сервис реально
поднимется: импорты, локальная модель, векторная база, размерности.

Код возврата: 0 — всё хорошо, 1 — есть блокирующие проблемы.
"""

import argparse
import os
import re
import sys
from pathlib import Path

# Батник переключает консоль в 65001; без reconfigure Python пишет в cp866
# и кириллица в отчёте превращается в мусор.
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except (AttributeError, ValueError):  # перенаправленный поток без reconfigure
    pass

ROOT = Path(__file__).resolve().parent.parent
BUNDLE = ROOT / "offline_bundle"

OK, WARN, FAIL = "OK  ", "WARN", "FAIL"

# Режим установки: офлайн-стенд требует запрета сети, обычная машина — нет.
# Переключается флагом --online и меняет часть проверок с блокирующих на
# предупреждения (одна и та же проверка нужна в обоих случаях, но её вес разный).
ONLINE = False
_results: list[tuple[str, str, str]] = []


def report(status: str, title: str, detail: str = "") -> None:
    _results.append((status, title, detail))
    line = f"  [{status}] {title}"
    print(line if not detail else f"{line}\n         {detail}")


def finish() -> int:
    failed = [r for r in _results if r[0] == FAIL]
    warned = [r for r in _results if r[0] == WARN]
    print()
    print(f"  итог: {len(_results) - len(failed) - len(warned)} ok, "
          f"{len(warned)} предупреждений, {len(failed)} блокирующих")
    if failed:
        print("\n  установка не пройдёт, пока не исправлено:")
        for _, title, _d in failed:
            print(f"    - {title}")
    return 1 if failed else 0


# ─────────────────────────── стадия pre (stdlib) ────────────────────────────

def check_python() -> str:
    """Возвращает тег ABI текущего интерпретатора (cp311, cp313 ...)."""
    tag = f"cp{sys.version_info.major}{sys.version_info.minor}"
    supported = {"cp311", "cp312", "cp313"}
    if tag in supported:
        report(OK, f"Python {sys.version.split()[0]} ({tag})")
    else:
        report(FAIL, f"Python {sys.version.split()[0]} не поддерживается",
               "нужен 3.11, 3.12 или 3.13; на 3.14 нет колёс sentence-transformers")
    if sys.maxsize <= 2 ** 32:
        report(FAIL, "интерпретатор 32-битный", "torch собирается только под 64 бита")
    return tag


def check_wheels(tag: str) -> None:
    """Колёса скомпилированных пакетов должны совпадать с ABI интерпретатора."""
    wheels_dir = BUNDLE / "wheels"
    if not wheels_dir.is_dir():
        report(FAIL, "нет offline_bundle/wheels",
               "собрать бандл: python scripts/make_offline_bundle.py")
        return

    wheels = sorted(p.name for p in wheels_dir.glob("*.whl"))
    if not wheels:
        report(FAIL, "offline_bundle/wheels пуста")
        return

    binary_tags: dict[str, set[str]] = {}
    for name in wheels:
        found = set(re.findall(r"cp3\d+", name))
        if found:
            pkg = name.split("-")[0].lower().replace("_", "-")
            binary_tags.setdefault(pkg, set()).update(found)

    # Пакет считается проблемным, только если у него нет колеса под наш тег и
    # при этом нет universal-колеса (py3-none-any) — такие в binary_tags не попадают.
    mismatched = {pkg: tags for pkg, tags in binary_tags.items() if tag not in tags}
    # cffi/hf_xet/safetensors и т.п. часто лежат с abi3-тегом более старой версии
    # (cp38-abi3), и он совместим со всеми более новыми — такие исключаем.
    mismatched = {pkg: tags for pkg, tags in mismatched.items()
                  if not any(f"{t}-abi3" in "".join(wheels) for t in tags)}

    report(OK if not mismatched else FAIL,
           f"колёс в бандле: {len(wheels)}, теги: {sorted({t for ts in binary_tags.values() for t in ts})}",
           "" if not mismatched else
           f"нет колёс под {tag}: {', '.join(sorted(mismatched))}. "
           f"Бандл собран под другой Python — либо поставить на стенде тот же "
           f"минор, либо пересобрать: python scripts/make_offline_bundle.py")

    critical = {"torch", "numpy", "scipy", "tokenizers", "safetensors"}
    have = {n.split("-")[0].lower().replace("_", "-") for n in wheels}
    missing = sorted(critical - have)
    if missing:
        report(FAIL, "в бандле нет обязательных пакетов", ", ".join(missing))


def check_model() -> None:
    """Каталог модели должен быть самодостаточным — без обращения к HF."""
    model_dir = BUNDLE / "models" / "multilingual-e5-base"
    if not model_dir.is_dir():
        report(FAIL, "нет offline_bundle/models/multilingual-e5-base")
        return
    required = ["config.json", "modules.json", "sentence_bert_config.json",
                "tokenizer.json", "tokenizer_config.json"]
    missing = [f for f in required if not (model_dir / f).is_file()]
    weights = list(model_dir.glob("model.safetensors")) + list(model_dir.glob("pytorch_model.bin"))
    if missing:
        report(FAIL, "в каталоге модели не хватает файлов", ", ".join(missing))
    elif not weights:
        report(FAIL, "в каталоге модели нет весов", "ожидается model.safetensors")
    else:
        size_mb = weights[0].stat().st_size // (1024 * 1024)
        report(OK, f"модель эмбеддингов на месте (веса {size_mb} МБ)")


def check_vector_db() -> None:
    """Готовая база избавляет стенд от построения векторов (10-15 минут)."""
    prebuilt = BUNDLE / "vector_db_prebuilt"
    installed = [d for d in (ROOT / "data").glob("vector_db*") if (d / "embeddings.npy").is_file()]
    if (prebuilt / "embeddings.npy").is_file():
        size_mb = (prebuilt / "embeddings.npy").stat().st_size // (1024 * 1024)
        report(OK, f"готовая векторная база в бандле ({size_mb} МБ)")
    elif installed:
        report(OK, f"векторная база уже установлена: {installed[0].name}")
    else:
        report(WARN, "нет готовой векторной базы",
               "установщик соберёт её локальной моделью — это 10-15 минут")


def check_env_file() -> None:
    """Типовые грабли Windows: .env.txt от «Блокнота», UTF-16 от Out-File, BOM."""
    env = ROOT / ".env"
    stray = ROOT / ".env.txt"
    if stray.is_file():
        report(FAIL, "рядом лежит .env.txt", "переименовать в .env (Проводник скрывает .txt)")
    if not env.is_file():
        report(WARN, "нет .env", "установщик создаст его из .env.example")
        return

    raw = env.read_bytes()
    if raw[:2] in (b"\xff\xfe", b"\xfe\xff"):
        report(FAIL, ".env в UTF-16", "перезаписать в UTF-8: обычно это результат Out-File без -Encoding utf8")
        return
    text = raw.decode("utf-8-sig", errors="replace")
    if raw[:3] == b"\xef\xbb\xbf":
        report(WARN, ".env с BOM", "python-dotenv справится, но лучше сохранить без BOM")

    active = {}
    for line in text.splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            active[k.strip()] = v.strip()

    if active.get("HF_HUB_OFFLINE", "").lower() in ("1", "true", "yes", "on"):
        report(OK, "HF_HUB_OFFLINE=1 — сеть в HF-библиотеках запрещена")
    elif ONLINE:
        report(OK, "HF_HUB_OFFLINE не задан — модель будет догружаться из сети",
               "для изолированного стенда флаг обязателен")
    else:
        report(FAIL, "в .env нет активной строки HF_HUB_OFFLINE=1",
               "без неё sentence-transformers пойдёт в сеть и упадёт с FileMetadataError")

    model = active.get("EMBEDDING_MODEL", "")
    if not model and ONLINE:
        report(OK, "EMBEDDING_MODEL не задан — берётся intfloat/multilingual-e5-base из сети")
    elif not model:
        report(FAIL, "в .env нет активной строки EMBEDDING_MODEL",
               "иначе берётся значение по умолчанию intfloat/multilingual-e5-base — это загрузка из интернета")
    elif "/" in model and not (ROOT / model).exists() and not Path(model).exists():
        report(FAIL, f"EMBEDDING_MODEL={model} — такого пути нет",
               "должен указывать на локальный каталог модели")
    else:
        report(OK, f"EMBEDDING_MODEL={model}")

    vdb = active.get("VECTOR_DB_DIR", "data/vector_db")
    if not (ROOT / vdb / "embeddings.npy").is_file():
        report(FAIL, f"VECTOR_DB_DIR={vdb} — база не найдена",
               "скопировать готовую базу из бандла или собрать её")
    else:
        report(OK, f"VECTOR_DB_DIR={vdb}")

    provider = active.get("LLM_PROVIDER", "")
    online_only = {"groq": "api.groq.com", "gemini": "Google AI", "ario": "llm.ario.directum360.ru"}
    if provider in online_only:
        report(WARN, f"LLM_PROVIDER={provider} требует доступа к {online_only[provider]}",
               "на изолированном стенде нужен custom (endpoint в своей сети) или ollama (модель локально)")
    elif provider in ("custom", "ollama"):
        url = active.get("CUSTOM_LLM_BASE_URL" if provider == "custom" else "OLLAMA_BASE_URL", "")
        report(OK, f"LLM_PROVIDER={provider}", f"endpoint: {url or 'не задан — проверить в бэкофисе'}")
    else:
        report(WARN, f"LLM_PROVIDER={provider or 'не задан'}")


# ─────────────────────────── стадия post (venv) ─────────────────────────────

def check_imports() -> None:
    for mod in ("numpy", "torch", "sentence_transformers", "fastapi", "uvicorn",
                "dotenv", "pandas", "openpyxl", "fitz"):
        try:
            __import__(mod)
        except Exception as exc:  # noqa: BLE001 — важен сам факт и текст
            report(FAIL, f"не импортируется {mod}", f"{type(exc).__name__}: {exc}")
            return
    report(OK, "все зависимости импортируются")


RUNTIME_PROBE = r"""
import json, os, sys
sys.path.insert(0, "src")
import env_bootstrap                     # .env до HF-библиотек
import huggingface_hub.constants as hf
out = {"offline": bool(hf.HF_HUB_OFFLINE)}
try:
    import numpy as np, config
    from sentence_transformers import SentenceTransformer
    model = SentenceTransformer(config.EMBEDDING_MODEL)
    out["dim"] = int(model.get_sentence_embedding_dimension())
    out["model"] = config.EMBEDDING_MODEL
    db = config.VECTOR_DB_DIR
    emb = np.load(os.path.join(db, "embeddings.npy"))
    with open(os.path.join(db, "metadata.json"), encoding="utf-8") as fh:
        meta = json.load(fh)
    out["rows"], out["cols"], out["records"] = int(emb.shape[0]), int(emb.shape[1]), len(meta)
    vec = model.encode("query: не вывозят мусор во дворе", normalize_embeddings=True)
    norms = np.linalg.norm(emb, axis=1, keepdims=True)
    sims = (emb / np.maximum(norms, 1e-9)) @ vec
    top = int(sims.argmax())
    out["top_sim"] = round(float(sims[top]), 3)
    out["top_code"] = meta[top].get("code")
    out["top_name"] = meta[top].get("name", "")[:45]
    out["threshold"] = float(config.FULL_FALLBACK_SIM_THRESHOLD)
except Exception as exc:
    out["error"] = f"{type(exc).__name__}: {exc}"
print("PROBE_JSON " + json.dumps(out, ensure_ascii=False))
"""


def check_runtime() -> None:
    """Прогон в ОТДЕЛЬНОМ процессе.

    Порядок импортов здесь и есть предмет проверки: `HF_HUB_OFFLINE` читается
    один раз, при импорте `huggingface_hub`. Если проверять в текущем процессе,
    где HF-библиотеки уже подтянуты предыдущими шагами, результат будет
    ложноотрицательным — ровно это и произошло на первом прогоне.
    """
    import json
    import subprocess

    env = dict(os.environ)
    # Флаги должны прийти из .env, а не из окружения проверяющего процесса.
    for key in ("HF_HUB_OFFLINE", "TRANSFORMERS_OFFLINE", "HF_ENDPOINT"):
        env.pop(key, None)

    proc = subprocess.run([sys.executable, "-c", RUNTIME_PROBE],
                          cwd=str(ROOT), env=env, capture_output=True, text=True,
                          encoding="utf-8", errors="replace")
    line = next((l for l in proc.stdout.splitlines() if l.startswith("PROBE_JSON ")), "")
    if not line:
        tail = (proc.stderr or proc.stdout).strip().splitlines()[-3:]
        report(FAIL, "прогон не запустился", " / ".join(tail) or f"код {proc.returncode}")
        return

    data = json.loads(line[len("PROBE_JSON "):])
    if data.get("error"):
        report(FAIL, "прогон упал", data["error"])
        return

    if data.get("offline"):
        report(OK, "офлайн-режим HF активен (флаг дошёл до huggingface_hub)")
    elif ONLINE:
        report(OK, "офлайн-режим HF не включён (обычная машина с доступом в сеть)")
    else:
        report(FAIL, "HF_HUB_OFFLINE не дошёл до huggingface_hub",
               "проверить, что env_bootstrap импортируется до sentence_transformers")

    report(OK, f"модель загружена без сети, dim={data['dim']}")

    if data["cols"] != data["dim"]:
        report(FAIL, f"размерность базы {data['cols']} != модели {data['dim']}",
               "база собрана другой моделью — пересобрать: python src/build_vectordb.py")
    elif data["rows"] != data["records"]:
        report(FAIL, f"в базе {data['rows']} векторов, а в metadata.json {data['records']} записей")
    else:
        report(OK, f"векторная база согласована: {data['rows']} записей x {data['cols']}")

    sim, thr = data["top_sim"], data["threshold"]
    report(OK, f"поиск работает: top1={sim} {data['top_code']} {data['top_name']}")
    if sim < thr:
        report(WARN, f"top1={sim} ниже порога FULL_FALLBACK_SIM_THRESHOLD={thr}",
               "полный классификатор будет уходить в LLM на каждом запросе. "
               "Для адаптированной базы порог обычно 0.15")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--stage", choices=("pre", "post"), default="pre")
    ap.add_argument("--online", action="store_true",
                    help="машина с доступом в интернет: не требовать офлайн-флагов")
    args = ap.parse_args()

    global ONLINE
    ONLINE = args.online

    mode = "обычной" if ONLINE else "офлайн-"
    print(f"\n  Проверка {mode}установки: стадия {args.stage}")
    print(f"  Корень проекта: {ROOT}\n")

    if args.stage == "pre":
        tag = check_python()
        if not ONLINE:
            # Онлайн-установке комплект не нужен: колёса берутся с PyPI,
            # модель — с Hugging Face.
            check_wheels(tag)
            check_model()
        check_vector_db()
        check_env_file()
    else:
        check_python()
        check_env_file()
        # runtime раньше check_imports: тот тянет HF-библиотеки в этот процесс,
        # но подпроцессу это уже не мешает — порядок оставлен для читаемости лога.
        check_runtime()
        check_imports()
    return finish()


if __name__ == "__main__":
    sys.exit(main())
