"""Сборка кеша Hugging Face для переноса на стенд без интернета.

Готовит каталог, который на стенде монтируется как `HF_CACHE_DIR` (в контейнер
он попадает в `/root/.cache/huggingface`). При этом `EMBEDDING_MODEL` остаётся
именем репозитория — путь менять не нужно, модель просто находится в кеше.

    python scripts/make_hf_cache.py --out D:/og-hf-cache

Скачивается только то, что нужно sentence-transformers: веса в safetensors,
токенизатор и конфиги. Форматы ONNX, OpenVINO, TensorFlow и дублирующий
pytorch_model.bin пропускаются — иначе каталог раздувается в разы.

В конце каталог проверяется загрузкой модели в отдельном процессе с
`HF_HUB_OFFLINE=1`: если чего-то не хватает, это видно здесь, а не на стенде.
"""

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_REPO = "intfloat/multilingual-e5-base"

# Всё, что нужно sentence-transformers, и ничего сверх того.
ALLOW = [
    "config.json",
    "config_sentence_transformers.json",
    "modules.json",
    "sentence_bert_config.json",
    "model.safetensors",
    "tokenizer.json",
    "tokenizer_config.json",
    "special_tokens_map.json",
    "sentencepiece.bpe.model",
    "1_Pooling/*",
    "2_Normalize/*",
]

README = """# Кеш модели эмбеддингов для стенда

Каталог целиком заменяет собой `~/.cache/huggingface`: внутри лежит модель
`{repo}` в формате кеша Hugging Face.

## Перенос

1. Скопировать этот каталог на стенд, например в `/opt/class-og/hf-cache`.
2. В `.env` проекта:

```
HF_CACHE_DIR=./hf-cache
HF_HUB_OFFLINE=1
TRANSFORMERS_OFFLINE=1
```

`EMBEDDING_MODEL` менять не нужно — модель находится по имени репозитория
прямо в кеше.

3. Пересоздать контейнер:

```
docker compose up -d --force-recreate classifier
```

`HF_HUB_OFFLINE=1` здесь важен: без него библиотека всё равно пойдёт в сеть
проверять обновления и упадёт, хотя файлы лежат рядом.

## Проверка

```
docker compose exec classifier python -c "import sys; sys.path.insert(0,'src'); import env_bootstrap; from sentence_transformers import SentenceTransformer; import config; m=SentenceTransformer(config.EMBEDDING_MODEL); print('OK', m.get_sentence_embedding_dimension())"
```

Ожидается `OK 768`.

## Что внутри

Модель: `{repo}`, ревизия `{revision}`.
Файлов: {files}, размер: {size_mb} МБ.
Собрано скриптом `scripts/make_hf_cache.py`.
"""

PROBE = (
    "import json, os, sys; sys.path.insert(0, 'src');"
    " import env_bootstrap;"
    " import huggingface_hub.constants as c;"
    " from sentence_transformers import SentenceTransformer;"
    " m = SentenceTransformer(os.environ['PROBE_MODEL']);"
    " print('PROBE ' + json.dumps({'offline': bool(c.HF_HUB_OFFLINE),"
    " 'dim': int(m.get_sentence_embedding_dimension())}))"
)


def dir_size_mb(path: Path) -> int:
    """Размер по реальным файлам.

    Снимок модели состоит из симлинков на блобы, поэтому обход "по файлам"
    считает веса дважды и завышает размер вдвое.
    """
    total = 0
    for f in path.rglob("*"):
        if f.is_symlink() or not f.is_file():
            continue
        total += f.stat().st_size
    return total // (1024 * 1024)


def count_files(path: Path) -> int:
    return sum(1 for f in path.rglob("*") if not f.is_symlink() and f.is_file())


def verify_offline(out: Path, repo: str) -> bool:
    """Загрузка модели с запретом сети — в отдельном процессе.

    Отдельный процесс обязателен: `huggingface_hub` фиксирует офлайн-флаг при
    импорте, а в текущем процессе библиотека уже загружена.
    """
    env = dict(os.environ)
    env["HF_HOME"] = str(out)
    env["HF_HUB_OFFLINE"] = "1"
    env["TRANSFORMERS_OFFLINE"] = "1"
    env["PROBE_MODEL"] = repo
    env.pop("HF_ENDPOINT", None)

    proc = subprocess.run([sys.executable, "-c", PROBE], cwd=str(ROOT), env=env,
                          capture_output=True, text=True, encoding="utf-8", errors="replace")
    line = next((l for l in proc.stdout.splitlines() if l.startswith("PROBE ")), "")
    if not line:
        tail = (proc.stderr or proc.stdout).strip().splitlines()[-4:]
        print("  ПРОВЕРКА НЕ ПРОШЛА:")
        for row in tail:
            print(f"    {row}")
        return False

    data = json.loads(line[len("PROBE "):])
    print(f"  проверка офлайн-загрузки: сеть запрещена={data['offline']}, dim={data['dim']}")
    return data["offline"] and data["dim"] == 768


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", required=True, help="каталог для кеша (он же HF_CACHE_DIR на стенде)")
    ap.add_argument("--repo", default=DEFAULT_REPO, help=f"репозиторий модели (по умолчанию {DEFAULT_REPO})")
    args = ap.parse_args()

    out = Path(args.out).resolve()
    hub = out / "hub"
    hub.mkdir(parents=True, exist_ok=True)

    print(f"\n  Кеш: {out}")
    print(f"  Модель: {args.repo}\n")

    # Скачиваем в структуру кеша, а не плоским каталогом: на стенде она
    # монтируется как ~/.cache/huggingface, и имя репозитория продолжает работать.
    from huggingface_hub import snapshot_download
    path = snapshot_download(repo_id=args.repo, cache_dir=str(hub), allow_patterns=ALLOW)
    revision = Path(path).name

    files = count_files(out)
    size_mb = dir_size_mb(out)
    print(f"  скачано: {files} файлов, {size_mb} МБ, ревизия {revision[:12]}")

    ok = verify_offline(out, args.repo)

    (out / "README.md").write_text(
        README.format(repo=args.repo, revision=revision, files=files, size_mb=size_mb),
        encoding="utf-8")
    print(f"  инструкция: {out / 'README.md'}")

    if not ok:
        print("\n  Кеш собран, но офлайн-загрузка не удалась — на стенд не переносить.")
        return 1
    print("\n  Готово: кеш проверен, модель грузится без сети.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
