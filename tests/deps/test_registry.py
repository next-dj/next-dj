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
from tests.support import DeferringProvider


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

    def test_bump_moves_the_version_and_keeps_the_classes(self) -> None:
        registry = ProviderRegistry()
        registry.add(UrlKwargsProvider)
        registry.bump()
        assert registry.version == 2
        assert list(registry) == [UrlKwargsProvider]

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

    def test_removing_an_auto_provider_drops_it_until_the_registry_moves(self) -> None:
        planned = DependencyResolver()
        planned.resolve_dependencies(_plain)
        depends = planned._providers[_index_of(planned, DependsProvider)]
        planned.remove_provider(depends)
        assert depends not in planned._auto
        assert all(type(p) is not DependsProvider for p in planned._providers)
        late = _late(5)
        planned.resolve_dependencies(_plain)
        # The next resync rebuilds every auto instance from the registry, so
        # the removed one is back and the newcomer sits ahead of it.
        assert _index_of(planned, late) == 0
        assert _index_of(planned, DependsProvider) == 1

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
