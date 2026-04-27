"""Тесты эндпоинтов: /verify, /examples, /api/backoffice/stats.

Используем прямой async-вызов функций эндпоинтов — это избегает
запуска startup-event'а (который грузит ML-модель и требует GROQ_API_KEY).
"""

import asyncio
import json
import sys
import time as _time
from pathlib import Path

import pytest
from fastapi import HTTPException

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from api_server import verify, get_examples, VerifyRequest, backoffice_stats, log_request, get_current_user
from appeals_logger import AppealsLogger


@pytest.fixture
def backoffice_logger(tmp_path):
    """Logger with a temp file, and patch _REQUEST_LOG for log_request tests."""
    log_path = tmp_path / "appeals_log.jsonl"
    logger = AppealsLogger(log_path=log_path)

    # Patch the module-level _REQUEST_LOG so log_request writes to temp dir
    import api_server as api_mod
    orig_log = api_mod._REQUEST_LOG
    api_mod._REQUEST_LOG = tmp_path / "request_log.jsonl"
    yield logger
    api_mod._REQUEST_LOG = orig_log


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


# ── tests for /api/backoffice/stats ────────────────────────────────────────────

def test_backoffice_stats_returns_all_fields(backoffice_logger):
    """backoffice_stats returns correct structure with all required fields."""
    # Write some entries
    for i in range(3):
        backoffice_logger.log(
            appeal_text=f"текст {i}",
            candidates=[],
            agent_questions=[{"selected_code": "0005.0005.0051.1103", "confidence": 0.8}],
            overall_confidence=0.8,
        )

    result = asyncio.run(backoffice_stats(_username="admin", logger=backoffice_logger))

    assert "total_classifications" in result
    assert "total_verifications" in result
    assert "confirmed" in result
    assert "corrected" in result
    assert "rejected" in result
    assert "pending" in result
    assert "avg_confidence" in result
    assert "confidence_histogram" in result
    assert "top_codes" in result
    assert "daily_usage" in result
    assert "ip_stats" in result
    assert isinstance(result["confidence_histogram"], dict)
    assert isinstance(result["top_codes"], list)
    assert isinstance(result["ip_stats"], list)


def test_backoffice_stats_count_total(isolated_logger):
    """total_classifications equals number of log entries."""
    for i in range(5):
        isolated_logger.log(
            appeal_text=f"текст {i}",
            candidates=[],
            agent_questions=[],
            overall_confidence=0.5,
        )

    result = asyncio.run(backoffice_stats(_username="admin", logger=isolated_logger))
    assert result["total_classifications"] == 5


def test_backoffice_stats_verified_counts(isolated_logger):
    """verified counts reflect confirmed + corrected entries."""
    log_ids = []
    for i in range(4):
        lid = isolated_logger.log(
            appeal_text=f"текст {i}",
            candidates=[],
            agent_questions=[],
            overall_confidence=0.5,
        )
        log_ids.append(lid)

    isolated_logger.confirm(log_ids[0])
    isolated_logger.confirm(log_ids[1])
    isolated_logger.correct(log_ids[2], ["0005.0005.0051.1103"])

    result = asyncio.run(backoffice_stats(_username="admin", logger=isolated_logger))
    assert result["total_verifications"] == 3
    assert result["confirmed"] == 2
    assert result["corrected"] == 1


def test_backoffice_stats_avg_confidence(backoffice_logger):
    """avg_confidence is mean of all overall_confidence values."""
    for conf in [0.6, 0.8, 0.7]:
        backoffice_logger.log(
            appeal_text="текст",
            candidates=[],
            agent_questions=[],
            overall_confidence=conf,
        )

    result = asyncio.run(backoffice_stats(_username="admin", logger=backoffice_logger))
    assert abs(result["avg_confidence"] - 0.7) < 0.01


def test_backoffice_stats_histogram_binning(backoffice_logger):
    """entries are binned into correct histogram buckets."""
    # Add entries at known confidence levels
    for conf in [0.05, 0.15, 0.25, 0.55, 0.75, 0.95]:
        backoffice_logger.log(
            appeal_text="текст",
            candidates=[],
            agent_questions=[],
            overall_confidence=conf,
        )

    result = asyncio.run(backoffice_stats(_username="admin", logger=backoffice_logger))
    hist = result["confidence_histogram"]
    assert hist["0.0-0.1"] == 1
    assert hist["0.1-0.2"] == 1
    assert hist["0.2-0.3"] == 1
    assert hist["0.5-0.6"] == 1
    assert hist["0.7-0.8"] == 1
    assert hist["0.9-1.0"] == 1


def test_backoffice_stats_top_codes_from_verified(isolated_logger):
    """top_codes come only from confirmed/corrected entries."""
    lid = isolated_logger.log(
        appeal_text="текст",
        candidates=[],
        agent_questions=[{"selected_code": "0001.0001.0001.0001", "confidence": 0.9}],
        overall_confidence=0.9,
    )
    isolated_logger.confirm(lid)

    result = asyncio.run(backoffice_stats(_username="admin", logger=isolated_logger))
    top = result["top_codes"]
    assert len(top) == 1
    assert top[0]["code"] == "0001.0001.0001.0001"
    assert top[0]["count"] == 1


def test_backoffice_stats_daily_usage_count(isolated_logger):
    """daily_usage returns last 30 days with count per day."""
    result = asyncio.run(backoffice_stats(_username="admin", logger=isolated_logger))
    assert len(result["daily_usage"]) == 30
    # All entries should have date and count keys
    for day in result["daily_usage"]:
        assert "date" in day
        assert "count" in day


def test_backoffice_stats_empty_log(tmp_path):
    """empty log returns zeros and empty arrays, not crashes."""
    log_path = tmp_path / "appeals_log.jsonl"
    logger = AppealsLogger(log_path=log_path)

    result = asyncio.run(backoffice_stats(_username="admin", logger=logger))
    assert result["total_classifications"] == 0
    assert result["total_verifications"] == 0
    assert result["avg_confidence"] == 0.0
    assert result["top_codes"] == []
    assert result["confidence_histogram"]["0.0-0.1"] == 0


# ── tests for IP logging ────────────────────────────────────────────────────────

def test_log_request_appends_to_file(backoffice_logger):
    """log_request appends a JSON line to request_log.jsonl."""
    # backoffice_logger patches _REQUEST_LOG to point to tmp dir
    import api_server as api_mod
    log_path = api_mod._REQUEST_LOG
    log_request("192.168.1.10", "/classify", 1.5, "abc-123")
    log_request("192.168.1.11", "/classify/file", 2.0, "def-456")

    assert log_path.exists()
    lines = log_path.read_text(encoding="utf-8").strip().split("\n")
    assert len(lines) == 2

    rec1 = json.loads(lines[0])
    assert rec1["ip"] == "192.168.1.10"
    assert rec1["endpoint"] == "/classify"
    assert abs(rec1["elapsed_seconds"] - 1.5) < 0.01
    assert rec1["log_id"] == "abc-123"
    assert "timestamp" in rec1


def test_log_request_handles_none_log_id(backoffice_logger):
    """log_request handles None log_id gracefully."""
    import api_server as api_mod
    log_path = api_mod._REQUEST_LOG
    log_request("10.0.0.1", "/classify", 0.5, None)

    lines = log_path.read_text(encoding="utf-8").strip().split("\n")
    rec = json.loads(lines[0])
    assert rec["log_id"] == ""


def test_get_current_user_rejects_wrong_password():
    """get_current_user raises 401 for wrong credentials."""
    from fastapi.security import HTTPBasicCredentials
    from starlette.status import HTTP_401_UNAUTHORIZED

    with pytest.raises(HTTPException) as exc:
        asyncio.run(get_current_user(
            HTTPBasicCredentials(username="admin", password="wrong")
        ))
    assert exc.value.status_code == HTTP_401_UNAUTHORIZED


def test_get_current_user_accepts_correct_credentials():
    """get_current_user returns username for correct credentials."""
    from fastapi.security import HTTPBasicCredentials

    result = get_current_user(
        HTTPBasicCredentials(username="admin", password="password")
    )
    assert result == "admin"