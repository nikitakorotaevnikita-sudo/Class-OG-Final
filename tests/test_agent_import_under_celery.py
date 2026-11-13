"""classifier_agent импортируется лениво внутри задачи Celery-воркера.

Celery подменяет sys.stdout/sys.stderr на LoggingProxy, у которого нет
reconfigure(). Импорт модуля не должен падать на такой подмене — иначе
каждая задача классификации завершается ошибкой AttributeError.
"""

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

SCRIPT = r"""
import sys
sys.path.insert(0, 'src')

class Proxy:
    # Как celery.utils.log.LoggingProxy: write/flush есть, reconfigure нет.
    encoding = 'utf-8'
    def write(self, s): return len(s)
    def flush(self): pass
    def isatty(self): return False

import os, traceback
sys.stdout = Proxy()
sys.stderr = Proxy()
try:
    from classifier import classifier_agent  # noqa: F401
except BaseException:
    os.write(2, traceback.format_exc().encode('utf-8'))
    raise
os.write(1, b'IMPORT_OK\n')
"""


def test_classifier_agent_imports_when_stdout_has_no_reconfigure():
    proc = subprocess.run([sys.executable, "-c", SCRIPT], cwd=str(ROOT),
                          capture_output=True, text=True, encoding="utf-8",
                          errors="replace", timeout=300)
    assert "IMPORT_OK" in proc.stdout, proc.stderr[-2000:]
