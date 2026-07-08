"""Import the II25 DOCX dataset into JSONL files for retrieval training.

The dataset stores labels in file names. Examples:
  02-239.docx       -> 0002.0004.0051.0239
  03-737-0061.docx  -> 0003.0009.0099.0737.0061
  21-2-689.docx     -> lookup by target 0689 without using the batch prefix

Usage:
  python scripts/import_ii25_dataset.py --source "C:\\path\\to\\II25"
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter
from dataclasses import asdict, dataclass
from pathlib import Path
from zipfile import ZipFile
import xml.etree.ElementTree as ET


REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SOURCE = Path.home() / "Desktop" / "Проекты" / "ИИ25"
DEFAULT_CLASSIFIER = REPO_ROOT / "data" / "classifier_flat.json"
DEFAULT_TRAIN_OUT = REPO_ROOT / "data" / "ii25_train.jsonl"
DEFAULT_TEST_OUT = REPO_ROOT / "data" / "ii25_test.jsonl"
DEFAULT_REPORT_OUT = REPO_ROOT / "data" / "ii25_report.json"

TRAIN_MARKER = "обучение"
TEST_MARKER = "тестирование"
DOCX_NS = {"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"}


@dataclass(frozen=True)
class ParsedLabel:
    section: str | None
    target: str
    subtarget: str | None
    raw_numbers: list[str]
    mode: str


@dataclass(frozen=True)
class ImportRecord:
    id: str
    split: str
    source_file: str
    source_path: str
    appeal_text: str
    assigned_code: str
    code_name: str
    full_path: str
    level: int
    parent_code: str
    parsed_label: dict


def load_classifier(path: Path) -> list[dict]:
    with path.open(encoding="utf-8") as f:
        return json.load(f)


def normalize_code_part(value: int | str) -> str:
    return f"{int(value):04d}"


def parse_filename_label(path: Path) -> ParsedLabel | None:
    """Parse a classifier target from a dataset DOCX file name.

    The first number is usually the L1 section. Some test files start with a
    batch marker such as 21-2-689; in that shape the true label is 0689 and the
    section must be inferred from the classifier.
    """
    stem = re.sub(r"\s+с\s+приложениями$", "", path.stem.strip(), flags=re.IGNORECASE)
    numbers = re.findall(r"\d+", stem)
    if len(numbers) < 2:
        return None

    if numbers[0] == "21" and len(numbers) >= 3:
        target = normalize_code_part(numbers[2])
        subtarget = normalize_code_part(numbers[3]) if len(numbers) >= 4 and len(numbers[3]) == 4 else None
        return ParsedLabel(
            section=None,
            target=target,
            subtarget=subtarget,
            raw_numbers=numbers,
            mode="batch_21",
        )

    if len(numbers) >= 3 and 1 <= int(numbers[1]) <= 5 and int(numbers[2]) >= 100:
        target = normalize_code_part(numbers[2])
        subtarget = normalize_code_part(numbers[3]) if len(numbers) >= 4 and len(numbers[3]) == 4 else None
        return ParsedLabel(
            section=normalize_code_part(numbers[0]),
            target=target,
            subtarget=subtarget,
            raw_numbers=numbers,
            mode="extra_middle_marker",
        )

    target = normalize_code_part(numbers[1])
    subtarget = normalize_code_part(numbers[2]) if len(numbers) >= 3 and len(numbers[2]) == 4 else None
    return ParsedLabel(
        section=normalize_code_part(numbers[0]),
        target=target,
        subtarget=subtarget,
        raw_numbers=numbers,
        mode="section_target",
    )


def resolve_label(label: ParsedLabel, classifier_entries: list[dict]) -> tuple[dict | None, list[dict]]:
    """Resolve a parsed label to a classifier entry.

    Without an explicit subtarget we prefer the entry whose last code part is
    the target. That avoids turning 02-325 into an arbitrary child code.
    """
    matches = []
    for entry in classifier_entries:
        code = entry.get("code", "")
        parts = code.split(".")
        if label.section and not code.startswith(label.section + "."):
            continue

        if label.subtarget:
            if len(parts) >= 2 and parts[-2] == label.target and parts[-1] == label.subtarget:
                matches.append(entry)
        elif parts and parts[-1] == label.target:
            matches.append(entry)

    if not matches:
        return None, []

    matches.sort(key=lambda item: (int(item.get("children_count", 0)), -int(item.get("level", 0))))
    return matches[0], matches


def extract_docx_text(path: Path) -> str:
    with ZipFile(path) as archive:
        xml_bytes = archive.read("word/document.xml")

    root = ET.fromstring(xml_bytes)
    paragraphs: list[str] = []
    for paragraph in root.findall(".//w:p", DOCX_NS):
        text_parts = [node.text or "" for node in paragraph.findall(".//w:t", DOCX_NS)]
        text = "".join(text_parts).strip()
        if text:
            paragraphs.append(text)
    return "\n".join(paragraphs).strip()


def detect_split(path: Path) -> str | None:
    lower_parts = [part.lower() for part in path.parts]
    if any(TRAIN_MARKER in part for part in lower_parts):
        return "train"
    if any(TEST_MARKER in part for part in lower_parts):
        return "test"
    return None


def iter_docx_files(source: Path) -> list[Path]:
    return sorted(
        path
        for path in source.rglob("*.docx")
        if not path.name.startswith("~$")
    )


def build_records(source: Path, classifier_entries: list[dict]) -> tuple[list[ImportRecord], list[dict]]:
    records: list[ImportRecord] = []
    errors: list[dict] = []

    for path in iter_docx_files(source):
        split = detect_split(path)
        if not split:
            continue

        parsed = parse_filename_label(path)
        if parsed is None:
            errors.append({"file": str(path), "error": "cannot_parse_filename"})
            continue

        code_entry, matches = resolve_label(parsed, classifier_entries)
        if code_entry is None:
            errors.append({
                "file": str(path),
                "error": "classifier_code_not_found",
                "parsed_label": asdict(parsed),
            })
            continue

        try:
            text = extract_docx_text(path)
        except Exception as exc:  # pragma: no cover - defensive reporting path
            errors.append({"file": str(path), "error": "docx_extract_failed", "details": str(exc)})
            continue

        if not text:
            errors.append({"file": str(path), "error": "empty_text"})
            continue

        record_id = f"ii25_{split}_{len([r for r in records if r.split == split]) + 1:03d}"
        records.append(
            ImportRecord(
                id=record_id,
                split=split,
                source_file=path.name,
                source_path=str(path),
                appeal_text=text,
                assigned_code=code_entry["code"],
                code_name=code_entry.get("name", ""),
                full_path=code_entry.get("full_path", ""),
                level=int(code_entry.get("level", 0)),
                parent_code=code_entry.get("parent_code") or "",
                parsed_label={
                    **asdict(parsed),
                    "match_count": len(matches),
                },
            )
        )

    return records, errors


def write_jsonl(path: Path, records: list[ImportRecord]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        for record in records:
            f.write(json.dumps(asdict(record), ensure_ascii=False) + "\n")


def build_report(records: list[ImportRecord], errors: list[dict], source: Path) -> dict:
    by_split = Counter(record.split for record in records)
    by_l1 = Counter(record.assigned_code.split(".")[0] for record in records)
    by_code = Counter(record.assigned_code for record in records)
    lengths = [len(record.appeal_text) for record in records]

    return {
        "source": str(source),
        "total_records": len(records),
        "errors_count": len(errors),
        "by_split": dict(sorted(by_split.items())),
        "by_l1": dict(sorted(by_l1.items())),
        "unique_codes": len(by_code),
        "duplicate_codes": {
            code: count
            for code, count in sorted(by_code.items(), key=lambda item: (-item[1], item[0]))
            if count > 1
        },
        "text_length": {
            "min": min(lengths) if lengths else 0,
            "max": max(lengths) if lengths else 0,
            "avg": round(sum(lengths) / len(lengths), 1) if lengths else 0,
        },
        "errors": errors,
    }


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Import II25 DOCX dataset to JSONL.")
    parser.add_argument("--source", type=Path, default=DEFAULT_SOURCE, help="Path to the II25 dataset directory.")
    parser.add_argument("--classifier", type=Path, default=DEFAULT_CLASSIFIER, help="Path to classifier_flat.json.")
    parser.add_argument("--train-out", type=Path, default=DEFAULT_TRAIN_OUT, help="Output train JSONL path.")
    parser.add_argument("--test-out", type=Path, default=DEFAULT_TEST_OUT, help="Output test JSONL path.")
    parser.add_argument("--report-out", type=Path, default=DEFAULT_REPORT_OUT, help="Output JSON report path.")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    source = args.source
    if not source.exists():
        print(f"Source directory not found: {source}", file=sys.stderr)
        return 2

    classifier_entries = load_classifier(args.classifier)
    records, errors = build_records(source, classifier_entries)
    train_records = [record for record in records if record.split == "train"]
    test_records = [record for record in records if record.split == "test"]

    write_jsonl(args.train_out, train_records)
    write_jsonl(args.test_out, test_records)

    report = build_report(records, errors, source)
    args.report_out.parent.mkdir(parents=True, exist_ok=True)
    args.report_out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"Imported II25 records: {len(records)}")
    print(f"  train: {len(train_records)} -> {args.train_out}")
    print(f"  test : {len(test_records)} -> {args.test_out}")
    print(f"  report: {args.report_out}")
    if errors:
        print(f"  errors: {len(errors)} (see report)")
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
