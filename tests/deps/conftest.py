from collections.abc import Generator

import pytest

from tests.support import restored_provider_registry


@pytest.fixture(autouse=True)
def _pristine_provider_registry() -> Generator[None, None, None]:
    """Keep provider classes declared inside a test from outliving it."""
    with restored_provider_registry():
        yield
