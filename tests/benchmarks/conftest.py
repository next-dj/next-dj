"""Bench-only conftest: auto-apply ``perf`` marker to everything under this tree."""

from __future__ import annotations

from pathlib import Path

import pytest


_BENCH_ROOT = Path(__file__).parent


def pytest_collection_modifyitems(config, items):
    for item in items:
        if Path(item.fspath).is_relative_to(_BENCH_ROOT):
            item.add_marker(pytest.mark.perf)
