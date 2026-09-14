import importlib.util
import warnings

import pytest
from pytest_django.live_server_helper import LiveServer

from e2e_support.settings import CONTEXT_ARGS


BROWSER_PLUGIN = "e2e_support.browser"


class BrowserSuiteSkipped(UserWarning):
    """Raised in place of a silent skip, so `-W error` can promote it."""


NO_BROWSER_STACK = (
    "playwright is not installed, so the browser suite was skipped. Install the e2e "
    "dependency group and run `make test-examples-e2e` to exercise it."
)

PLUGIN_NOT_LOADED = (
    f"playwright is installed but the {BROWSER_PLUGIN} plugin is not loaded, so the "
    f"browser suite was skipped rather than run. Run `make test-examples-e2e`, or "
    f"pass `-p {BROWSER_PLUGIN}` to pytest."
)


def ignore_browser_suite(config: pytest.Config) -> bool | None:
    """Report whether the browser suite has to be skipped, and say why out loud.

    Skipping rather than erroring lets a bare `pytest` still run the integration tests
    next door, and the warning names the reason instead of leaving an empty directory.
    """
    if config.pluginmanager.has_plugin(BROWSER_PLUGIN):
        return None
    missing_stack = importlib.util.find_spec("playwright") is None
    reason = NO_BROWSER_STACK if missing_stack else PLUGIN_NOT_LOADED
    warnings.warn(reason, BrowserSuiteSkipped, stacklevel=2)
    return True


@pytest.fixture(scope="session")
def base_url(live_server: LiveServer) -> str:
    """Point playwright at the live server instead of pytest-base-url's default."""
    return live_server.url


@pytest.fixture(scope="session")
def browser_context_args(browser_context_args: dict[str, object]) -> dict[str, object]:
    """Pin everything that would otherwise drift between machines.

    Lives here, not in the browser plugin, because `-p` registers that plugin during
    preparse, before pytest-playwright's own fixture of the same name can shadow it.
    """
    return {**browser_context_args, **CONTEXT_ARGS}
