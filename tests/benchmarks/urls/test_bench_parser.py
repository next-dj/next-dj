"""Benchmarks for ``next.urls.parser.URLPatternParser``."""

from __future__ import annotations

import pytest

from next.urls import URLPatternParser


_SIMPLE_PATH = "blog/posts/detail"
_TYPED_PATH = "blog/[int:year]/[slug:title]/[[trail]]"
_COMPILE_ROUNDS = 500


class TestBenchURLParser:
    @pytest.mark.benchmark(group="urls.parser")
    def test_parse_simple_segment(
        self, url_parser: URLPatternParser, benchmark
    ) -> None:
        benchmark(url_parser.parse_url_pattern, _SIMPLE_PATH)

    @pytest.mark.benchmark(group="urls.parser")
    def test_parse_typed_converter(
        self, url_parser: URLPatternParser, benchmark
    ) -> None:
        benchmark(url_parser.parse_url_pattern, _TYPED_PATH)

    @pytest.mark.benchmark(group="urls.parser")
    def test_prepare_url_name(self, url_parser: URLPatternParser, benchmark) -> None:
        benchmark(url_parser.prepare_url_name, _TYPED_PATH)

    @pytest.mark.benchmark(group="urls.parser")
    def test_regex_compile_many(self, benchmark) -> None:
        def run() -> None:
            for _ in range(_COMPILE_ROUNDS):
                URLPatternParser()

        benchmark(run)
