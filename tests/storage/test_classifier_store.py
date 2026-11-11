import numpy as np
import pytest

from storage.models import ClassifierEntry, ModelVersion

A = "0001.0000.0000.0000"
B = "0002.0000.0000.0000"
A1 = "0001.0001.0000.0000"


def vec(i):
    v = np.zeros(768, dtype=np.float32)
    v[i] = 1.0
    return v


def entries():
    return [
        ClassifierEntry(B, "B", 1, None, "B", 0),
        ClassifierEntry(A, "A", 1, None, "A", 1),
        ClassifierEntry("0001.0001.0000.0000", "A1", 2, A, "A/A1", 0),
    ]


def setup_versions(storage, n=1):
    for v in range(n):
        storage.models.register(ModelVersion(v, "base", f"m{v}", v == 0))


def test_upsert_and_load_entries_roundtrip(storage):
    assert storage.classifier.upsert_entries(entries()) == 3
    got = storage.classifier.load_entries()
    # порядок как во входном списке (classifier_flat.json), не по коду
    assert [e.code for e in got] == [e.code for e in entries()]
    assert all(e.search_text == "" for e in got)
    assert storage.classifier.count_entries() == 3
    storage.classifier.upsert_entries(entries())
    assert storage.classifier.count_entries() == 3


def test_load_entries_search_text_from_active_base(storage):
    storage.classifier.upsert_entries(entries())
    assert storage.classifier.load_entries()[0].search_text == ""
    setup_versions(storage)
    storage.classifier.add_vectors(0, [(A, "base", "txt A", vec(0))])
    got = {e.code: e.search_text for e in storage.classifier.load_entries()}
    assert got[A] == "txt A"
    assert got[B] == ""


def test_search_orders_by_cosine_and_groups_by_code(storage):
    storage.classifier.upsert_entries(entries())
    setup_versions(storage)
    ann = ((vec(0) + vec(1)) / np.sqrt(2)).astype(np.float32)
    storage.classifier.add_vectors(
        0, [(A, "base", "a", vec(0)), (B, "base", "b", vec(1)), (A, "annotation", "ann", ann)]
    )
    r = storage.classifier.search(vec(1), 0, 10, True)
    assert [c for c, _ in r] == [B, A]
    assert r[0][1] == pytest.approx(1.0, abs=1e-5)
    assert r[1][1] == pytest.approx(0.7071, abs=1e-3)
    r = dict(storage.classifier.search(vec(1), 0, 10, False))
    assert r[A] == pytest.approx(0.0, abs=1e-5)


def test_search_ignores_null_embeddings(storage):
    storage.classifier.upsert_entries(entries())
    setup_versions(storage)
    storage.classifier.add_vectors(0, [(A, "base", "a", vec(0))])
    storage.classifier.add_annotation(B, "x", 0)
    res = storage.classifier.search(vec(0), 0, 10, True)
    assert [c for c, _ in res] == [A]


def test_search_filters_by_version(storage):
    storage.classifier.upsert_entries(entries())
    setup_versions(storage, 2)
    storage.classifier.add_vectors(0, [(A, "base", "a", vec(0))])
    storage.classifier.add_vectors(1, [(B, "base", "b", vec(0))])
    assert [c for c, _ in storage.classifier.search(vec(0), 1, 10, False)] == [B]


def test_activate_switches_single_active(storage):
    setup_versions(storage, 2)
    assert storage.models.active().version == 0
    storage.models.activate(1)
    assert storage.models.active().version == 1
    assert storage.models.get(0).is_active is False
    assert storage.models.next_version() == 2
    with pytest.raises(KeyError):
        storage.models.activate(99)


def test_add_annotation_then_pending_then_set_embedding(storage):
    storage.classifier.upsert_entries(entries())
    setup_versions(storage)
    vid = storage.classifier.add_annotation(A, "note", 0)
    assert storage.classifier.pending_annotations(0) == [(vid, "note")]
    assert storage.classifier.annotations_for(A) == ["note"]
    assert storage.classifier.annotated_codes() == [A]
    storage.classifier.set_embedding(vid, vec(3))
    assert storage.classifier.pending_annotations(0) == []
    assert storage.classifier.vectors_for_version(0) == [(A, "annotation", "note")]


def test_search_ties_broken_by_code(storage):
    storage.classifier.upsert_entries(entries())
    setup_versions(storage)
    # одинаковые векторы -> равная similarity; порядок задаёт код
    storage.classifier.add_vectors(
        0,
        [
            (B, "base", "b", vec(3)),
            (A, "base", "a", vec(3)),
            (A1, "base", "c", vec(3)),
        ],
    )
    for _ in range(3):
        r = storage.classifier.search(vec(3), 0, 10, False)
        assert [c for c, _ in r] == [A, A1, B]
    assert [c for c, _ in storage.classifier.search(vec(3), 0, 2, False)] == [A, A1]


def test_register_active_deactivates_others(storage):
    storage.models.register(ModelVersion(0, "base", "m0", True))
    storage.models.register(ModelVersion(1, "base", "m1", True))
    assert storage.models.active().version == 1
    assert [m.is_active for m in storage.models.list()] == [False, True]
    # регистрация неактивной версии не трогает активную
    storage.models.register(ModelVersion(2, "base", "m2", False))
    assert storage.models.active().version == 1


def test_delete_annotations_by_prefix(storage):
    storage.classifier.upsert_entries(entries())
    setup_versions(storage)
    storage.classifier.add_annotation(A, "note: x", 0)
    storage.classifier.add_annotation(A, "note: y", 0)
    storage.classifier.add_annotation(A, "comment 100%_", 0)
    assert storage.classifier.delete_annotations(A, "note: ") == 2
    assert storage.classifier.annotations_for(A) == ["comment 100%_"]
    assert storage.classifier.delete_annotations(A, "comment 100%_") == 1
    assert storage.classifier.delete_annotations(A) == 0
    storage.classifier.add_annotation(A, "z", 0)
    assert storage.classifier.delete_annotations(A) == 1
    assert storage.classifier.annotated_codes() == []


def test_load_entries_preserves_given_order(storage):
    given = [entries()[2], entries()[0], entries()[1]]  # A1, B, A — не по коду
    storage.classifier.upsert_entries(given)
    assert [e.code for e in storage.classifier.load_entries()] == [e.code for e in given]
    assert [e.sort_order for e in storage.classifier.load_entries()] == [0, 1, 2]
