from collections.abc import Callable

import pytest
from flags.models import Flag
from flags.providers import WRITE_GATE_FLAG


@pytest.fixture()
def make_flag() -> Callable[..., Flag]:
    # `update_or_create` so a test can restate one of the migration-seeded flags
    # without caring whether the row already exists.
    def _make(
        name: str,
        *,
        label: str | None = None,
        description: str = "",
        enabled: bool = False,
    ) -> Flag:
        flag, _ = Flag.objects.update_or_create(
            name=name,
            defaults={
                "label": label or name.replace("_", " ").capitalize(),
                "description": description,
                "enabled": enabled,
            },
        )
        return flag

    return _make


@pytest.fixture()
def write_gate(make_flag) -> Callable[..., Flag]:
    def _set(*, enabled: bool) -> Flag:
        return make_flag(WRITE_GATE_FLAG, label="Admin writes", enabled=enabled)

    return _set


@pytest.fixture()
def no_flags() -> None:
    Flag.objects.all().delete()
