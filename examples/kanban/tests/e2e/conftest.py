import pytest
from e2e_support.suite import base_url, browser_context_args, ignore_browser_suite


__all__ = ["base_url", "browser_context_args"]


def pytest_ignore_collect(config: pytest.Config) -> bool | None:
    return ignore_browser_suite(config)
