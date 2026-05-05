"""
Конфигурация агента классификации обращений граждан.
Все секреты читаются из файла .env (см. .env.example).
"""

import os
from pathlib import Path
from dotenv import load_dotenv

# Загружаем .env из корня проекта
load_dotenv(Path(__file__).parent.parent / ".env")

# ── LLM Provider ────────────────────────────────────────────────────────────────
LLM_PROVIDER: str = os.getenv("LLM_PROVIDER", "groq")  # "groq" | "gemini" | "ollama" | "ario"

# ── Groq API ───────────────────────────────────────────────────────────────────
GROQ_API_KEY: str = os.environ.get("GROQ_API_KEY", "")

# ── Google Gemini API ─────────────────────────────────────────────────────────
GEMINI_API_KEY: str = os.environ.get("GEMINI_API_KEY", "")
# llama-3.3-70b-versatile  — лучший русский язык (рекомендуется)
# llama-3.1-8b-instruct   — быстрее, меньше точность
GROQ_MODEL: str = os.getenv("GROQ_MODEL", "llama-3.3-70b-versatile")

# ── Ollama API (локально) ───────────────────────────────────────────────────────
OLLAMA_MODEL: str = os.getenv("OLLAMA_MODEL", "qwen2.5-14b")
OLLAMA_BASE_URL: str = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434/v1")

# ── Ario API (OpenAI-compatible) ───────────────────────────────────────────────
ARIO_API_KEY: str = os.environ.get("ARIO_API_KEY", "")
ARIO_BASE_URL: str = os.getenv("ARIO_BASE_URL", "https://gpt.ario.directum360.ru/v1")
ARIO_MODEL: str = os.getenv("ARIO_MODEL", "Qwen/Qwen3-32B-AWQ")

# ── Векторная база (numpy) ─────────────────────────────────────────────────────
_data_dir = Path(__file__).parent.parent / "data"
VECTOR_DB_DIR: str = os.getenv(
    "VECTOR_DB_DIR",
    str(_data_dir / "vector_db")
)

# ── Модель эмбеддингов ─────────────────────────────────────────────────────────
EMBEDDING_MODEL: str = os.getenv(
    "EMBEDDING_MODEL", "intfloat/multilingual-e5-base"
)

# ── Параметры поиска ───────────────────────────────────────────────────────────
TOP_K_CANDIDATES: int = int(os.getenv("TOP_K_CANDIDATES", "10"))
TOP_K_RESULT:     int = 3
MIN_CONFIDENCE:  float = float(os.getenv("MIN_CONFIDENCE", "0.65"))

# ── Пути к файлам классификатора ───────────────────────────────────────────────
CLASSIFIER_FLAT_PATH: str = os.getenv(
    "CLASSIFIER_FLAT_PATH", str(_data_dir / "classifier_flat.json")
)
CLASSIFIER_HIERARCHY_PATH: str = os.getenv(
    "CLASSIFIER_HIERARCHY_PATH", str(_data_dir / "classifier_hierarchy.json")
)

# ── API-сервер ─────────────────────────────────────────────────────────────────
API_HOST: str = os.getenv("API_HOST", "0.0.0.0")
API_PORT: int = int(os.getenv("API_PORT", "8000"))

# ── Параметры агента ───────────────────────────────────────────────────────────
MAX_APPEAL_LENGTH: int = 5000   # символов

# ── Дообучение ─────────────────────────────────────────────────────────────────
# Порог верифицированных записей для запуска fine-tuning
FINETUNE_THRESHOLD: int = int(os.getenv("FINETUNE_THRESHOLD", "50"))
# Папка для дообученных моделей
MODELS_DIR: str = os.getenv("MODELS_DIR", str(Path(__file__).parent.parent / "models"))

# ── Бэк-офис (Basic Auth) ───────────────────────────────────────────────────────
BACKOFFICE_USER:     str = os.getenv("BACKOFFICE_USER", "admin")
BACKOFFICE_PASSWORD: str = os.getenv("BACKOFFICE_PASSWORD", "password")
