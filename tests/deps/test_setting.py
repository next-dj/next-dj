import inspect
from collections.abc import Generator
from types import SimpleNamespace
from typing import override
from unittest.mock import patch

import pytest
from django.core.exceptions import ImproperlyConfigured
from django.utils.functional import empty

from next.deps import Depends
from next.deps.linear import LinearDependencyResolver
from next.deps.resolver import (
    DependencyResolver,
    _configured_resolver_class,
    _holder,
    apply_resolver_setting,
    current_resolver,
    resolver,
)
from next.testing import override_next_settings, resolve_call


DEFAULT_PATH = "next.deps.DependencyResolver"
LINEAR_PATH = "next.deps.linear.LinearDependencyResolver"
WIDE_SKIP_PATH = "tests.deps.test_setting.WideSkipResolver"
SLOTTED_PATH = "tests.deps.test_setting.SlottedResolver"
BUILT_PATH = "tests.deps.test_setting.BuiltResolver"

NOT_A_CLASS = object()


class WideSkipResolver(DependencyResolver):
    """Resolver subclass that refuses a `label` parameter on top of the core list."""

    @override
    def skips(self, param: inspect.Parameter) -> bool:
        """Widen the core refusals by the name this subclass never fills."""
        return param.name == "label" or super().skips(param)


class SlottedResolver(DependencyResolver):
    """Resolver subclass declaring slots of its own."""

    __slots__ = ("extra",)


class BuiltResolver(DependencyResolver):
    """Resolver subclass that sets up a field of its own while it is built."""

    def __init__(self, *providers) -> None:
        """Note that the constructor ran, on top of the base initialisation."""
        super().__init__(*providers)
        self.badge = "built"


def widget(size: int = 3, label: str = "plain") -> dict[str, object]:
    return {"size": size, "label": label}


def greeting() -> str:
    return "hi"


def welcome(greeting: str = Depends("greeting")) -> str:
    return greeting


def apply_with(dotted: str) -> None:
    """Apply the setting as if `NEXT_FRAMEWORK` named `dotted`, without a reload."""
    named = SimpleNamespace(DEPENDENCY_RESOLVER=dotted)
    with patch("next.backends.next_framework_settings", named):
        apply_resolver_setting()


@pytest.fixture(autouse=True)
def _restored_resolver() -> Generator[None, None, None]:
    """Put the object the holder stood for back, so no swap outlives a test."""
    original = current_resolver()
    try:
        yield
    finally:
        _holder._wrapped = original


class TestConfiguredResolverClass:
    """The DEPENDENCY_RESOLVER lookup behind the singleton swap."""

    def test_default_short_circuits_the_import_helper(self) -> None:
        """The default dotted path binds the core class without importing."""
        with patch("next.backends.import_class_cached") as import_helper:
            cls = _configured_resolver_class()
        import_helper.assert_not_called()
        assert cls is DependencyResolver

    def test_invalid_dotted_path_raises_improperly_configured(self) -> None:
        """An unimportable dotted path fails loudly where it is read."""
        with pytest.raises(ImproperlyConfigured, match="could not be imported"):
            apply_with("no_such_module_zzz.Resolver")

    @pytest.mark.parametrize(
        "dotted",
        ["tests.deps.test_setting.NOT_A_CLASS", "next.deps.ProviderRegistry"],
        ids=["not_a_class", "class_not_a_resolver"],
    )
    def test_non_resolver_target_raises_improperly_configured(self, dotted) -> None:
        """Importable targets outside DependencyResolver subclasses are rejected."""
        with pytest.raises(ImproperlyConfigured, match="DependencyResolver subclass"):
            apply_with(dotted)

    def test_a_rejected_target_leaves_the_singleton_alone(self) -> None:
        """A refused class never reaches the singleton."""
        with pytest.raises(ImproperlyConfigured):
            apply_with("next.deps.ProviderRegistry")
        assert type(current_resolver()) is DependencyResolver


class TestApplyResolverSetting:
    """`apply_resolver_setting` rebuilds the singleton behind the shared holder."""

    def test_a_swap_rebuilds_the_object_behind_the_holder(self) -> None:
        """A holder imported before the swap reads the resolver in force after it."""
        before = current_resolver()
        apply_with(WIDE_SKIP_PATH)
        assert current_resolver() is not before
        assert resolver.__class__ is WideSkipResolver

    def test_an_untouched_holder_builds_the_core_resolver_on_first_read(self) -> None:
        """Nothing has to apply the setting for a holder to stand for a resolver."""
        _holder._wrapped = empty
        assert type(current_resolver()) is DependencyResolver

    def test_a_swap_runs_the_constructor_of_the_class_it_adopts(self) -> None:
        """A subclass setting up a field of its own is built, not taken on."""
        apply_with(BUILT_PATH)
        assert current_resolver().badge == "built"

    def test_swap_changes_what_the_recompiled_plan_carries(self) -> None:
        """A subclass widening `skips` drops the parameter from the next plan."""
        assert resolve_call(widget) == {"size": 3, "label": "plain"}
        apply_with(WIDE_SKIP_PATH)
        assert resolve_call(widget) == {"size": 3}

    def test_a_swap_leaves_no_memo_of_the_class_it_replaced(self) -> None:
        """Every memo the previous class filled goes with the object that took it."""
        previous = current_resolver()
        previous.register_dependency("greeting", greeting)
        try:
            assert resolve_call(welcome) == {"greeting": "hi"}
            assert previous._plan_cache
            assert previous._leaf_dependencies
            apply_with(WIDE_SKIP_PATH)
            assert not resolver._plan_cache
            assert not resolver._leaf_dependencies
        finally:
            previous.unregister_dependency("greeting")

    def test_reapplying_the_same_class_rebuilds_nothing(self) -> None:
        """A second apply of the class already in place keeps the object and its memos."""
        apply_with(WIDE_SKIP_PATH)
        built = current_resolver()
        resolve_call(widget)
        held = list(resolver._plan_cache)
        assert held
        apply_with(WIDE_SKIP_PATH)
        assert current_resolver() is built
        assert list(resolver._plan_cache) == held

    def test_returning_to_the_default_restores_the_core_class(self) -> None:
        """The default path takes a swapped singleton back to the core resolver."""
        apply_with(WIDE_SKIP_PATH)
        apply_with(DEFAULT_PATH)
        assert type(current_resolver()) is DependencyResolver
        assert resolve_call(widget) == {"size": 3, "label": "plain"}

    def test_the_linear_resolver_ships_as_a_selectable_alternative(self) -> None:
        """The reference resolver is reachable through the setting like any other."""
        apply_with(LINEAR_PATH)
        assert type(current_resolver()) is LinearDependencyResolver
        assert resolve_call(widget) == {"size": 3, "label": "plain"}
        assert not resolver._plan_cache

    def test_a_subclass_with_slots_is_built_like_any_other(self) -> None:
        """Slots of its own are no obstacle once the object is built rather than retyped."""
        apply_with(SLOTTED_PATH)
        assert type(current_resolver()) is SlottedResolver


class TestSettingsReloadWiring:
    """The `settings_reloaded` receiver applies the setting without a restart."""

    def test_override_swaps_the_class_and_restores_it_on_exit(self) -> None:
        """`override_next_settings` swaps the class for the block only."""
        with override_next_settings(DEPENDENCY_RESOLVER=WIDE_SKIP_PATH):
            assert type(current_resolver()) is WideSkipResolver
            assert resolve_call(widget) == {"size": 3}
        assert type(current_resolver()) is DependencyResolver
        assert resolve_call(widget) == {"size": 3, "label": "plain"}
