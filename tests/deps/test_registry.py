from dataclasses import dataclass
from itertools import count

from next.deps import (
    DependencyResolver,
    ProviderRegistry,
    RegisteredParameterProvider,
    provider_registry,
)
from next.deps.markers import DependsProvider
from next.deps.signals import provider_registered
from next.forms.markers import CleanedDataProvider, FormProvider
from next.pages.context import ContextByNameProvider
from next.testing import capture_signals
from next.urls import QueryParamProvider, UrlKwargsProvider
from tests.support import DeferringProvider, restored_provider_registry


def _plain(plain) -> None:
    return None


_late_serial = count()


def _late(priority_value: int, value: object = "late") -> type:
    # A distinct qualname per class, because the registry reads a repeated
    # address as the dev reloader replacing the class that held it.
    serial = next(_late_serial)

    class Late(RegisteredParameterProvider):
        __qualname__ = f"_late.<locals>.Late{serial}"
        priority = priority_value

        def can_handle(self, param, context) -> bool:
            return param.name == "plain"

        def resolve(self, param, context) -> object:
            return value

    return Late


@dataclass
class _EqualProvider:
    """Provider whose instances compare equal, the way a dataclass one would."""

    name: str = "plain"

    def can_handle(self, param, context) -> bool:
        return param.name == self.name

    def resolve(self, param, context) -> object:
        return "EQUAL"

    def static_can_handle(self, param) -> bool | None:
        return None


def _index_of(planned: DependencyResolver, cls: type) -> int:
    return next(i for i, p in enumerate(planned._providers) if type(p) is cls)


def _declare_in_page_module(path: str) -> type:
    """Declare one provider the way the file router execs a `page.py`.

    Every page module is loaded under the one name `page_module`, so two page
    files declaring the same class name differ only by the path their bodies
    were compiled from.
    """

    def can_handle(self, param, context) -> bool:
        return False

    def resolve(self, param, context) -> object:
        return None

    can_handle.__code__ = can_handle.__code__.replace(co_filename=path)
    resolve.__code__ = resolve.__code__.replace(co_filename=path)
    return type(
        "Flag",
        (RegisteredParameterProvider,),
        {
            "__module__": "page_module",
            "__qualname__": "Flag",
            "priority": 5,
            "can_handle": can_handle,
            "resolve": resolve,
        },
    )


class TestProviderRegistry:
    """The registry keeps definition order, a version, and fires on every add."""

    def test_add_appends_bumps_and_fires(self) -> None:
        registry = ProviderRegistry()
        assert len(registry) == 0
        assert registry.version == 0
        with capture_signals(provider_registered) as recorder:
            registry.add(UrlKwargsProvider)
            registry.add(FormProvider)
        assert list(registry) == [UrlKwargsProvider, FormProvider]
        assert registry.version == 2
        assert [event.sender for event in recorder] == [UrlKwargsProvider, FormProvider]

    def test_replace_swaps_the_classes_and_moves_the_version(self) -> None:
        registry = ProviderRegistry()
        registry.add(UrlKwargsProvider)
        registry.replace([FormProvider])
        assert registry.version == 2
        assert list(registry) == [FormProvider]
        # The index went back with the list, so the address is held once.
        registry.add(FormProvider)
        assert list(registry) == [FormProvider]

    def test_subclass_definition_lands_in_the_singleton(self) -> None:
        version = provider_registry.version
        late = _late(5)
        assert list(provider_registry)[-1] is late
        assert provider_registry.version == version + 1


class TestResolverSync:
    """A resolver catches up with the registry tail on its next resolve."""

    def test_class_registered_after_the_first_resolve_is_picked_up(self) -> None:
        planned = DependencyResolver()
        assert planned.resolve_dependencies(_plain) == {"plain": None}
        version = planned._providers_version
        _late(5)
        assert planned.resolve_dependencies(_plain) == {"plain": "late"}
        assert planned._providers_version == version + 1
        assert planned._registry_seen == provider_registry.version
        assert {type(p) for p in planned._auto} == set(provider_registry)

    def test_late_class_lands_by_priority_among_auto_providers(self) -> None:
        planned = DependencyResolver()
        planned.resolve_dependencies(_plain)
        first = _late(5)
        middle = _late(35)
        last = _late(1000)
        planned.resolve_dependencies(_plain)
        assert _index_of(planned, first) < _index_of(planned, DependsProvider)
        assert (
            _index_of(planned, ContextByNameProvider)
            < _index_of(planned, middle)
            < _index_of(planned, FormProvider)
        )
        assert _index_of(planned, last) == len(planned._providers) - 1
        assert _index_of(planned, QueryParamProvider) < _index_of(planned, last)

    def test_equal_priority_keeps_definition_order(self) -> None:
        planned = DependencyResolver()
        planned.resolve_dependencies(_plain)
        first = _late(40)
        second = _late(40)
        planned.resolve_dependencies(_plain)
        assert (
            _index_of(planned, CleanedDataProvider)
            < _index_of(planned, first)
            < _index_of(planned, second)
        )

    def test_several_late_classes_sync_in_one_pass(self) -> None:
        planned = DependencyResolver()
        planned.resolve_dependencies(_plain)
        version = planned._providers_version
        _late(5, "first")
        _late(6, "second")
        assert planned.resolve_dependencies(_plain) == {"plain": "first"}
        assert planned._providers_version == version + 1

    def test_manual_prepend_stays_ahead_of_a_late_class(self) -> None:
        planned = DependencyResolver()
        stub = DeferringProvider("plain")
        planned.prepend_provider(stub)
        _late(5)
        assert planned.resolve_dependencies(_plain) == {"plain": "STUB"}
        assert planned._providers[0] is stub
        assert isinstance(planned._providers[1], RegisteredParameterProvider)
        assert planned._providers[1].priority == 5

    def test_manual_append_lands_behind_the_auto_providers(self) -> None:
        planned = DependencyResolver()
        stub = DeferringProvider("plain")
        planned.add_provider(stub)
        assert planned.resolve_dependencies(_plain) == {"plain": "STUB"}
        assert planned._providers[-1] is stub
        assert isinstance(planned._providers[0], DependsProvider)

    def test_explicit_resolver_never_reads_the_registry(self) -> None:
        planned = DependencyResolver(UrlKwargsProvider())
        assert planned.resolve_dependencies(_plain) == {"plain": None}
        _late(5)
        assert planned.resolve_dependencies(_plain) == {"plain": None}
        assert len(planned._providers) == 1
        assert planned._providers_version == 0
        assert planned._registry_seen == provider_registry.version

    def test_removing_an_auto_provider_outlives_a_registry_move(self) -> None:
        planned = DependencyResolver()
        planned.resolve_dependencies(_plain)
        depends = planned._providers[_index_of(planned, DependsProvider)]
        planned.remove_provider(depends)
        assert depends not in planned._auto
        assert all(type(p) is not DependsProvider for p in planned._providers)
        late = _late(5)
        planned.resolve_dependencies(_plain)
        # The resync rebuilds every auto instance from the registry, and the
        # class the caller took out stays out while the newcomer joins.
        assert _index_of(planned, late) == 0
        assert all(type(p) is not DependsProvider for p in planned._providers)

    def test_removing_an_auto_provider_outlives_a_reloaded_declaration(self) -> None:
        planned = DependencyResolver()
        first = _declare_in_page_module("/pages/flags/page.py")
        planned.resolve_dependencies(_plain)
        planned.remove_provider(planned._providers[_index_of(planned, first)])
        reloaded = _declare_in_page_module("/pages/flags/page.py")
        planned.resolve_dependencies(_plain)
        # The reloader mints a fresh class under the address the caller took
        # out, so the removal has to read the address rather than the class.
        assert reloaded is not first
        assert all(type(p).__qualname__ != "Flag" for p in planned._providers)

    def test_a_restore_puts_back_the_instances_the_block_replaced(self) -> None:
        singleton = RegisteredParameterProvider.resolver
        singleton.resolve_dependencies(_plain)
        before = list(singleton._providers)
        with restored_provider_registry():
            _late(5)
            singleton.resolve_dependencies(_plain)
        assert singleton._providers == before

    def test_removing_a_provider_by_equality_gives_up_no_other_instance(self) -> None:
        planned = DependencyResolver()
        first = _EqualProvider()
        second = _EqualProvider()
        planned.add_provider(first)
        planned.add_provider(second)
        planned.remove_provider(second)
        assert planned._tail == [first]
        assert planned._tail[0] is first

    def test_an_abstract_subclass_is_registered_and_never_instantiated(self) -> None:
        class Base(RegisteredParameterProvider):
            priority = 5

        assert list(provider_registry)[-1] is Base
        planned = DependencyResolver()
        assert planned.resolve_dependencies(_plain) == {"plain": None}
        assert all(type(p) is not Base for p in planned._providers)

    def test_a_class_redeclared_at_the_same_address_replaces_the_first(self) -> None:
        def declare(value: str) -> type:
            class Reloaded(RegisteredParameterProvider):
                priority = 5

                def can_handle(self, param, context) -> bool:
                    return param.name == "plain"

                def resolve(self, param, context) -> object:
                    return value

            return Reloaded

        planned = DependencyResolver()
        planned.resolve_dependencies(_plain)
        length = len(provider_registry)
        declare("first")
        second = declare("second")
        assert len(provider_registry) == length + 1
        assert list(provider_registry)[-1] is second
        assert planned.resolve_dependencies(_plain) == {"plain": "second"}

    def test_same_named_classes_from_two_files_both_register(self) -> None:
        planned = DependencyResolver()
        planned.resolve_dependencies(_plain)
        length = len(provider_registry)
        declared = [
            _declare_in_page_module(path) for path in ("/a/page.py", "/b/page.py")
        ]
        assert len(provider_registry) == length + 2
        assert [cls.__module__ for cls in declared] == ["page_module", "page_module"]
        assert [cls.__qualname__ for cls in declared] == ["Flag", "Flag"]
        assert all(cls in list(provider_registry) for cls in declared)

    def test_a_body_without_a_code_object_addresses_by_module(self) -> None:
        registry = ProviderRegistry()
        first = type("Bare", (), {})
        second = type("Bare", (), {})
        registry.add(first)
        registry.add(second)
        assert list(registry) == [second]

    def test_removing_a_provider_gives_up_every_copy_it_holds(self) -> None:
        planned = DependencyResolver()
        stub = DeferringProvider(name=None)
        planned.prepend_provider(stub)
        planned.add_provider(stub)
        planned.remove_provider(stub)
        assert planned._head == []
        assert planned._tail == []
        assert stub not in planned._providers

    def test_a_provider_resolving_while_it_is_built_terminates(self) -> None:
        planned = DependencyResolver()

        class Recursive(RegisteredParameterProvider):
            priority = 5

            def __init__(self, resolver: DependencyResolver) -> None:
                self.seen = resolver.resolve_dependencies(_plain)

            def can_handle(self, param, context) -> bool:
                return param.name == "plain"

            def resolve(self, param, context) -> object:
                return "recursive"

        assert planned.resolve_dependencies(_plain) == {"plain": "recursive"}
        built = planned._providers[_index_of(planned, Recursive)]
        assert built.seen == {"plain": None}

    def test_late_class_asking_for_the_resolver_receives_it(self) -> None:
        planned = DependencyResolver()
        planned.resolve_dependencies(_plain)

        class Wired(RegisteredParameterProvider):
            priority = 5

            def __init__(self, resolver: DependencyResolver) -> None:
                self.owner = resolver

            def can_handle(self, param, context) -> bool:
                return False

            def resolve(self, param, context) -> object:
                return None

        planned.resolve_dependencies(_plain)
        wired = planned._providers[_index_of(planned, Wired)]
        assert isinstance(wired, Wired)
        assert wired.owner is planned

    def test_singleton_sees_a_late_class_on_its_next_resolve(self) -> None:
        resolver = RegisteredParameterProvider.resolver
        assert resolver.resolve_dependencies(_plain) == {"plain": None}
        _late(5)
        assert resolver.resolve_dependencies(_plain) == {"plain": "late"}
