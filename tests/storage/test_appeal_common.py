"""Общие хелперы AppealStore (appeal_common)."""

import logging

from storage import appeal_common as ac


def test_norm_ip_plain_and_empty():
    assert ac.norm_ip("10.0.0.1") == "10.0.0.1"
    assert ac.norm_ip(" ::1 ") == "::1"
    assert ac.norm_ip(None) is None and ac.norm_ip("") is None


def test_norm_ip_proxied_list_takes_first_valid():
    assert ac.norm_ip("10.0.0.1, 10.0.0.2") == "10.0.0.1"
    assert ac.norm_ip("unknown, 10.0.0.2") == "10.0.0.2"
    assert ac.norm_ip(" , 192.168.1.5 ,10.0.0.9") == "192.168.1.5"


def test_norm_ip_non_ip_is_none_and_logged_at_debug(caplog):
    with caplog.at_level(logging.DEBUG, logger="storage.appeal_common"):
        assert ac.norm_ip("unknown") is None
        assert ac.norm_ip("a, b") is None
    msgs = [r for r in caplog.records if r.name == "storage.appeal_common"]
    assert msgs and all(r.levelno == logging.DEBUG for r in msgs)


def test_stats_payload_shape():
    assert ac.stats_payload({"pending": 1, "confirmed": 2, "corrected": 3}) == {
        "total": 6, "verified": 5, "pending": 1, "confirmed": 2, "corrected": 3, "rejected": 0,
    }
