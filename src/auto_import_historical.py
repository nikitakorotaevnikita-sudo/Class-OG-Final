"""auto_import_historical.py — Автоматический импорт исторических данных

Сканирует папку data/historical/ на предмет новых файлов (.xlsx, .xls, .csv, .json),
парсит и валидирует их, сохраняет валидные записи в historical_verified.jsonl,
перемещает обработанные файлы в processed/, а файлы с ошибками — в errors/.

Usage:
    python src/auto_import_historical.py              # однократный прогон
    python src/auto_import_historical.py --watch      # режим наблюдения (poll every 30s)
    python src/auto_import_historical.py --watch --interval 60   # custom interval
"""

import argparse
import json
import shutil
import sys
import time
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from historical_loader import parse_file, validate_codes, save_to_historical_jsonl, ValidationResult

HISTORICAL_DIR = Path("data/historical")
PROCESSED_DIR = HISTORICAL_DIR / "processed"
ERRORS_DIR = HISTORICAL_DIR / "errors"
SUPPORTED_EXTENSIONS = {".xlsx", ".xls", ".csv", ".json"}


def ensure_dirs():
    """Создаёт processed/ и errors/ если их ещё нет."""
    PROCESSED_DIR.mkdir(parents=True, exist_ok=True)
    ERRORS_DIR.mkdir(parents=True, exist_ok=True)


def scan_historical_dir() -> list[Path]:
    """Возвращает список файлов ожидающих обработки."""
    files = []
    if not HISTORICAL_DIR.exists():
        return files
    for item in HISTORICAL_DIR.iterdir():
        if item.is_file() and item.suffix.lower() in SUPPORTED_EXTENSIONS:
            files.append(item)
    return sorted(files, key=lambda p: p.stat().st_mtime)


def get_timestamp_prefix() -> str:
    return datetime.now().strftime("%Y-%m-%d_%H-%M-%S")


def move_to_processed(file_path: Path):
    timestamp = get_timestamp_prefix()
    dest = PROCESSED_DIR / f"{timestamp}_{file_path.name}"
    shutil.move(str(file_path), str(dest))
    return dest


def move_to_errors(file_path: Path):
    timestamp = get_timestamp_prefix()
    dest = ERRORS_DIR / f"{timestamp}_{file_path.name}"
    shutil.move(str(file_path), str(dest))
    return dest


def create_error_report(file_path: Path, validation_result: ValidationResult) -> Path:
    """Создаёт JSON-отчёт об ошибках валидации."""
    report = {
        "processed_at": datetime.now().isoformat(),
        "source_file": file_path.name,
        "stats": validation_result.stats,
        "errors": validation_result.errors,
        "saved_to": None  # no valid records were saved
    }
    report_path = ERRORS_DIR / f"{get_timestamp_prefix()}_{file_path.stem}_report.json"
    with open(report_path, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
    return report_path


def process_file(file_path: Path) -> dict:
    """
    Парсит, валидирует файл и раскладывает по папкам.

    Returns:
        dict с ключами: success (bool), source_file, stats, saved_to, report_path
    """
    result = {
        "success": False,
        "source_file": file_path.name,
        "stats": {"total": 0, "valid": 0, "invalid": 0},
        "saved_to": None,
        "report_path": None
    }

    try:
        records = parse_file(str(file_path))
    except Exception as e:
        # Фатальная ошибка парсинга — создаём отчёт и кидаем в errors
        report = {
            "processed_at": datetime.now().isoformat(),
            "source_file": file_path.name,
            "stats": {"total": 0, "valid": 0, "invalid": 0},
            "errors": [{"row": 0, "code": "", "error": f"Parse error: {str(e)}"}],
            "saved_to": None
        }
        report_path = ERRORS_DIR / f"{get_timestamp_prefix()}_{file_path.stem}_report.json"
        with open(report_path, "w", encoding="utf-8") as f:
            json.dump(report, f, ensure_ascii=False, indent=2)
        move_to_errors(file_path)
        result["report_path"] = str(report_path)
        return result

    validation_result = validate_codes(records)
    result["stats"] = validation_result.stats

    if validation_result.valid_records > 0:
        saved_to = save_to_historical_jsonl(validation_result.valid_records, file_path.name)
        result["saved_to"] = saved_to
        result["success"] = True

    if validation_result.errors > 0:
        report_path = create_error_report(file_path, validation_result)
        result["report_path"] = str(report_path)

    if validation_result.errors > 0:
        move_to_errors(file_path)
    else:
        move_to_processed(file_path)

    return result


def run_once(verbose: bool = True) -> list[dict]:
    """Однократный прогон — обрабатывает все файлы в historical/."""
    ensure_dirs()
    files = scan_historical_dir()

    if not files:
        if verbose:
            print("No files to process in data/historical/")
        return []

    results = []
    for file_path in files:
        if verbose:
            print(f"Processing: {file_path.name} ...")
        result = process_file(file_path)
        results.append(result)

        if verbose:
            stats = result["stats"]
            print(f"  -> total={stats['total']}, valid={stats['valid']}, invalid={stats['invalid']}")
            if result["saved_to"]:
                print(f"  -> saved to: {result['saved_to']}")
            if result["report_path"]:
                print(f"  -> error report: {result['report_path']}")

    return results


def run_watch(interval: int = 30, verbose: bool = True):
    """Режим наблюдения — сканирует папку каждые interval секунд."""
    if verbose:
        print(f"Watching data/historical/ (interval={interval}s). Press Ctrl+C to stop.")

    ensure_dirs()
    processed_names: set[str] = set()

    while True:
        files = scan_historical_dir()

        # Пропускаем файлы которые уже обработаны в этом цикле
        new_files = [f for f in files if f.name not in processed_names]

        for file_path in new_files:
            if verbose:
                print(f"\n[{datetime.now().strftime('%H:%M:%S')}] Processing: {file_path.name} ...")
            result = process_file(file_path)
            processed_names.add(file_path.name)

            if verbose:
                stats = result["stats"]
                print(f"  -> total={stats['total']}, valid={stats['valid']}, invalid={stats['invalid']}")
                if result["saved_to"]:
                    print(f"  -> saved to: {result['saved_to']}")
                if result["report_path"]:
                    print(f"  -> error report: {result['report_path']}")
                print(f"  -> moved to: {'processed/' if result['success'] else 'errors/'}")

        time.sleep(interval)


def main():
    parser = argparse.ArgumentParser(
        description="Auto-import historical appeals from data/historical/"
    )
    parser.add_argument(
        "--watch",
        action="store_true",
        help="Watch mode: continuously scan folder for new files"
    )
    parser.add_argument(
        "--interval",
        type=int,
        default=30,
        help="Poll interval in seconds for watch mode (default: 30)"
    )
    args = parser.parse_args()

    ensure_dirs()

    if args.watch:
        run_watch(interval=args.interval)
    else:
        results = run_once()
        if not results:
            sys.exit(0)
        # Exit with error code if any file had errors
        has_errors = any(r["stats"]["invalid"] > 0 for r in results)
        sys.exit(1 if has_errors else 0)


if __name__ == "__main__":
    main()