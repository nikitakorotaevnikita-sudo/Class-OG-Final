"""Сборка комплекта для установки на стенд без интернета.

Запускается на машине С интернетом, из корня проекта:

    python scripts/make_offline_bundle.py --python-version 3.11

`--python-version` — минор Python, который стоит НА СТЕНДЕ, а не на этой машине.
Колёса скомпилированных пакетов (torch, numpy, scipy, tokenizers) привязаны к ABI
интерпретатора: бандл, собранный под 3.13, на стенде с 3.11 не поставится.

Что попадает в offline_bundle:

    wheels/               колёса всех зависимостей под целевой интерпретатор
    models/…-e5-base/     модель эмбеддингов целиком (файлы, не кеш HF)
    vector_db_prebuilt/   готовая векторная база — стенду не нужно её считать
    env.stand.example     шаблон .env с офлайн-флагами

Проверить готовый комплект: python scripts/check_offline.py --stage pre
"""

import argparse
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parent.parent
BUNDLE = ROOT / "offline_bundle"

ENV_TEMPLATE = """# .env для стенда БЕЗ интернета.
# Скопировать в корень проекта как .env и подставить свои значения.

# --- Запрет сети в HF-библиотеках -----------------------------------------
# Читается один раз, при импорте huggingface_hub. Без этой строки
# sentence-transformers попытается сходить в сеть и упадёт с FileMetadataError.
HF_HUB_OFFLINE=1
TRANSFORMERS_OFFLINE=1

# --- Локальная модель вместо имени репозитория HF -------------------------
EMBEDDING_MODEL={model_rel}
ENABLE_EMBEDDING_ADAPTER=false

# --- Готовая векторная база ------------------------------------------------
VECTOR_DB_DIR={vdb_rel}
# Порог взят с машины сборки. У адаптированной базы косинусы низкие (top1 ~0.26),
# поэтому порог 0.15; для обычной базы (top1 ~0.82) он должен быть выше.
FULL_FALLBACK_SIM_THRESHOLD={fallback_threshold}

# --- LLM ------------------------------------------------------------------
# На изолированном стенде работают только провайдеры внутри сети:
#   custom  — любой OpenAI-совместимый endpoint (vLLM, LM Studio, gpt-oss)
#   ollama  — модель на самом стенде
# groq / gemini / ario требуют интернета.
LLM_PROVIDER=custom
CUSTOM_LLM_BASE_URL=http://<хост-с-моделью>:8000/v1
CUSTOM_LLM_MODEL=<имя-модели>
CUSTOM_LLM_API_KEY=

# --- Интеграция с Directum RX --------------------------------------------
# Нужны только для вызовов по document_id. Если RX присылает текст обращения
# в запросе, креды можно не заполнять.
RX_ODATA_URL=
RX_USER=
RX_PASSWORD=
"""


def run(cmd: list[str]) -> int:
    print("  $ " + " ".join(cmd))
    return subprocess.call(cmd)


def download_wheels(py_version: str, platform: str, clean: bool) -> bool:
    """Скачивает колёса под целевой ABI, не собирая ничего из исходников."""
    wheels = BUNDLE / "wheels"
    if clean and wheels.exists():
        shutil.rmtree(wheels)
    wheels.mkdir(parents=True, exist_ok=True)

    # --only-binary=:all: обязателен: без него pip притащит sdist, который на
    # стенде пришлось бы компилировать — а компилятора там нет.
    cmd = [
        sys.executable, "-m", "pip", "download",
        "-r", str(ROOT / "requirements.txt"),
        "-d", str(wheels),
        "--only-binary=:all:",
        "--python-version", py_version,
        "--platform", platform,
    ]
    code = run(cmd)
    if code != 0:
        print("\n  Скачивание колёс не удалось.")
        print("  Частая причина: у пакета нет колеса под целевую пару "
              f"(python {py_version}, {platform}).")
        return False

    # pip тянет зависимости по маркерам целевой версии, но саму requirements-строку
    # с маркером python_version мог отбросить — проверяем ключевые пакеты.
    names = {p.name.split("-")[0].lower().replace("_", "-") for p in wheels.glob("*.whl")}
    missing = sorted({"torch", "numpy", "scipy", "tokenizers", "safetensors",
                      "sentence-transformers", "fastapi", "uvicorn"} - names)
    if missing:
        print(f"\n  ВНИМАНИЕ: в колёсах нет {', '.join(missing)} — проверить requirements.txt")
        return False

    total_mb = sum(p.stat().st_size for p in wheels.glob("*.whl")) // (1024 * 1024)
    print(f"  колёс: {len(list(wheels.glob('*.whl')))}, {total_mb} МБ")
    return True


def copy_model(source: str) -> bool:
    """Складывает модель обычными файлами: каталог-кеш HF на стенде хрупок."""
    dest = BUNDLE / "models" / "multilingual-e5-base"
    src = Path(source) if source else None

    if src and src.is_dir():
        print(f"  копирую модель из {src}")
        dest.parent.mkdir(parents=True, exist_ok=True)
        if dest.exists():
            shutil.rmtree(dest)
        shutil.copytree(src, dest, ignore=shutil.ignore_patterns(".git*", "*.h5", "*.msgpack", "onnx*"))
    else:
        repo = source or "intfloat/multilingual-e5-base"
        print(f"  скачиваю {repo} с Hugging Face")
        try:
            from huggingface_hub import snapshot_download
        except ImportError:
            print("  нет huggingface_hub — поставить его или указать --model-dir")
            return False
        snapshot_download(
            repo_id=repo,
            local_dir=str(dest),
            # Веса в safetensors; форматы TF/Flax и onnx стенду не нужны.
            allow_patterns=["*.json", "*.txt", "*.model", "*.safetensors", "1_Pooling/*", "2_Normalize/*"],
        )

    required = ["config.json", "modules.json", "sentence_bert_config.json",
                "tokenizer.json", "tokenizer_config.json"]
    missing = [f for f in required if not (dest / f).is_file()]
    weights = list(dest.glob("model.safetensors")) or list(dest.glob("pytorch_model.bin"))
    if missing or not weights:
        print(f"  НЕПОЛНАЯ модель: не хватает {missing or 'весов'}")
        return False
    print(f"  модель готова: {len(list(dest.rglob('*')))} файлов, "
          f"веса {weights[0].stat().st_size // (1024 * 1024)} МБ")
    return True


def copy_vector_db(vdb_dir: Path) -> bool:
    """Готовая база экономит стенду 10-15 минут прогона модели по 2108 записям."""
    dest = BUNDLE / "vector_db_prebuilt"
    emb = vdb_dir / "embeddings.npy"
    meta = vdb_dir / "metadata.json"
    if not (emb.is_file() and meta.is_file()):
        print(f"  базы нет в {vdb_dir} — сначала python src/build_vectordb.py")
        return False
    dest.mkdir(parents=True, exist_ok=True)
    shutil.copy2(emb, dest / "embeddings.npy")
    shutil.copy2(meta, dest / "metadata.json")
    records = len(json.loads(meta.read_text(encoding="utf-8")))
    print(f"  база скопирована из {vdb_dir.name}: {records} записей, "
          f"{emb.stat().st_size // (1024 * 1024)} МБ")
    return True


def write_env_template(vdb_name: str, threshold: str) -> None:
    path = BUNDLE / "env.stand.example"
    path.write_text(
        ENV_TEMPLATE.format(
            model_rel="offline_bundle/models/multilingual-e5-base",
            vdb_rel=f"data/{vdb_name}",
            fallback_threshold=threshold,
        ),
        encoding="utf-8",
    )
    print(f"  шаблон окружения: {path.name}")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--python-version", default=f"{sys.version_info.major}.{sys.version_info.minor}",
                    help="минор Python НА СТЕНДЕ (например 3.11). По умолчанию — текущий")
    ap.add_argument("--platform", default="win_amd64", help="платформа колёс (по умолчанию win_amd64)")
    ap.add_argument("--model-dir", default="",
                    help="готовый каталог модели; если не задан — скачивается с HF")
    ap.add_argument("--vector-db", default="",
                    help="каталог векторной базы; по умолчанию берётся VECTOR_DB_DIR из .env")
    ap.add_argument("--skip-wheels", action="store_true", help="не перекачивать колёса")
    ap.add_argument("--keep-wheels", action="store_true", help="не чистить wheels перед скачиванием")
    args = ap.parse_args()

    print(f"\n  Сборка офлайн-комплекта в {BUNDLE}")
    print(f"  Целевой интерпретатор: Python {args.python_version} / {args.platform}\n")
    BUNDLE.mkdir(parents=True, exist_ok=True)

    sys.path.insert(0, str(ROOT / "src"))
    import config

    vdb = Path(args.vector_db) if args.vector_db else Path(config.VECTOR_DB_DIR)
    if not vdb.is_absolute():
        vdb = ROOT / vdb

    steps: list[tuple[str, bool]] = []
    if args.skip_wheels:
        print("  колёса: пропущено по флагу")
    else:
        steps.append(("колёса", download_wheels(args.python_version, args.platform,
                                                clean=not args.keep_wheels)))
    steps.append(("модель", copy_model(args.model_dir)))
    steps.append(("векторная база", copy_vector_db(vdb)))
    threshold = float(config.FULL_FALLBACK_SIM_THRESHOLD)
    if "adapt" in vdb.name and threshold > 0.2:
        # Иначе на стенде каждый запрос будет уходить в фолбэк по полному
        # классификатору: у адаптированной базы top1 около 0.26.
        print(f"  ВНИМАНИЕ: база {vdb.name} адаптированная, а порог фолбэка {threshold} "
              f"слишком высок — в шаблон записан 0.15")
        threshold = 0.15
    write_env_template(vdb.name, str(threshold))

    failed = [name for name, ok in steps if not ok]
    size_mb = sum(p.stat().st_size for p in BUNDLE.rglob("*") if p.is_file()) // (1024 * 1024)
    print(f"\n  Размер комплекта: {size_mb} МБ")
    if failed:
        print(f"  НЕ СОБРАНО: {', '.join(failed)}")
        return 1
    print("  Готово. Проверить: python scripts/check_offline.py --stage pre")
    return 0


if __name__ == "__main__":
    sys.exit(main())
