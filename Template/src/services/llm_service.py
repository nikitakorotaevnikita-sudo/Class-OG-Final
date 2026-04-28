"""Сервис для взаимодействия с LLM через OpenAI Python SDK.

TEMPLATE: Используй как есть. Не нужно менять, если не требуется
специфичная логика (кастомные параметры, max_tokens и т.д.).
"""

from typing import AsyncGenerator
from openai import AsyncOpenAI, APIStatusError
from src.config import get_openai_api_key, get_openai_model, get_openai_server


def _create_client(api_key: str | None = None, base_url: str | None = None) -> AsyncOpenAI:
    """Создаёт асинхронный клиент OpenAI.

    Args:
        api_key: API-ключ (если None — из .env).
        base_url: Кастомный base URL (если None — из .env).
    """
    resolved_key = api_key or get_openai_api_key()
    resolved_url = base_url if base_url is not None else get_openai_server()

    kwargs = {"api_key": resolved_key or "dummy"}
    if resolved_url:
        kwargs["base_url"] = resolved_url

    return AsyncOpenAI(**kwargs)


async def stream_completion(
    messages: list[dict],
    model: str | None = None,
    api_key: str | None = None,
    base_url: str | None = None,
) -> AsyncGenerator[str, None]:
    """
    Генерирует текст через OpenAI API с потоковой передачей (streaming).

    Args:
        messages: Список сообщений в формате [{role, content}].
        model: Название модели (если None — берётся из конфигурации).
        api_key: API-ключ для переопределения (если None — из .env).
        base_url: Base URL для переопределения (если None — из .env).

    Yields:
        str: Фрагменты текста ответа по мере генерации.

    Raises:
        ValueError: Если превышен лимит контекста модели.
        RuntimeError: При других ошибках API.
    """
    client = _create_client(api_key=api_key, base_url=base_url)
    model_name = model or get_openai_model()

    try:
        stream = await client.chat.completions.create(
            model=model_name,
            messages=messages,
            stream=True,
        )
        async for chunk in stream:
            if chunk.choices and chunk.choices[0].delta.content:
                yield chunk.choices[0].delta.content

    except APIStatusError as e:
        if e.status_code == 400 and "context_length_exceeded" in str(e.body).lower():
            raise ValueError(
                "Данные слишком большие для обработки. "
                "Попробуйте загрузить меньше данных."
            ) from e
        raise RuntimeError(f"Ошибка OpenAI API: {e.message}") from e
    except Exception as e:
        if "context_length_exceeded" in str(e).lower() or "maximum context" in str(e).lower():
            raise ValueError(
                "Данные слишком большие для обработки. "
                "Попробуйте загрузить меньше данных."
            ) from e
        raise RuntimeError(f"Ошибка при обращении к LLM: {e}") from e
