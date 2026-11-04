"""xlsx-шаблон ручной проверки агента «Классификатор обращений» в ArioCowork.

    venv\\Scripts\\python scripts\\make_cowork_check_template.py [summary_xlsx] [out]

16 обращений: 11 из выборки заказчика (tests/fixtures/test_appeals_real12.json)
и 5 синтетических ветеранских. Для каждого — эталон, текст для вставки в чат
агента, итог пайплайна (если передан summary_xlsx — сводка замеров
eval_results/e2e_summary_all.xlsx) и пустые колонки для вариантов A и B.

Тексты обращений — персональные данные: в шаблон копируются, в консоль — нет.
"""

import json
import sys
from pathlib import Path

from openpyxl import Workbook, load_workbook
from openpyxl.styles import Alignment, Font, PatternFill

ROOT = Path(__file__).resolve().parent.parent
FIXTURE = ROOT / "tests" / "fixtures" / "test_appeals_real12.json"
DEFAULT_OUT = ROOT / "eval_results" / "cowork_manual_check.xlsx"
SUMMARY_SHEET = "По обращениям"
SUMMARY_COLUMN = "Qwen3 · предобработка replace"

VET_CODE = "0004.0015.0158.0974"
# Синтетические тексты, на них же шли замеры 22.10.2026.
VET_TEXTS = [
    "Прошу оказать содействие в получении удостоверения ветерана боевых действий. "
    "Мой сын участвовал в специальной военной операции.",
    "Не выплачены единовременные выплаты за участие в специальной военной операции. "
    "Прошу разобраться и произвести выплату.",
    "Прошу выдать удостоверение ветерана боевых действий, документы сданы в военкомат "
    "полгода назад, ответа нет.",
    "Муж вернулся из зоны СВО, выплаты за участие в боевых действиях до сих пор не получены.",
    "Отказали в присвоении статуса ветерана боевых действий, прошу пересмотреть решение.",
]

PER_VARIANT = ["Коды агента", "Совпало с эталоном", "Время, с", "История использована",
               "Коды вне классификатора", "Комментарий"]
HEADERS = (["id", "Набор", "Эталонные коды", "Текст для вставки",
            "Пайплайн (Qwen3 + предобработка)"]
           + [f"{v}: {h}" for v in ("A", "B") for h in PER_VARIANT])


def _cases() -> list[tuple[str, str, list[str], str]]:
    real = json.loads(FIXTURE.read_text(encoding="utf-8"))
    cases = [(c["id"], "выборка заказчика", c["expected_codes"], c["text"]) for c in real]
    cases += [(f"vet{i + 1}", "ветераны (синтетика)", [VET_CODE], t)
              for i, t in enumerate(VET_TEXTS)]
    return cases


def _pipeline_marks(summary_xlsx: Path) -> dict[str, str]:
    ws = load_workbook(summary_xlsx, read_only=True)[SUMMARY_SHEET]
    rows = ws.iter_rows(values_only=True)
    header = list(next(rows))
    col = header.index(SUMMARY_COLUMN)
    return {r[0]: r[col] for r in rows if r and r[0]}


def make_template(out: Path, summary_xlsx: Path | None = None) -> int:
    marks = _pipeline_marks(summary_xlsx) if summary_xlsx else {}
    wb = Workbook()
    ws = wb.active
    ws.title = "Проверка"
    ws.append(HEADERS)
    cases = _cases()
    for case_id, dataset, expected, text in cases:
        ws.append([case_id, dataset, "\n".join(expected), text, marks.get(case_id, "")]
                  + [""] * (len(HEADERS) - 5))

    bold, fill = Font(bold=True), PatternFill("solid", fgColor="DDEBF7")
    for cell in ws[1]:
        cell.font, cell.fill = bold, fill
    for row in ws.iter_rows():
        for cell in row:
            cell.alignment = Alignment(wrap_text=True, vertical="top")
    widths = {"A": 10, "B": 20, "C": 22, "D": 60, "E": 22}
    for col in ws.columns:
        letter = col[0].column_letter
        ws.column_dimensions[letter].width = widths.get(letter, 16)
    ws.freeze_panes = "B2"

    out.parent.mkdir(parents=True, exist_ok=True)
    wb.save(out)
    return len(cases)


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    summary = Path(sys.argv[1]) if len(sys.argv) > 1 else None
    target = Path(sys.argv[2]) if len(sys.argv) > 2 else DEFAULT_OUT
    if summary and not summary.exists():
        sys.exit(f"Нет файла сводки: {summary}")
    count = make_template(target, summary)
    print(f"Шаблон: {target} — обращений: {count}")
