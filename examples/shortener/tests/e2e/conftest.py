import pytest
from e2e_support.settings import CONTEXT_ARGS


def pytest_ignore_collect(config):
    """Skip the browser suite unless its plugin was asked for.

    A bare `pytest tests/` carries neither the plugin nor, usually, playwright, and
    skipping quietly beats a collection error in every other workflow.
    """
    if not config.pluginmanager.has_plugin("e2e_support.browser"):
        return True
    return None


@pytest.fixture(scope="session")
def base_url(live_server) -> str:
    """Point playwright at the live server instead of pytest-base-url's default."""
    return live_server.url


@pytest.fixture(scope="session")
def browser_context_args(browser_context_args):
    """Pin everything that would otherwise drift between machines."""
    return {**browser_context_args, **CONTEXT_ARGS}
