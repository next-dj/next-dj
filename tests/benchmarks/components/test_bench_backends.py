"""Benchmarks for ``next.components.scanner.ComponentScanner``."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from next.components.scanner import ComponentScanner
from tests.benchmarks.factories import build_component_djx_dir


if TYPE_CHECKING:
    from pathlib import Path


class TestBenchComponentScanner:
    @pytest.mark.benchmark(group="components.discovery")
    def test_scan_small(self, tmp_path: Path, benchmark) -> None:
        build_component_djx_dir(tmp_path, 10)
        scanner = ComponentScanner()
        benchmark(scanner.scan_directory, tmp_path, tmp_path, "")

    @pytest.mark.benchmark(group="components.discovery")
    def test_scan_large(self, tmp_path: Path, benchmark) -> None:
        build_component_djx_dir(tmp_path, 500)
        scanner = ComponentScanner()
        benchmark(scanner.scan_directory, tmp_path, tmp_path, "")
