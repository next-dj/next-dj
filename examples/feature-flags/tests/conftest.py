from collections.abc import Callable

import pytest
from flags.demo import seed_demo
from flags.models import Flag
from flags.providers import WRITE_GATE_FLAG


@pytest.fixture()
def demo_data(db) -> None:
    """Seed the shipped demo flags for tests that read them."""
    seed_demo()


@pytest.fixture()
def make_flag() -> Callable[..., Flag]:
    def _make(
        name: str,
        *,
        label: str | None = None,
        description: str = "",
        enabled: bool = False,
    ) -> Flag:
        return Flag.objects.create(
            name=name,
            label=label or name.replace("_", " ").capitalize(),
            description=description,
            enabled=enabled,
        )

    return _make


@pytest.fixture()
def write_gate(make_flag) -> Callable[..., Flag]:
    def _set(*, enabled: bool) -> Flag:
        return make_flag(WRITE_GATE_FLAG, label="Admin writes", enabled=enabled)

    return _set
