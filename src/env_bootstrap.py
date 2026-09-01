"""Читает `.env` раньше, чем импортируется любая библиотека Hugging Face.

`huggingface_hub` фиксирует `HF_HUB_OFFLINE` и `HF_ENDPOINT` один раз — в момент
своего импорта. Модули проекта импортируют `sentence_transformers` раньше, чем
`config` (который и вызывает `load_dotenv`), поэтому офлайн-флаги из `.env`
до библиотеки не доходили: на стенде без интернета это превращалось в попытку
сетевого запроса и `FileMetadataError` вместо загрузки локальной модели.

Модуль импортируется первым — до `torch`, `transformers`, `sentence_transformers`:

    import env_bootstrap  # noqa: F401  — .env до HF-библиотек
    from sentence_transformers import SentenceTransformer

Идемпотентен: повторный импорт ничего не меняет, `config.load_dotenv` тоже
можно вызывать как обычно.
"""

import os
from pathlib import Path

from dotenv import load_dotenv

ENV_PATH = Path(__file__).parent.parent / ".env"

# override=False: переменные, заданные в окружении процесса (docker -e, set в
# батнике, окружение systemd), приоритетнее файла.
load_dotenv(ENV_PATH, override=False)

_TRUE = {"1", "true", "yes", "on"}


def is_offline() -> bool:
    """Стенд объявлен офлайновым (`HF_HUB_OFFLINE=1` в окружении или `.env`)."""
    return os.getenv("HF_HUB_OFFLINE", "").strip().lower() in _TRUE


if is_offline():
    # Ни одна HF-библиотека не должна пытаться выйти в сеть: любой промах по
    # локальному кешу должен падать сразу и внятно, а не висеть на таймауте.
    os.environ["HF_HUB_OFFLINE"] = "1"
    os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")
    os.environ.setdefault("HF_DATASETS_OFFLINE", "1")
    os.environ.setdefault("HF_HUB_DISABLE_TELEMETRY", "1")
    # Зеркало HF на офлайн-стенде — лишний недостижимый хост в сообщениях об
    # ошибках, только запутывает диагностику.
    os.environ.pop("HF_ENDPOINT", None)

# В онлайне адрес не навязывается: библиотека сама идёт на huggingface.co.
# Раньше здесь подставлялось зеркало hf-mirror.com — оно помогало в одной
# конкретной корпоративной сети, но на стенде с обычным интернетом ломало
# запуск: до huggingface.co доступ есть, до зеркала нет. Кому зеркало нужно —
# задаёт HF_ENDPOINT в .env явно.
