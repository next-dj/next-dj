from collections.abc import Generator

import pytest

from next.deps import DependencyResolver
from next.deps.linear import LinearDependencyResolver
from tests.support import restored_provider_registry


RESOLVER_CLASSES = (DependencyResolver, LinearDependencyResolver)


@pytest.fixture(autouse=True)
def _pristine_provider_registry() -> Generator[None, None, None]:
    """Keep provider classes declared inside a test from outliving it."""
    with restored_provider_registry():
        yield


@pytest.fixture(params=RESOLVER_CLASSES, ids=["planned", "linear"])
def resolver_class(request) -> type[DependencyResolver]:
    """Each resolution path in turn, so a case pinned once holds for both."""
    return request.param
