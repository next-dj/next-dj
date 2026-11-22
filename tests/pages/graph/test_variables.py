import asyncio
import contextvars
from pathlib import Path

import pytest
from asgiref.local import Local
from django.conf import settings
from django.utils import translation

from next.pages.registry import PageContextRegistry


LEAF = Path("/graph/mid/leaf/page.py")

_marker: contextvars.ContextVar[str] = contextvars.ContextVar("graph_marker")
_absent: contextvars.ContextVar[str] = contextvars.ContextVar("graph_absent")

pytestmark = pytest.mark.usefixtures("framework_providers")


def _collect(registry: PageContextRegistry, file_path: Path = LEAF, **kwargs):
    """Run `acollect_context` of `file_path` on a fresh loop."""
    return asyncio.run(registry.acollect_context(file_path, **kwargs))


def _two_async(registry: PageContextRegistry, one, two) -> None:
    registry.register_context(LEAF, "one", one)
    registry.register_context(LEAF, "two", two)


class TestWrites:
    """A write to a context variable reaches the later callables and the caller."""

    def test_a_sync_write_reaches_a_later_callable_and_the_caller(
        self, registry
    ) -> None:
        def activate() -> int:
            translation.activate("fr")
            return 1

        async def reader(activate: int) -> str:
            return translation.get_language()

        registry.register_context(LEAF, "activate", activate)
        registry.register_context(LEAF, "reader", reader)

        async def main() -> tuple[str, str]:
            translation.activate("en")
            result = await registry.acollect_context(LEAF)
            return result.context_data["reader"], translation.get_language()

        assert asyncio.run(main()) == ("fr", "fr")

    def test_writes_restore_in_declaration_order(self, registry, inline) -> None:
        async def one() -> int:
            await asyncio.sleep(0.002)
            _marker.set("one")
            return 1

        async def two() -> int:
            _marker.set("two")
            _absent.set("two")
            return 2

        _two_async(registry, one, two)

        async def main() -> tuple[str, str]:
            _marker.set("start")
            await registry.acollect_context(LEAF)
            return _marker.get(), _absent.get()

        assert asyncio.run(main()) == ("two", "two")

    def test_a_layout_language_reaches_the_caller(self, registry) -> None:
        async def one() -> int:
            return 1

        async def two() -> int:
            return 2

        def language() -> int:
            translation.activate("de")
            return 3

        _two_async(registry, one, two)
        registry.register_context(LEAF, "language", language)

        async def main() -> str:
            translation.activate("en")
            await registry.acollect_context(LEAF)
            return translation.get_language()

        assert asyncio.run(main()) == "de"

    def test_a_callable_sees_the_writes_of_its_dependency(
        self, registry, inline
    ) -> None:
        async def a_one() -> int:
            _marker.set("written")
            return 1

        async def a_two() -> int:
            return 2

        async def b_reader(a_one: int) -> str:
            return _marker.get("unset")

        registry.register_context(LEAF, "a_one", a_one)
        registry.register_context(LEAF, "a_two", a_two)
        registry.register_context(LEAF, "b_reader", b_reader)

        assert _collect(registry).context_data["b_reader"] == "written"

    def test_an_equal_value_keeps_the_caller_object(self, registry, inline) -> None:
        original = "".join(["x"] * 40)

        async def one() -> int:
            _marker.set("".join(["x"] * 40))
            return 1

        async def two() -> int:
            return 2

        _two_async(registry, one, two)

        async def main() -> bool:
            _marker.set(original)
            await registry.acollect_context(LEAF)
            return _marker.get() is original

        assert asyncio.run(main()) is True

    def test_a_local_keeps_the_attribute_a_later_override_restored(
        self, registry
    ) -> None:
        async def a_layout() -> int:
            await asyncio.sleep(0.002)
            translation.activate("de")
            return 1

        async def b_preview() -> int:
            with translation.override("fr"):
                pass
            return 2

        _two_async(registry, a_layout, b_preview)

        async def main() -> str:
            translation.activate("en")
            await registry.acollect_context(LEAF)
            return translation.get_language()

        assert asyncio.run(main()) == "de"

    def test_a_local_drops_the_attribute_a_callable_deleted(self, registry) -> None:
        async def one() -> int:
            translation.deactivate()
            return 1

        async def two() -> int:
            return 2

        _two_async(registry, one, two)

        async def main() -> str:
            translation.activate("de")
            await registry.acollect_context(LEAF)
            return translation.get_language()

        assert asyncio.run(main()) == settings.LANGUAGE_CODE

    def test_a_local_first_set_by_a_callable_reaches_the_caller(self, registry) -> None:
        local = Local()

        async def one() -> int:
            local.value = "set"
            return 1

        async def two() -> int:
            return 2

        _two_async(registry, one, two)

        async def main() -> str:
            await registry.acollect_context(LEAF)
            return local.value

        assert asyncio.run(main()) == "set"
