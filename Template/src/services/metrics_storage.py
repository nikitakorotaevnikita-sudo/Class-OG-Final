"""Хранилище метрик использования (SQLite).

TEMPLATE: Используй как есть. Адаптируй get_metrics() если в проекте
другое количество кейсов или другие критерии.
"""

import os
import sqlite3
from datetime import datetime, timezone
from src.config import get_data_dir


def _get_db_path() -> str:
    """Путь к файлу базы данных."""
    return os.path.join(get_data_dir(), "metrics.db")


def init_db():
    """Инициализирует таблицы SQLite при первом запуске."""
    db_path = _get_db_path()
    os.makedirs(os.path.dirname(db_path), exist_ok=True)
    conn = sqlite3.connect(db_path)
    try:
        cursor = conn.cursor()
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS requests (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ip TEXT NOT NULL,
                endpoint TEXT NOT NULL,
                case_id INTEGER,
                timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
            )
        """)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS feedback (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ip TEXT NOT NULL,
                case_id INTEGER NOT NULL,
                session_id TEXT NOT NULL,
                item_id INTEGER,
                vote INTEGER NOT NULL,
                timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(case_id, session_id, item_id)
            )
        """)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS chat_feedback (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id TEXT NOT NULL,
                vote INTEGER NOT NULL,
                user_message TEXT NOT NULL,
                context_type TEXT NOT NULL,
                context_name TEXT NOT NULL,
                summary TEXT,
                timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
            )
        """)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS custom_prompts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                prompt_text TEXT NOT NULL,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP
            )
        """)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS system_prompts (
                id INTEGER PRIMARY KEY,
                prompt_text TEXT NOT NULL
            )
        """)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS llm_models (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                base_url TEXT,
                token TEXT,
                display_name TEXT,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP
            )
        """)
        # Миграции: добавить колонки если таблицы уже существовали без них
        for migration in [
            "ALTER TABLE feedback ADD COLUMN model_label TEXT",
            "ALTER TABLE feedback ADD COLUMN item_id INTEGER",
            "ALTER TABLE chat_feedback ADD COLUMN model_label TEXT",
        ]:
            try:
                cursor.execute(migration)
            except Exception:
                pass
        conn.commit()
    finally:
        conn.close()


# ===== УПРАВЛЕНИЕ LLM-МОДЕЛЯМИ =====

def get_llm_models() -> list[dict]:
    """Возвращает список пользовательских LLM-моделей (без токена)."""
    conn = sqlite3.connect(_get_db_path())
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            "SELECT id, name, base_url, display_name, "
            "(token IS NOT NULL AND token != '') as has_token FROM llm_models ORDER BY id"
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def get_llm_model_by_id(model_id: int) -> dict | None:
    """Возвращает полную запись модели включая токен или None если не найдена."""
    conn = sqlite3.connect(_get_db_path())
    conn.row_factory = sqlite3.Row
    try:
        row = conn.execute(
            "SELECT id, name, base_url, token, display_name FROM llm_models WHERE id = ?",
            (model_id,)
        ).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def create_llm_model(name: str, base_url: str | None, token: str | None, display_name: str | None) -> int:
    """Создаёт новую пользовательскую LLM-модель. Возвращает id."""
    conn = sqlite3.connect(_get_db_path())
    try:
        cursor = conn.execute(
            "INSERT INTO llm_models (name, base_url, token, display_name) VALUES (?, ?, ?, ?)",
            (name, base_url or None, token or None, display_name or None),
        )
        conn.commit()
        return cursor.lastrowid
    finally:
        conn.close()


def update_llm_model(model_id: int, name: str, base_url: str | None, token, display_name: str | None) -> bool:
    """
    Обновляет запись LLM-модели.
    token=None — не менять токен; пустая строка — очистить.
    """
    conn = sqlite3.connect(_get_db_path())
    try:
        if token is None:
            cursor = conn.execute(
                "UPDATE llm_models SET name=?, base_url=?, display_name=? WHERE id=?",
                (name, base_url or None, display_name or None, model_id),
            )
        else:
            cursor = conn.execute(
                "UPDATE llm_models SET name=?, base_url=?, token=?, display_name=? WHERE id=?",
                (name, base_url or None, token or None, display_name or None, model_id),
            )
        conn.commit()
        return cursor.rowcount > 0
    finally:
        conn.close()


def delete_llm_model(model_id: int) -> bool:
    """Удаляет пользовательскую LLM-модель. Возвращает True если найдена."""
    conn = sqlite3.connect(_get_db_path())
    try:
        cursor = conn.execute("DELETE FROM llm_models WHERE id = ?", (model_id,))
        conn.commit()
        return cursor.rowcount > 0
    finally:
        conn.close()


def log_request(ip: str, endpoint: str, case_id: int | None = None):
    """Логирует входящий запрос."""
    db_path = _get_db_path()
    conn = sqlite3.connect(db_path)
    try:
        conn.execute("""
            INSERT INTO requests (ip, endpoint, case_id, timestamp)
            VALUES (?, ?, ?, ?)
        """, (ip, endpoint, case_id, datetime.now(timezone.utc).isoformat()))
        conn.commit()
    finally:
        conn.close()


def save_feedback(
    ip: str,
    case_id: int,
    session_id: str,
    vote: int,
    model_label: str | None = None,
    item_id: int | None = None,
):
    """Сохраняет оценку пользователя (UPSERT по case_id + session_id + item_id)."""
    db_path = _get_db_path()
    conn = sqlite3.connect(db_path)
    try:
        conn.execute("""
            INSERT INTO feedback (ip, case_id, session_id, item_id, vote, model_label, timestamp)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(case_id, session_id, item_id) DO UPDATE SET vote=excluded.vote, model_label=excluded.model_label, timestamp=excluded.timestamp
        """, (ip, case_id, session_id, item_id, vote, model_label, datetime.now(timezone.utc).isoformat()))
        conn.commit()
    finally:
        conn.close()


def save_chat_feedback(
    session_id: str,
    vote: int,
    user_message: str,
    context_type: str,
    context_name: str,
    model_label: str | None = None,
) -> int:
    """Сохраняет оценку чат-ответа. Возвращает id новой записи."""
    db_path = _get_db_path()
    conn = sqlite3.connect(db_path)
    try:
        cursor = conn.execute("""
            INSERT INTO chat_feedback (session_id, vote, user_message, context_type, context_name, model_label, timestamp)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (session_id, vote, user_message, context_type, context_name, model_label,
               datetime.now(timezone.utc).isoformat()))
        conn.commit()
        return cursor.lastrowid
    finally:
        conn.close()


def update_chat_feedback_summary(record_id: int, summary: str):
    """Обновляет поле summary для записи чат-фидбека."""
    db_path = _get_db_path()
    conn = sqlite3.connect(db_path)
    try:
        conn.execute("UPDATE chat_feedback SET summary = ? WHERE id = ?", (summary, record_id))
        conn.commit()
    finally:
        conn.close()


def get_chat_feedback(limit: int = 50) -> list[dict]:
    """Возвращает последние записи чат-фидбека для бэкофиса."""
    db_path = _get_db_path()
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT id, session_id, vote, user_message, context_type, context_name, summary, model_label, timestamp
            FROM chat_feedback
            ORDER BY timestamp DESC
            LIMIT ?
        """, (limit,))
        return [dict(r) for r in cursor.fetchall()]
    finally:
        conn.close()


def get_metrics() -> dict:
    """
    Возвращает агрегированные метрики для бэк-офиса.

    Returns:
        dict: total_requests, unique_ips, ip_stats, case_stats, timeline, total_positive_pct
    """
    db_path = _get_db_path()
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        cursor = conn.cursor()

        # Общая статистика
        cursor.execute("SELECT COUNT(*) as cnt, COUNT(DISTINCT ip) as uniq FROM requests")
        row = cursor.fetchone()
        total_requests = row["cnt"] if row else 0
        unique_ips = row["uniq"] if row else 0

        # Статистика по IP
        cursor.execute("""
            SELECT ip, COUNT(*) as cnt FROM requests
            GROUP BY ip ORDER BY cnt DESC LIMIT 20
        """)
        ip_stats = [{"ip": r["ip"], "count": r["cnt"]} for r in cursor.fetchall()]

        # TEMPLATE: Адаптируй range(1, N+1) под количество кейсов проекта
        case_stats = []
        for case_id in range(1, 8):
            cursor.execute("SELECT COUNT(*) as cnt FROM requests WHERE case_id = ?", (case_id,))
            req_count = cursor.fetchone()["cnt"]

            cursor.execute(
                "SELECT SUM(CASE WHEN vote = 1 THEN 1 ELSE 0 END) as pos, "
                "SUM(CASE WHEN vote = -1 THEN 1 ELSE 0 END) as neg "
                "FROM feedback WHERE case_id = ?", (case_id,)
            )
            fb = cursor.fetchone()
            positive = fb["pos"] or 0
            negative = fb["neg"] or 0
            total_votes = positive + negative
            pct = round(positive / total_votes * 100, 1) if total_votes > 0 else None

            cursor.execute(
                "SELECT COALESCE(model_label, '—') as ml, "
                "SUM(CASE WHEN vote=1 THEN 1 ELSE 0 END) as pos, "
                "SUM(CASE WHEN vote=-1 THEN 1 ELSE 0 END) as neg "
                "FROM feedback WHERE case_id = ? GROUP BY ml ORDER BY ml",
                (case_id,)
            )
            models_breakdown = [
                {"model": r["ml"], "positive": r["pos"] or 0, "negative": r["neg"] or 0}
                for r in cursor.fetchall()
            ]

            case_stats.append({
                "case_id": case_id,
                "requests": req_count,
                "positive": positive,
                "negative": negative,
                "pct_positive": pct,
                "models_breakdown": models_breakdown,
            })

        # График по дням (последние 30 дней)
        cursor.execute("""
            SELECT DATE(timestamp) as date, COUNT(*) as cnt
            FROM requests
            WHERE timestamp >= DATE('now', '-30 days')
            GROUP BY DATE(timestamp)
            ORDER BY date
        """)
        timeline = [{"date": r["date"], "count": r["cnt"]} for r in cursor.fetchall()]

        # Общий % положительных
        cursor.execute(
            "SELECT SUM(CASE WHEN vote = 1 THEN 1 ELSE 0 END) as pos, COUNT(*) as total FROM feedback"
        )
        fb_total = cursor.fetchone()
        pos_total = fb_total["pos"] or 0
        votes_total = fb_total["total"] or 0
        total_positive_pct = round(pos_total / votes_total * 100, 1) if votes_total > 0 else None

        # Чат-фидбек: общий % положительных
        cursor.execute(
            "SELECT SUM(CASE WHEN vote = 1 THEN 1 ELSE 0 END) as pos, COUNT(*) as total FROM chat_feedback"
        )
        cf_total = cursor.fetchone()
        cf_pos = cf_total["pos"] or 0
        cf_votes = cf_total["total"] or 0
        chat_positive_pct = round(cf_pos / cf_votes * 100, 1) if cf_votes > 0 else None

        return {
            "total_requests": total_requests,
            "unique_ips": unique_ips,
            "ip_stats": ip_stats,
            "case_stats": case_stats,
            "timeline": timeline,
            "total_positive_pct": total_positive_pct,
            "chat_feedback_count": cf_votes,
            "chat_positive_pct": chat_positive_pct,
        }
    finally:
        conn.close()


# ===== УПРАВЛЕНИЕ ПОЛЬЗОВАТЕЛЬСКИМИ ПРОМПТАМИ =====

def get_custom_prompts() -> list[dict]:
    """Возвращает список пользовательских промптов."""
    conn = sqlite3.connect(_get_db_path())
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            "SELECT id, name, prompt_text, created_at FROM custom_prompts ORDER BY created_at"
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def create_custom_prompt(name: str, prompt_text: str) -> dict:
    """Создаёт пользовательский промпт. Возвращает новую запись."""
    conn = sqlite3.connect(_get_db_path())
    try:
        cursor = conn.execute(
            "INSERT INTO custom_prompts (name, prompt_text) VALUES (?, ?)",
            (name, prompt_text),
        )
        conn.commit()
        row_id = cursor.lastrowid
        return {"id": row_id, "name": name, "prompt_text": prompt_text}
    finally:
        conn.close()


def update_custom_prompt(prompt_id: int, name: str, prompt_text: str) -> bool:
    """Обновляет пользовательский промпт. Возвращает True если запись найдена."""
    conn = sqlite3.connect(_get_db_path())
    try:
        cursor = conn.execute(
            "UPDATE custom_prompts SET name = ?, prompt_text = ? WHERE id = ?",
            (name, prompt_text, prompt_id),
        )
        conn.commit()
        return cursor.rowcount > 0
    finally:
        conn.close()


def delete_custom_prompt(prompt_id: int) -> bool:
    """Удаляет пользовательский промпт. Возвращает True если запись найдена."""
    conn = sqlite3.connect(_get_db_path())
    try:
        cursor = conn.execute("DELETE FROM custom_prompts WHERE id = ?", (prompt_id,))
        conn.commit()
        return cursor.rowcount > 0
    finally:
        conn.close()


# ===== УПРАВЛЕНИЕ СИСТЕМНЫМИ ПРОМПТАМИ =====

def get_system_prompt(prompt_id: int) -> str | None:
    """Возвращает переопределённый системный промпт или None если не задан."""
    conn = sqlite3.connect(_get_db_path())
    try:
        row = conn.execute(
            "SELECT prompt_text FROM system_prompts WHERE id = ?", (prompt_id,)
        ).fetchone()
        return row[0] if row else None
    finally:
        conn.close()


def set_system_prompt(prompt_id: int, prompt_text: str) -> None:
    """Сохраняет переопределение системного промпта (INSERT OR REPLACE)."""
    conn = sqlite3.connect(_get_db_path())
    try:
        conn.execute(
            "INSERT OR REPLACE INTO system_prompts (id, prompt_text) VALUES (?, ?)",
            (prompt_id, prompt_text),
        )
        conn.commit()
    finally:
        conn.close()


def reset_system_prompt(prompt_id: int) -> None:
    """Удаляет переопределение системного промпта (сброс к дефолту из кода)."""
    conn = sqlite3.connect(_get_db_path())
    try:
        conn.execute("DELETE FROM system_prompts WHERE id = ?", (prompt_id,))
        conn.commit()
    finally:
        conn.close()
