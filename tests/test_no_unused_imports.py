"""Остатки монолита: неиспользуемые импорты верхнего уровня в модулях API и агента."""

import ast
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parents[1] / "src"

# Импорты ради побочного эффекта или подменяемые тестами.
ALLOWED = {
    "classifier_agent.py": {"env_bootstrap", "EMBEDDING_MODEL"},
}


def unused_imports(path: Path) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    imported = {}
    for node in tree.body:
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            for alias in node.names:
                imported[(alias.asname or alias.name).split(".")[0]] = node.lineno
    used = {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)}
    return sorted(name for name in imported if name not in used)


@pytest.mark.parametrize("name", ["api_server.py", "integration_api.py", "classifier_agent.py"])
def test_no_unused_module_level_imports(name):
    unused = set(unused_imports(SRC / name)) - ALLOWED.get(name, set())
    assert not unused, f"{name}: неиспользуемые импорты {sorted(unused)}"
