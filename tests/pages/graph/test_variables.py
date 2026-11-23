import asyncio
import contextvars
import threading
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import pytest
from asgiref.local import Local
from django.conf import settings
from django.urls import get_script_prefix, get_urlconf, set_script_prefix, set_urlconf
from django.utils import translation

from next.deps.runner import segment_runner
from next.pages.graph import register_local_accessor, variables
from tests.support.graphs import LEAF, collect, register_two


_marker: contextvars.ContextVar[str] = contextvars.ContextVar("graph_marker")
_absent: contextvars.ContextVar[str] = contextvars.ContextVar("graph_absent")

pytestmark = pytest.mark.usefixtures("framework_providers")


@dataclass(frozen=True, slots=True)
class _Accessor:
    read: Callable[[], object]
    write: Callable[[Any], None]


@pytest.fixture()
def registered(monkeypatch: pytest.MonkeyPatch) -> None:
    """Drop the values a test registers once it ends."""
    monkeypatch.setattr(variables._registry, "accessors", variables.accessors())


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

        register_two(registry, one, two)

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

        register_two(registry, one, two)
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

        assert collect(registry).context_data["b_reader"] == "written"

    def test_an_equal_value_keeps_the_caller_object(self, registry, inline) -> None:
        original = "".join(["x"] * 40)

        async def one() -> int:
            _marker.set("".join(["x"] * 40))
            return 1

        async def two() -> int:
            return 2

        register_two(registry, one, two)

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

        register_two(registry, a_layout, b_preview)

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

        register_two(registry, one, two)

        async def main() -> str:
            translation.activate("de")
            await registry.acollect_context(LEAF)
            return translation.get_language()

        assert asyncio.run(main()) == settings.LANGUAGE_CODE

    def test_locals_first_set_by_a_callable_reach_the_caller(self, registry) -> None:
        local, other = Local(), Local()

        async def one() -> int:
            local.value = "set"
            other.value = "too"
            return 1

        async def two() -> int:
            return 2

        register_two(registry, one, two)

        async def main() -> tuple[str, str]:
            await registry.acollect_context(LEAF)
            return local.value, other.value

        assert asyncio.run(main()) == ("set", "too")

    def test_a_local_restores_whole_from_the_last_callable_that_changed_it(
        self, registry
    ) -> None:
        local = Local()

        async def one() -> int:
            local.first = "one"
            return 1

        async def two() -> int:
            await asyncio.sleep(0.002)
            local.second = "two"
            return 2

        register_two(registry, one, two)

        async def main() -> tuple[object, object]:
            await registry.acollect_context(LEAF)
            return getattr(local, "first", None), local.second

        assert asyncio.run(main()) == (None, "two")

    def test_a_sync_callable_on_another_thread_reads_a_local_of_the_caller(
        self, registry
    ) -> None:
        local = Local()

        async def one() -> int:
            return 1

        async def two() -> int:
            return 2

        def reader() -> object:
            return getattr(local, "value", None), threading.get_ident()

        register_two(registry, one, two)
        registry.register_context(LEAF, "reader", reader)

        async def main() -> tuple[object, bool]:
            local.value = "set"
            result = await registry.acollect_context(LEAF)
            value, ident = result.context_data["reader"]
            return value, ident != threading.get_ident()

        assert asyncio.run(main()) == ("set", True)

    def test_a_local_a_sync_callable_wrote_on_another_thread_reaches_the_caller(
        self, registry
    ) -> None:
        local = Local()

        async def one() -> int:
            return 1

        async def two() -> int:
            return 2

        def writer() -> int:
            local.value = "written"
            return 3

        register_two(registry, one, two)
        registry.register_context(LEAF, "writer", writer)

        async def main() -> object:
            local.value = "start"
            await registry.acollect_context(LEAF)
            return local.value

        assert asyncio.run(main()) == "written"

    def test_an_urlconf_and_a_prefix_keep_what_an_earlier_callable_set(
        self, registry
    ) -> None:
        async def a_layout() -> int:
            await asyncio.sleep(0.002)
            set_urlconf("layout.urls")
            set_script_prefix("/layout/")
            return 1

        async def b_preview() -> int:
            urlconf, prefix = get_urlconf(), get_script_prefix()
            set_urlconf("preview.urls")
            set_script_prefix("/preview/")
            set_urlconf(urlconf)
            set_script_prefix(prefix)
            return 2

        register_two(registry, a_layout, b_preview)

        async def main() -> tuple[object, str]:
            await registry.acollect_context(LEAF)
            return get_urlconf(), get_script_prefix()

        assert asyncio.run(main()) == ("layout.urls", "/layout/")

    def test_a_deactivation_keeps_against_a_later_override_restored(
        self, registry
    ) -> None:
        async def a_layout() -> int:
            await asyncio.sleep(0.002)
            translation.deactivate_all()
            return 1

        async def b_preview() -> int:
            with translation.override("fr"):
                pass
            return 2

        register_two(registry, a_layout, b_preview)

        async def main() -> object:
            translation.activate("en")
            await registry.acollect_context(LEAF)
            return translation.get_language()

        assert asyncio.run(main()) is None

    def test_a_runner_that_keeps_its_copy_keeps_the_sync_writes(self, registry) -> None:
        async def one() -> int:
            return 1

        async def two() -> int:
            return 2

        def writer() -> int:
            _absent.set("written")
            return 3

        register_two(registry, one, two)
        registry.register_context(LEAF, "writer", writer)

        async def copied(segment):
            return await asyncio.to_thread(segment)

        async def main() -> str:
            segment_runner.set(copied)
            await registry.acollect_context(LEAF)
            return _absent.get("unset")

        assert asyncio.run(main()) == "unset"


class TestLocalAccessors:
    """A registered value keeps what the last callable that changed it set."""

    def test_a_registered_local_keeps_against_a_later_override_restored(
        self, registry, registered
    ) -> None:
        tenant = Local()
        register_local_accessor(
            _Accessor(
                lambda: getattr(tenant, "name", None),
                lambda name: setattr(tenant, "name", name),
            )
        )

        async def a_layout() -> int:
            await asyncio.sleep(0.002)
            tenant.name = "acme"
            return 1

        async def b_preview() -> int:
            saved = tenant.name
            tenant.name = "preview"
            tenant.name = saved
            return 2

        register_two(registry, a_layout, b_preview)

        async def main() -> object:
            tenant.name = "start"
            await registry.acollect_context(LEAF)
            return tenant.name

        assert asyncio.run(main()) == "acme"

    def test_an_unregistered_local_takes_the_restored_value(self, registry) -> None:
        tenant = Local()

        async def a_layout() -> int:
            await asyncio.sleep(0.002)
            tenant.name = "acme"
            return 1

        async def b_preview() -> int:
            saved = tenant.name
            tenant.name = "preview"
            tenant.name = saved
            return 2

        register_two(registry, a_layout, b_preview)

        async def main() -> object:
            tenant.name = "start"
            await registry.acollect_context(LEAF)
            return tenant.name

        assert asyncio.run(main()) == "start"

    def test_a_layer_without_writes_reads_nothing(self, registry, registered) -> None:
        read: list[int] = []
        register_local_accessor(_Accessor(lambda: read.append(1), lambda _value: None))

        async def one() -> int:
            return 1

        async def two() -> int:
            return 2

        def three() -> int:
            return 3

        register_two(registry, one, two)
        registry.register_context(LEAF, "three", three)
        collect(registry)
        assert read == []

    def test_a_layer_with_a_write_reads_each_writer_and_its_start(
        self, registry, registered
    ) -> None:
        read: list[int] = []
        register_local_accessor(_Accessor(lambda: read.append(1), lambda _value: None))

        async def one() -> int:
            _marker.set("one")
            return 1

        async def two() -> int:
            return 2

        register_two(registry, one, two)
        collect(registry)
        assert len(read) == 3

    def test_a_deactivation_restores_no_override_against_a_later_one_restored(
        self, registry, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        async def a_layout() -> int:
            await asyncio.sleep(0.002)
            translation.deactivate()
            return 1

        async def b_preview() -> int:
            with translation.override("fr"):
                pass
            return 2

        register_two(registry, a_layout, b_preview)

        async def main() -> str:
            translation.activate("de")
            await registry.acollect_context(LEAF)
            monkeypatch.setattr(settings, "LANGUAGE_CODE", "nl")
            return translation.get_language()

        assert asyncio.run(main()) == "nl"
