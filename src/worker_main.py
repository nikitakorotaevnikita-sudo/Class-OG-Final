"""Процесс воркера: singleton ClassifierAgent с горячей перезагрузкой.

Celery запускается как `celery -A queueing.celery_app worker ...` с `src`
в PYTHONPATH. Агент создаётся лениво (get_agent); прогрев по сигналам
Celery подключает queueing.warmup (импортируется вместе с queueing.tasks и
тянет этот модуль лишь в процессе, который выполняет задачи).
"""
import logging
import threading
from typing import Optional

from dotenv import dotenv_values

import config
import settings_store
from classifier_agent import ClassifierAgent
from storage import get_storage

log = logging.getLogger(__name__)

_lock = threading.RLock()
_agent: Optional[ClassifierAgent] = None


def embed_pending_annotations(agent) -> int:
    """Досчитать векторы аннотаций для текущей версии модели агента."""
    classifier = get_storage().classifier
    pending = classifier.pending_annotations(agent.model_version)
    for vector_id, text in pending:
        classifier.set_embedding(vector_id, agent.embed_passage(text))
    log.info("Аннотации: досчитано векторов %d (модель v%s)", len(pending), agent.model_version)
    return len(pending)


def _current_env_mtime() -> Optional[int]:
    try:
        return settings_store.ENV_PATH.stat().st_mtime_ns
    except OSError:
        return None


# Базовая отметка берётся при импорте, до создания агента: правки .env
# во время долгой загрузки модели не теряются (config читался при старте).
_env_mtime: Optional[int] = _current_env_mtime()


def refresh_settings_if_env_changed() -> bool:
    """Если .env изменился — применить изменённые настройки в этом процессе.

    Отметка mtime сдвигается только после успешного применения, поэтому
    при ошибке следующая попытка повторится.
    """
    global _env_mtime
    mtime = _current_env_mtime()
    with _lock:
        if mtime is None or mtime == _env_mtime:
            return False
        values = dotenv_values(settings_store.ENV_PATH)
        updates = {}
        for key, value in values.items():
            if key in settings_store.FIELDS_BY_KEY and value is not None                     and str(getattr(config, key, "")) != value:
                updates[key] = value
        if updates:
            settings_store.apply_runtime(updates, _agent)
            log.info("Настройки из .env применены: %s", ", ".join(sorted(updates)))
        _env_mtime = mtime
        return True


def get_agent() -> ClassifierAgent:
    global _agent
    with _lock:
        if _agent is None:
            _agent = ClassifierAgent(get_storage())
            try:
                embed_pending_annotations(_agent)
            except Exception:
                log.exception("Не удалось досчитать аннотации")
        else:
            _agent.reload_model_if_changed()
        try:
            refresh_settings_if_env_changed()
        except Exception:
            log.exception("Обновление настроек из .env не удалось; повтор при следующем вызове")
        return _agent
