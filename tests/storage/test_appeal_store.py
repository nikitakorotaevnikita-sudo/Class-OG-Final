import json
from contextlib import contextmanager
from datetime import datetime, timedelta

import pytest

from storage.models import AppealQuestionRow, AppealResult

LEGACY_KEYS = {
    "id", "timestamp", "appeal_text", "candidates", "agent_questions",
    "overall_confidence", "verification",
}
VERIF_KEYS = {"status", "verified_at", "operator_codes", "comment"}
Q_KEYS = {"question_text", "selected_code", "confidence", "alternative_codes"}
CODE = "0005.0005.0051.1103"
UNKNOWN = "11111111-2222-3333-4444-555555555555"


def q(i=1, code=CODE, conf=0.8):
    return AppealQuestionRow(
        ordinal=i, question_text=f"вопрос {i}", selected_code=code,
        confidence=conf, alternative_codes=["0001.0001.0001.0001"],
        predmet_vedeniya="пв", reasoning="потому", verification_reasons=["low"],
    )


def result(conf=0.8, questions=None):
    return AppealResult(
        summary="суть", applicant_fio="Иванов И.И.", applicant_email="a@b.ru",
        vid_obrascheniya="Заявление", tip_obrascheniya="Письменное", is_ustnoe=False,
        overall_confidence=conf, needs_verification=False, full_fallback_used=False,
        candidates=[{"code": CODE, "name": "n", "similarity": 0.9}],
        llm_provider="ario", llm_model="qwen", elapsed_seconds=1.5,
        questions=[q()] if questions is None else questions,
    )


def classified(storage, conf=0.8, text="текст", **kw):
    aid = storage.appeals.create(text, **kw)
    storage.appeals.save_result(aid, result(conf))
    return aid


def test_create_makes_pending_verification(storage):
    aid = storage.appeals.create("текст обращения", document_id=7)
    assert isinstance(aid, str) and len(aid) == 36
    assert storage.appeals.get_text(aid) == "текст обращения"
    [e] = storage.appeals.as_log_entries()
    assert e["id"] == aid
    assert e["verification"]["status"] == "pending"
    assert storage.appeals.stats()["pending"] == 1


def test_create_with_given_id(storage):
    assert storage.appeals.create("t", appeal_id=UNKNOWN) == UNKNOWN
    assert storage.appeals.get_text(UNKNOWN) == "t"


def test_get_text_unknown_returns_none(storage):
    assert storage.appeals.get_text(UNKNOWN) is None
    assert storage.appeals.get_text("not-a-uuid") is None


def test_save_result_twice_replaces_questions(storage):
    aid = storage.appeals.create("t")
    storage.appeals.save_result(aid, result(questions=[q(1), q(2)]))
    assert len(storage.appeals.as_log_entries()[0]["agent_questions"]) == 2
    storage.appeals.save_result(aid, result(questions=[q(1)]))
    [e] = storage.appeals.as_log_entries()
    assert len(e["agent_questions"]) == 1


def test_save_result_unknown_id_raises(storage):
    with pytest.raises(KeyError):
        storage.appeals.save_result(UNKNOWN, result())


def test_set_verification_unknown_id_returns_false(storage):
    assert storage.appeals.set_verification(UNKNOWN, "confirmed", None, None) is False
    assert storage.appeals.set_verification("bad", "confirmed", None, None) is False


def test_set_verification_updates(storage):
    aid = classified(storage)
    assert storage.appeals.set_verification(
        aid, "corrected", ["0001.0001.0001.0001"], "коммент", verified_by="op"
    )
    [e] = storage.appeals.as_log_entries()
    v = e["verification"]
    assert v["status"] == "corrected"
    assert v["operator_codes"] == ["0001.0001.0001.0001"]
    assert v["comment"] == "коммент"
    datetime.fromisoformat(v["verified_at"])
    with pytest.raises(ValueError):
        storage.appeals.set_verification(aid, "bogus", None, None)


def test_as_log_entries_shape_matches_legacy(storage):
    aid = classified(storage, conf=0.8, text="текст")
    [e] = storage.appeals.as_log_entries()
    assert set(e) == LEGACY_KEYS
    assert set(e["verification"]) == VERIF_KEYS
    assert e["verification"] == {
        "status": "pending", "verified_at": None, "operator_codes": None, "comment": None,
    }
    assert e["id"] == aid
    assert e["appeal_text"] == "текст"
    assert e["overall_confidence"] == 0.8  # точное равенство: без хвоста float32
    assert e["candidates"] == [{"code": CODE, "name": "n", "similarity": 0.9}]
    [aq] = e["agent_questions"]
    assert set(aq) == Q_KEYS
    assert aq["selected_code"] == CODE
    assert aq["confidence"] == 0.8
    assert aq["alternative_codes"] == ["0001.0001.0001.0001"]
    # формат как в старом логе: ISO без долей секунды и без часового пояса
    ts = e["timestamp"]
    assert datetime.fromisoformat(ts).isoformat(timespec="seconds") == ts
    assert abs(datetime.now() - datetime.fromisoformat(ts)) < timedelta(minutes=5)
    json.dumps(e)


def test_confidences_exact_after_roundtrip(storage):
    aid = storage.appeals.create("t")
    storage.appeals.save_result(aid, result(conf=0.87, questions=[q(1, conf=0.87), q(2, conf=0.1)]))
    [e] = storage.appeals.as_log_entries()
    assert e["overall_confidence"] == 0.87
    assert [x["confidence"] for x in e["agent_questions"]] == [0.87, 0.1]


def test_avg_confidence_rounding_boundary_same_on_both_backends(storage):
    # среднее 0.285: float32 мог бы сдвинуть округление; ожидание считаем на чистых float
    classified(storage, conf=0.28)
    classified(storage, conf=0.29)
    assert storage.appeals.backoffice_stats()["avg_confidence"] == round((0.28 + 0.29) / 2, 2)


def test_as_log_entries_order_and_filter(storage):
    now = datetime.now()
    a = storage.appeals.create("a", created_at=now - timedelta(hours=2))
    b = storage.appeals.create("b", created_at=now - timedelta(hours=3))
    c = storage.appeals.create("c", created_at=now - timedelta(hours=1))
    storage.appeals.set_verification(c, "confirmed", None, None)
    assert [e["id"] for e in storage.appeals.as_log_entries()] == [b, a, c]
    assert [e["id"] for e in storage.appeals.as_log_entries(("confirmed",))] == [c]
    assert [e["id"] for e in storage.appeals.as_log_entries(("pending", "confirmed"))] == [b, a, c]


def test_stats_counts(storage):
    ids = [classified(storage) for _ in range(5)]
    storage.appeals.set_verification(ids[0], "confirmed", None, None)
    storage.appeals.set_verification(ids[1], "confirmed", None, None)
    storage.appeals.set_verification(ids[2], "corrected", [CODE], None)
    storage.appeals.set_verification(ids[3], "rejected", None, "спам")
    assert storage.appeals.stats() == {
        "total": 5, "verified": 3, "pending": 1, "confirmed": 2, "corrected": 1, "rejected": 1,
    }


def test_stats_empty(storage):
    assert storage.appeals.stats() == {
        "total": 0, "verified": 0, "pending": 0, "confirmed": 0, "corrected": 0, "rejected": 0,
    }


def test_backoffice_stats_empty(storage):
    r = storage.appeals.backoffice_stats()
    assert r["total_classifications"] == 0
    assert r["avg_confidence"] == 0.0
    assert r["top_codes"] == [] and r["ip_stats"] == []
    assert len(r["daily_usage"]) == 30
    assert r["confidence_histogram"]["0.0-0.1"] == 0


def test_backoffice_stats_keys_and_counts(storage):
    ids = [classified(storage, conf=0.6), classified(storage, conf=0.8), classified(storage, conf=0.7)]
    storage.appeals.set_verification(ids[0], "confirmed", None, None)
    storage.appeals.set_verification(ids[1], "corrected", [CODE], None)
    r = storage.appeals.backoffice_stats()
    assert set(r) == {
        "total_classifications", "total_verifications", "confirmed", "corrected", "rejected",
        "pending", "avg_confidence", "confidence_histogram", "top_codes", "daily_usage", "ip_stats",
    }
    assert r["total_classifications"] == 3
    assert r["total_verifications"] == 2
    assert (r["confirmed"], r["corrected"], r["rejected"], r["pending"]) == (1, 1, 0, 1)
    assert r["avg_confidence"] == 0.7


def test_backoffice_stats_histogram_and_daily_usage(storage):
    for conf in [0.05, 0.15, 0.25, 0.55, 0.7, 0.95, 1.0]:
        classified(storage, conf=conf)
    storage.appeals.create("не классифицировано")  # NULL: в total, но не в гистограмме
    r = storage.appeals.backoffice_stats()
    hist = r["confidence_histogram"]
    assert list(hist) == [f"{i / 10:.1f}-{(i + 1) / 10:.1f}" for i in range(10)]
    assert hist["0.0-0.1"] == 1 and hist["0.1-0.2"] == 1 and hist["0.2-0.3"] == 1
    assert hist["0.5-0.6"] == 1 and hist["0.7-0.8"] == 1
    assert hist["0.9-1.0"] == 2  # 0.95 и 1.0
    assert sum(hist.values()) == 7
    assert r["total_classifications"] == 8
    assert r["pending"] == 8
    assert r["avg_confidence"] == round((0.05 + 0.15 + 0.25 + 0.55 + 0.7 + 0.95 + 1.0) / 7, 2)
    du = r["daily_usage"]
    assert len(du) == 30
    today = datetime.now().date()
    assert [d["date"] for d in du] == [
        (today - timedelta(days=i)).isoformat() for i in range(29, -1, -1)
    ]
    assert du[-1]["count"] == 8
    assert sum(d["count"] for d in du[:-1]) == 0


def test_backoffice_stats_daily_usage_window(storage):
    now = datetime.now()
    storage.appeals.create("вчера", created_at=now - timedelta(days=1))
    storage.appeals.create("давно", created_at=now - timedelta(days=40))
    r = storage.appeals.backoffice_stats(days=7)
    assert len(r["daily_usage"]) == 7
    assert r["daily_usage"][-2]["count"] == 1
    assert r["total_classifications"] == 2


def test_backoffice_top_codes_from_verified_only(storage):
    a = storage.appeals.create("a")
    storage.appeals.save_result(a, result(questions=[q(1, "0001.0001.0001.0001"), q(2, CODE)]))
    b = storage.appeals.create("b")
    storage.appeals.save_result(b, result(questions=[q(1, CODE)]))
    c = storage.appeals.create("c")  # pending: не учитывается
    storage.appeals.save_result(c, result(questions=[q(1, "9999.0000.0000.0000")]))
    storage.appeals.set_verification(a, "confirmed", None, None)
    storage.appeals.set_verification(b, "corrected", ["x"], None)
    top = storage.appeals.backoffice_stats()["top_codes"]
    assert top == [{"code": CODE, "count": 2}, {"code": "0001.0001.0001.0001", "count": 1}]


def test_ip_stats_from_cards(storage):
    storage.appeals.create("a", ip_address="10.0.0.1")
    storage.appeals.create("b", ip_address="10.0.0.1")
    storage.appeals.create("c", ip_address="10.0.0.2")
    storage.appeals.create("d")  # без ip: исключается
    x = storage.appeals.create("e")
    storage.appeals.set_request_meta(x, "10.0.0.2", 3.5)
    y = storage.appeals.create("f")
    storage.appeals.set_request_meta(y, "10.0.0.2", None)
    ip = storage.appeals.backoffice_stats()["ip_stats"]
    assert ip == [{"ip": "10.0.0.2", "count": 3}, {"ip": "10.0.0.1", "count": 2}]


def test_set_request_meta_unknown_id_is_noop(storage):
    storage.appeals.set_request_meta(UNKNOWN, "10.0.0.1", 1.0)
    assert storage.appeals.backoffice_stats()["ip_stats"] == []


def test_log_llm_request_persists(storage, request):
    aid = storage.appeals.create("t")
    storage.appeals.log_llm_request(aid, True, 1, 2.5, 0.9, None, 10)
    storage.appeals.log_llm_request(None, False, 2, None, None, "timeout", 10)
    if "pg_conn" in request.fixturenames:
        n = request.getfixturevalue("pg_conn").execute(
            "SELECT count(*) FROM llm_request_log"
        ).fetchone()[0]
    else:
        n = len(storage.appeals._llm_log)
    assert n == 2


@pytest.mark.db
def test_tx_with_caller_conn_does_not_commit(pg_conn):
    """_tx(conn=...) переиспользует соединение без commit: откат вызывающего стирает всё."""
    from storage.pg import PgStorage

    entered = []

    @contextmanager
    def connect():
        entered.append(1)
        with pg_conn.transaction():
            yield pg_conn

    st = PgStorage(connect)
    with pg_conn.transaction(force_rollback=True):
        aid = st.appeals.create("не должно сохраниться", conn=pg_conn)
        st.appeals.save_result(aid, result(), conn=pg_conn)
        assert pg_conn.execute("SELECT count(*) FROM appeal_classifications").fetchone()[0] == 1
    assert entered == []  # собственная транзакция хранилища не открывалась
    assert pg_conn.execute("SELECT count(*) FROM appeal_classifications").fetchone()[0] == 0
    assert pg_conn.execute("SELECT count(*) FROM appeal_questions").fetchone()[0] == 0
    assert pg_conn.execute("SELECT count(*) FROM appeal_verifications").fetchone()[0] == 0


def test_save_result_accepts_legacy_null_fields(storage):
    """Старые карточки: поля классификации отсутствуют -> NULL, не падаем."""
    aid = storage.appeals.create("старое обращение")
    legacy = AppealResult(
        summary=None, applicant_fio=None, applicant_email=None,
        vid_obrascheniya=None, tip_obrascheniya=None, is_ustnoe=None,
        overall_confidence=0.7, needs_verification=None, full_fallback_used=None,
        candidates=[], llm_provider=None, llm_model=None, elapsed_seconds=None,
        questions=[q()],
    )
    storage.appeals.save_result(aid, legacy)
    [e] = storage.appeals.as_log_entries()
    assert e["overall_confidence"] == 0.7
    assert len(e["agent_questions"]) == 1


def test_llm_log_count_and_explicit_created_at(storage):
    assert storage.appeals.llm_log_count() == 0
    when = datetime(2026, 4, 22, 14, 0, 0)
    storage.appeals.log_llm_request(None, True, 1, 1.0, 0.5, None, 3, created_at=when)
    storage.appeals.log_llm_request(None, False, 2, None, None, "err", 3)
    assert storage.appeals.llm_log_count() == 2


def test_ip_addresses_per_card(storage):
    a = storage.appeals.create("a", ip_address="10.0.0.1")
    b = storage.appeals.create("b")
    got = storage.appeals.ip_addresses([a, b, UNKNOWN])
    assert got == {a: "10.0.0.1", b: None}


# --- WP3: паритет memory/pg и общие хелперы ----------------------------------------------

def test_as_log_entries_ties_ordered_by_id(storage):
    """Одинаковый created_at: порядок по id в обоих бэкендах."""
    when = datetime(2026, 5, 1, 12, 0, 0)
    ids = [str(__import__("uuid").uuid4()) for _ in range(6)]
    for aid in ids:
        storage.appeals.create("t", appeal_id=aid, created_at=when)
    assert [e["id"] for e in storage.appeals.as_log_entries()] == sorted(ids)


def test_as_log_entries_filter_keeps_questions_of_selected_cards(storage):
    a = classified(storage)
    b = storage.appeals.create("b")
    storage.appeals.save_result(b, result(questions=[q(1), q(2)]))
    storage.appeals.set_verification(b, "confirmed", None, None)
    [e] = storage.appeals.as_log_entries(("confirmed",))
    assert e["id"] == b and len(e["agent_questions"]) == 2
    assert storage.appeals.as_log_entries(("rejected",)) == []
    assert {x["id"] for x in storage.appeals.as_log_entries(("pending",))} == {a}


def test_duplicate_create_raises_duplicate_error(storage):
    from storage.base import DuplicateError

    storage.appeals.create("t", appeal_id=UNKNOWN)
    with pytest.raises(DuplicateError):
        storage.appeals.create("t2", appeal_id=UNKNOWN)
    assert storage.appeals.get_text(UNKNOWN) == "t"


@pytest.mark.db
def test_duplicate_create_inside_transaction_keeps_it_usable(pg_conn):
    """Дубликат внутри storage.transaction() не обрывает транзакцию вызывающего (savepoint)."""
    from contextlib import contextmanager

    from storage.base import DuplicateError
    from storage.pg import PgStorage

    @contextmanager
    def connect():
        with pg_conn.transaction():
            yield pg_conn

    st = PgStorage(connect)
    with st.transaction() as tx:
        st.appeals.create("t", appeal_id=UNKNOWN, conn=tx)
        with pytest.raises(DuplicateError):
            st.appeals.create("t2", appeal_id=UNKNOWN, conn=tx)
        other = st.appeals.create("другое", conn=tx)  # запись после дубликата в той же транзакции
    assert st.appeals.get_text(UNKNOWN) == "t"
    assert st.appeals.get_text(other) == "другое"


def test_duplicate_error_is_value_error():
    from storage.base import DuplicateError

    assert issubclass(DuplicateError, ValueError)  # старые вызовы ловили ValueError


def test_log_llm_request_unknown_or_bad_appeal_id_same_on_both(storage, request):
    """llm_request_log.appeal_id без FK: неизвестный UUID пишется как есть, не-UUID -> NULL."""
    storage.appeals.log_llm_request(UNKNOWN, True, 1, 1.0, 0.5, None, 3)
    storage.appeals.log_llm_request("not-a-uuid", True, 1, 1.0, 0.5, None, 3)
    if "pg_conn" in request.fixturenames:
        rows = request.getfixturevalue("pg_conn").execute(
            "SELECT appeal_id::text FROM llm_request_log ORDER BY id"
        ).fetchall()
        got = [r[0] for r in rows]
    else:
        got = [r["appeal_id"] for r in storage.appeals._llm_log]
    assert got == [UNKNOWN, None]
    assert storage.appeals.llm_log_count() == 2


def test_set_request_meta_only_missing_keeps_live_values(storage):
    aid = storage.appeals.create("t", ip_address="10.0.0.1")
    storage.appeals.set_request_meta(aid, None, 2.0)
    storage.appeals.set_request_meta(aid, "10.9.9.9", 7.0, only_missing=True)
    assert storage.appeals.ip_addresses([aid]) == {aid: "10.0.0.1"}
    b = storage.appeals.create("b")
    storage.appeals.set_request_meta(b, "10.0.0.2", 3.0, only_missing=True)
    assert storage.appeals.ip_addresses([b]) == {b: "10.0.0.2"}
    assert storage.appeals.backoffice_stats()["ip_stats"] == [
        {"ip": "10.0.0.1", "count": 1}, {"ip": "10.0.0.2", "count": 1}]


def test_set_request_meta_only_missing_elapsed(storage, request):
    aid = storage.appeals.create("t")
    storage.appeals.set_request_meta(aid, None, 2.0)
    storage.appeals.set_request_meta(aid, None, 7.0, only_missing=True)
    if "pg_conn" in request.fixturenames:
        got = request.getfixturevalue("pg_conn").execute(
            "SELECT elapsed_seconds FROM appeal_classifications WHERE id = %s", (aid,)
        ).fetchone()[0]
    else:
        got = storage.appeals._cards[aid]["elapsed_seconds"]
    assert got == 2.0


def test_backoffice_stats_mixed_statuses_and_repeated_codes(storage):
    now = datetime.now()
    a = storage.appeals.create("a", ip_address="10.0.0.3", created_at=now - timedelta(days=2))
    storage.appeals.save_result(a, result(conf=0.35, questions=[q(1, CODE), q(2, CODE)]))
    storage.appeals.set_verification(a, "corrected", [CODE], None)
    b = storage.appeals.create("b", ip_address="10.0.0.3")
    storage.appeals.save_result(b, result(conf=0.1, questions=[q(1, "0002.0002.0002.0002")]))
    storage.appeals.set_verification(b, "confirmed", None, None)
    c = storage.appeals.create("c", ip_address="10.0.0.1")
    storage.appeals.save_result(c, result(conf=0.0, questions=[q(1, CODE)]))
    storage.appeals.set_verification(c, "rejected", None, None)
    old = storage.appeals.create("old", created_at=now - timedelta(days=100))
    storage.appeals.save_result(old, result(conf=0.99, questions=[]))
    r = storage.appeals.backoffice_stats(days=5)
    assert r["total_classifications"] == 4
    assert (r["confirmed"], r["corrected"], r["rejected"], r["pending"]) == (1, 1, 1, 1)
    assert r["top_codes"] == [{"code": CODE, "count": 2},
                              {"code": "0002.0002.0002.0002", "count": 1}]
    assert r["ip_stats"] == [{"ip": "10.0.0.3", "count": 2}, {"ip": "10.0.0.1", "count": 1}]
    h = r["confidence_histogram"]
    assert (h["0.0-0.1"], h["0.1-0.2"], h["0.3-0.4"], h["0.9-1.0"]) == (1, 1, 1, 1)
    assert r["avg_confidence"] == round((0.35 + 0.1 + 0.0 + 0.99) / 4, 2)
    assert [d["count"] for d in r["daily_usage"]] == [0, 0, 1, 0, 2]
