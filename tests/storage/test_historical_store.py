"""HistoricalStore: память и PostgreSQL."""

REC = {"appeal_text": "не вывозят мусор", "assigned_code": "0005.0005.0056.1160",
       "specialist": "Петрова", "date": "2025-01-01"}


def test_add_dedups_same_record(storage):
    h = storage.historical
    assert h.add([REC, dict(REC)], "a.csv") == 1
    assert h.add([REC], "a.csv") == 0
    # другой файл-источник — не дубль
    assert h.add([REC], "b.csv") == 1
    assert h.count() == 2


def test_count_and_all(storage):
    h = storage.historical
    assert h.count() == 0
    assert h.all() == []
    other = {"appeal_text": "яма на дороге", "assigned_code": "0005.0005.0056.1161"}
    assert h.add([REC, other], "a.csv") == 2
    assert h.count() == 2
    rows = h.all()
    assert rows[0] == {
        "appeal_text": "не вывозят мусор", "assigned_code": "0005.0005.0056.1160",
        "specialist": "Петрова", "registration_date": "2025-01-01", "source_file": "a.csv",
    }
    assert rows[1]["appeal_text"] == "яма на дороге"
    assert rows[1]["specialist"] is None and rows[1]["registration_date"] is None
