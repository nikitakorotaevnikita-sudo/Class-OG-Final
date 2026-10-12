"""Как подавать текст в модель эмбеддингов — зависит от её семейства.

Раньше правило «префикс только для e5» было продублировано в двух местах: при
сборке базы и при кодировании запроса. Разъехаться им нельзя — запрос и
документы должны быть закодированы в одном и том же соглашении, иначе поиск
сравнивает несравнимое. Поэтому правило живёт здесь одно.
"""

import os

# Qwen3-Embedding обучалась с инструкцией задачи у запроса; документы — голым
# текстом. Формат строки — из карточки модели. Инструкцию модель рекомендует
# писать по-английски, даже когда тексты на другом языке.
QWEN3_TASK = (
    "Given a Russian citizen's appeal to a government body, retrieve the "
    "category of the national classifier of citizens' appeals that matches "
    "the issue raised in it"
)


def _is_e5(model_name: str) -> bool:
    return "e5" in (model_name or "").lower()


def _is_qwen3_embedding(model_name: str) -> bool:
    name = (model_name or "").lower()
    return "qwen3" in name and "embedding" in name


def format_query(text: str, model_name: str) -> str:
    if _is_e5(model_name):
        return f"query: {text}"
    if _is_qwen3_embedding(model_name):
        return f"Instruct: {QWEN3_TASK}\nQuery:{text}"
    return text


def format_passage(text: str, model_name: str) -> str:
    if _is_e5(model_name):
        return f"passage: {text}"
    return text


# Предел длины входа в токенах. У e5 он 512, у Qwen3-Embedding — 32 тысячи, и
# обращение на 5000 символов кодировалось целиком: поиск на реальных
# обращениях шёл 5.9 с на запрос против 1.05 у e5. Суть обращения почти всегда
# в начале, а записи классификатора короче 400 токенов — базу предел не задевает.
MAX_SEQ_LENGTH = int(os.getenv("EMBEDDING_MAX_SEQ_LENGTH", "512"))

# Класс модели импортируется лениво: HF-библиотеки должны подниматься после
# env_bootstrap, иначе настройки офлайн-режима из .env не подхватятся.
# Имя на уровне модуля — чтобы тесты могли подменить загрузку.
SentenceTransformer = None


def load_embedder(model_name: str):
    """Загрузить модель эмбеддингов в типе весов, подходящем для устройства.

    Qwen3-Embedding хранится в bfloat16, и transformers 5.x так её и грузит.
    На CPU без аппаратной поддержки bf16 (Ryzen 7430U, Zen 3) это эмулируется:
    сборка базы на такой машине шла почти два часа, 0.2 записи/с против 1.2
    в float32. На видеокарте bf16 быстрый и экономит память — там тип не трогаем.
    """
    import torch

    cls = SentenceTransformer
    if cls is None:
        from sentence_transformers import SentenceTransformer as cls

    if torch.cuda.is_available():
        model = cls(model_name)
    else:
        model = cls(model_name, model_kwargs={"dtype": torch.float32})

    if getattr(model, "max_seq_length", 0) and model.max_seq_length > MAX_SEQ_LENGTH:
        model.max_seq_length = MAX_SEQ_LENGTH
    return model
