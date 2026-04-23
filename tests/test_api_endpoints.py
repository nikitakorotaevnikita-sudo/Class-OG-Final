"""Тесты новых эндпоинтов: /verify и /examples.

Используем прямой async-вызов функций эндпоинтов — это избегает
запуска startup-event'а (который грузит ML-модель и требует GROQ_API_KEY).
"""

import asyncio
import json
import sys
from pathlib import Path

import pytest
from fastapi import HTTPException

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from api_server import verify, get_examples, VerifyRequest
from appeals_logger import AppealsLogger


@pytest.fixture
def isolated_logger(tmp_path):
    """Логгер с временным JSONL-файлом — не пишет в продовый лог."""
    log_path = tmp_path / "test_log.jsonl"
    return AppealsLogger(log_path=log_path)


@pytest.fixture
def examples_file(tmp_path):
    """Временный test_appeals.json с двумя примерами."""
    path = tmp_path / "test_appeals.json"
    data = [
        {"id": "ex01", "title": "Пример 1", "text": "Текст обращения 1"},
        {"id": "ex02", "title": "Пример 2", "text": "Текст обращения 2"},
    ]
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    return path


def test_verify_confirm_marks_entry_confirmed(isolated_logger):
    log_id = isolated_logger.log(
        appeal_text="тестовое обращение",
        candidates=[],
        agent_questions=[],
        overall_confidence=0.9,
    )

    result = asyncio.run(verify(
        VerifyRequest(log_id=log_id, action="confirm"),
        logger=isolated_logger,
    ))

    assert result["status"] == "ok"
    assert result["log_id"] == log_id
    assert result["action"] == "confirm"
    assert "annotation_count" in result
    entries = isolated_logger.read_all()
    assert entries[0]["verification"]["status"] == "confirmed"


def test_verify_correct_writes_operator_codes(isolated_logger):
    log_id = isolated_logger.log(
        appeal_text="тестовое обращение",
        candidates=[],
        agent_questions=[],
        overall_confidence=0.5,
    )

    result = asyncio.run(verify(
        VerifyRequest(
            log_id=log_id,
            action="correct",
            operator_codes=["0005.0005.0051.1103"],
        ),
        logger=isolated_logger,
    ))

    assert result["action"] == "correct"
    entry = isolated_logger.read_all()[0]
    assert entry["verification"]["status"] == "corrected"
    assert entry["verification"]["operator_codes"] == ["0005.0005.0051.1103"]


def test_verify_correct_without_codes_returns_400(isolated_logger):
    log_id = isolated_logger.log(
        appeal_text="x",
        candidates=[],
        agent_questions=[],
        overall_confidence=0.5,
    )

    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(verify(
            VerifyRequest(log_id=log_id, action="correct"),
            logger=isolated_logger,
        ))

    assert exc_info.value.status_code == 400
    assert "operator_codes" in exc_info.value.detail


def test_verify_reject_marks_entry_rejected(isolated_logger):
    log_id = isolated_logger.log(
        appeal_text="спам",
        candidates=[],
        agent_questions=[],
        overall_confidence=0.3,
    )

    asyncio.run(verify(
        VerifyRequest(log_id=log_id, action="reject"),
        logger=isolated_logger,
    ))

    assert isolated_logger.read_all()[0]["verification"]["status"] == "rejected"


def test_examples_returns_list_from_file(examples_file):
    result = asyncio.run(get_examples(examples_path=examples_file))

    assert "examples" in result
    assert len(result["examples"]) == 2
    assert result["examples"][0]["id"] == "ex01"
    assert result["examples"][0]["title"] == "Пример 1"