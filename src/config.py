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
RETRIEVAL_POOL_SIZE: int = int(os.getenv("RETRIEVAL_POOL_SIZE", "50"))
LEXICAL_POOL_SIZE: int = int(os.getenv("LEXICAL_POOL_SIZE", "30"))
TOP_K_RESULT:     int = 3
MIN_CONFIDENCE:  float = float(os.getenv("MIN_CONFIDENCE", "0.65"))

# ── Heuristic reranker (merge dense+lexical + branch/parent бусты) ─────────────
# По замерам на ii25_test реранкер выталкивает золотой код из top-10
# (dense recall@10 41.1% → 10.7%). При false LLM получает чистый dense top-K.
ENABLE_HEURISTIC_RERANKER: bool = os.getenv("ENABLE_HEURISTIC_RERANKER", "true").lower() == "true"

# ── CrossEncoder reranker (опциональный, после heuristic rerank) ────────────────
ENABLE_CROSS_ENCODER_RERANKER: bool = os.getenv("ENABLE_CROSS_ENCODER_RERANKER", "false").lower() == "true"
CROSS_ENCODER_MODEL: str = os.getenv("CROSS_ENCODER_MODEL", "BAAI/bge-reranker-base")
# Сколько кандидатов передавать в CE (пересортировать). Если 0/None — все.
CE_RERANK_TOP_N: int = int(os.getenv("CE_RERANK_TOP_N", "30"))
# Веса при смешивании: финальный_score = (1-w) * heuristic + w * ce_score
CE_BLEND_WEIGHT: float = float(os.getenv("CE_BLEND_WEIGHT", "0.7"))

# ── LLM Query Expansion (опциональный, перед retrieval) ─────────────────────────
# Если включён — для каждого сегмента делается LLM-вызов: переформулировать вопрос
# в стиле классификаторных терминов. Embedding ищется по [original + expansion].
ENABLE_QUERY_EXPANSION: bool = os.getenv("ENABLE_QUERY_EXPANSION", "false").lower() == "true"

# ── Hierarchy-aware reranking ───────────────────────────────────────────────────
# Branch agreement: буст кандидата если другие кандидаты из той же L2-ветки.
HIERARCHY_BRANCH_WEIGHT: float = float(os.getenv("HIERARCHY_BRANCH_WEIGHT", "0.05"))
# Parent similarity: буст кандидата если его родительский код тоже в пуле.
HIERARCHY_PARENT_WEIGHT: float = float(os.getenv("HIERARCHY_PARENT_WEIGHT", "0.04"))
# L1-pruning: если 1-2 раздела доминируют — отсечь кандидатов из других. Default OFF (агрессивно).
ENABLE_HIERARCHY_PRUNING: bool = os.getenv("ENABLE_HIERARCHY_PRUNING", "false").lower() == "true"
HIERARCHY_PRUNE_THRESHOLD: float = float(os.getenv("HIERARCHY_PRUNE_THRESHOLD", "0.70"))

# ── Section routing (coarse-to-fine с явным выбором тематического раздела) ──────
# Если включён — перед retrieval делается отдельный LLM-вызов: «Какие разделы (L1)
# и тематики (L2) подходят этому обращению?». Кандидаты фильтруются по выбранным.
ENABLE_SECTION_ROUTING: bool = os.getenv("ENABLE_SECTION_ROUTING", "false").lower() == "true"
# Максимальное число L2-тематик которые LLM может выбрать (3 = широко, 1 = жёсткое сужение)
SECTION_ROUTING_MAX_TOPICS: int = int(os.getenv("SECTION_ROUTING_MAX_TOPICS", "3"))

# ── L3-routing (опц., после L2-routing) — сужает по L3-теме ─────────────────────
ENABLE_L3_ROUTING: bool = os.getenv("ENABLE_L3_ROUTING", "false").lower() == "true"
L3_ROUTING_MAX_THEMES: int = int(os.getenv("L3_ROUTING_MAX_THEMES", "3"))

# ── Multi-Query expansion (опц., перед retrieval) ───────────────────────────────
# LLM генерирует 3-5 разных формулировок обращения, для каждой делается отдельный
# retrieval, потом union пулов. Цена: +1 LLM-вызов на сегмент.
ENABLE_MULTI_QUERY_EXPAND: bool = os.getenv("ENABLE_MULTI_QUERY_EXPAND", "false").lower() == "true"
MQE_N_VARIANTS: int = int(os.getenv("MQE_N_VARIANTS", "4"))

# ── Allowed codes whitelist (опц.) — ограничение пула «горячими» кодами ────────
# Если включено и список загружается, retrieval/rerank возвращают только коды
# из этого списка (плюс их L4-родителей если включён soft mode). По данным НОР
# эти 69 кодов покрывают 60% реальных обращений.
ENABLE_ALLOWED_CODES: bool = os.getenv("ENABLE_ALLOWED_CODES", "false").lower() == "true"
ALLOWED_CODES_PATH: str = os.getenv(
    "ALLOWED_CODES_PATH",
    str(Path(__file__).parent.parent / "data" / "allowed_codes_top69.json"),
)

# ── Learning-to-Rank reranker (опциональный) ────────────────────────────────────
# Sklearn LogReg обучен на ii25_train: предсказывает вероятность что candidate -
# правильный код. Score добавляется в combined_score reranker'а.
ENABLE_LTR_RERANKER: bool = os.getenv("ENABLE_LTR_RERANKER", "false").lower() == "true"
LTR_MODEL_PATH: str = os.getenv("LTR_MODEL_PATH", str(Path(__file__).parent.parent / "models" / "ltr_v1.json"))
# Вес LtR-score в финальном combined_score
LTR_WEIGHT: float = float(os.getenv("LTR_WEIGHT", "0.30"))

# ── Embedding Adapter (Linear 768→768, обученный на ii25_train с InfoNCE) ──────
# Применяется к query embedding после e5. Vector DB должна быть пересобрана
# через тот же адаптер (data/vector_db_adapted).
ENABLE_EMBEDDING_ADAPTER: bool = os.getenv("ENABLE_EMBEDDING_ADAPTER", "false").lower() == "true"
ADAPTER_PATH: str = os.getenv("ADAPTER_PATH", str(Path(__file__).parent.parent / "models" / "adapter_v1.npz"))

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
