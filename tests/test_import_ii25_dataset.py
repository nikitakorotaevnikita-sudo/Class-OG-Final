from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile

from scripts.import_ii25_dataset import (
    extract_docx_text,
    parse_filename_label,
    resolve_label,
)


CLASSIFIER = [
    {
        "code": "0002.0013.0139.0325",
        "name": "School admission",
        "level": 4,
        "children_count": 8,
        "full_path": "",
    },
    {
        "code": "0002.0013.0139.0325.0035",
        "name": "Vocational education",
        "level": 5,
        "children_count": 0,
        "full_path": "",
    },
    {
        "code": "0003.0009.0097.0689",
        "name": "Complex improvement",
        "level": 4,
        "children_count": 0,
        "full_path": "",
    },
    {
        "code": "0003.0009.0099.0737.0061",
        "name": "Road transport",
        "level": 5,
        "children_count": 0,
        "full_path": "",
    },
    {
        "code": "0003.0009.0096.0684",
        "name": "Road construction",
        "level": 4,
        "children_count": 2,
        "full_path": "",
    },
]


def test_parse_filename_label_handles_dataset_variants():
    simple = parse_filename_label(Path("03-689-2.docx"))
    assert simple.section == "0003"
    assert simple.target == "0689"
    assert simple.subtarget is None

    explicit_leaf = parse_filename_label(Path("03-737-0061.docx"))
    assert explicit_leaf.section == "0003"
    assert explicit_leaf.target == "0737"
    assert explicit_leaf.subtarget == "0061"

    batch = parse_filename_label(Path("21-2-689.docx"))
    assert batch.section is None
    assert batch.target == "0689"
    assert batch.mode == "batch_21"

    middle_marker = parse_filename_label(Path("3-1-684.docx"))
    assert middle_marker.section == "0003"
    assert middle_marker.target == "0684"
    assert middle_marker.mode == "extra_middle_marker"


def test_resolve_label_prefers_exact_parent_when_no_subtarget():
    label = parse_filename_label(Path("02-325.docx"))
    entry, matches = resolve_label(label, CLASSIFIER)

    assert entry["code"] == "0002.0013.0139.0325"
    assert len(matches) == 1


def test_resolve_label_uses_explicit_subtarget_for_leaf_codes():
    label = parse_filename_label(Path("03-737-0061.docx"))
    entry, _ = resolve_label(label, CLASSIFIER)

    assert entry["code"] == "0003.0009.0099.0737.0061"


def test_resolve_label_can_infer_section_for_batch_files():
    label = parse_filename_label(Path("21-2-689.docx"))
    entry, _ = resolve_label(label, CLASSIFIER)

    assert entry["code"] == "0003.0009.0097.0689"


def test_extract_docx_text_reads_paragraphs(tmp_path):
    docx_path = tmp_path / "sample.docx"
    document_xml = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
  <w:body>
    <w:p><w:r><w:t>First paragraph</w:t></w:r></w:p>
    <w:p><w:r><w:t>Second</w:t></w:r><w:r><w:t> paragraph</w:t></w:r></w:p>
  </w:body>
</w:document>"""
    with ZipFile(docx_path, "w", compression=ZIP_DEFLATED) as archive:
        archive.writestr("word/document.xml", document_xml)

    assert extract_docx_text(docx_path) == "First paragraph\nSecond paragraph"
