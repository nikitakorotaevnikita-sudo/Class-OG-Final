"""conftest.py — общие fixtures для pytest."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))


def pytest_configure(config):
    """Ensure api_server imports work without triggering ML startup."""
    pass