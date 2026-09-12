import inspect
from collections.abc import Generator
from types import SimpleNamespace
from typing import override
from unittest.mock import patch

import pytest
from django.core.exceptions import ImproperlyConfigured

from next.deps import Depends
from next.deps.resolver import (
    DependencyResolver,
    _configured_resolver_class,
    apply_resolver_setting,
    resolver,
)
from next.testing import override_next_settings, resolve_call


DEFAULT_PATH = "next.deps.DependencyResolver"
WIDE_SKIP_PATH = "tests.deps.test_setting.WideSkipResolver"
SLOTTED_PATH = "tests.deps.test_setting.SlottedResolver"

NOT_A_CLASS = object()


class WideSkipResolver(DependencyResolver):
    """Resolver subclass that refuses a `label` parameter on top of the core list."""

    @override
    def skips(self, param: inspect.Parameter) -> bool:
        """Widen the core refusals by the name this subclass never fills."""
        return param.name == "label" or super().skips(param)


class SlottedResolver(DependencyResolver):
    """Resolver subclass whose own slots give it an incompatible object layout."""

    __slots__ = ("extra",)


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
def _restored_resolver_class() -> Generator[None, None, None]:
    """Put the resolver class and its memos back, so no swap outlives a test."""
    original = type(resolver)
    try:
        yield
    finally:
        resolver.__class__ = original
        resolver._providers_version += 1
        resolver._plan_cache.clear()
        resolver._leaf_dependencies.clear()


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
        assert type(resolver) is DependencyResolver


class TestApplyResolverSetting:
    """`apply_resolver_setting` retypes the singleton and drops its memos."""

    def test_swap_keeps_the_object_and_changes_its_class(self) -> None:
        """The singleton keeps its identity while its class moves."""
        before = resolver
        apply_with(WIDE_SKIP_PATH)
        assert resolver is before
        assert type(resolver) is WideSkipResolver

    def test_swap_changes_what_the_recompiled_plan_carries(self) -> None:
        """A subclass widening `skips` drops the parameter from the next plan."""
        assert resolve_call(widget) == {"size": 3, "label": "plain"}
        apply_with(WIDE_SKIP_PATH)
        assert resolve_call(widget) == {"size": 3}

    def test_swap_empties_the_plan_and_leaf_memos(self) -> None:
        """Both caches the previous class filled are dropped by the swap."""
        resolver.register_dependency("greeting", greeting)
        try:
            assert resolve_call(welcome) == {"greeting": "hi"}
            assert resolver._plan_cache
            assert resolver._leaf_dependencies
            apply_with(WIDE_SKIP_PATH)
            assert dict(resolver._plan_cache) == {}
            assert resolver._leaf_dependencies == {}
        finally:
            resolver.unregister_dependency("greeting")

    def test_swap_moves_the_providers_version(self) -> None:
        """A plan held outside the cache is stamped stale by the swap."""
        before = resolver._providers_version
        apply_with(WIDE_SKIP_PATH)
        assert resolver._providers_version > before

    def test_reapplying_the_same_class_invalidates_nothing(self) -> None:
        """A second apply of the class already in place leaves the memos intact."""
        apply_with(WIDE_SKIP_PATH)
        resolve_call(widget)
        version = resolver._providers_version
        cached = dict(resolver._plan_cache)
        assert cached
        apply_with(WIDE_SKIP_PATH)
        assert resolver._providers_version == version
        assert dict(resolver._plan_cache) == cached

    def test_returning_to_the_default_restores_the_core_class(self) -> None:
        """The default path takes a swapped singleton back to the core resolver."""
        apply_with(WIDE_SKIP_PATH)
        apply_with(DEFAULT_PATH)
        assert type(resolver) is DependencyResolver
        assert resolve_call(widget) == {"size": 3, "label": "plain"}

    def test_incompatible_layout_raises_improperly_configured(self) -> None:
        """A subclass declaring slots cannot be taken on in place."""
        with pytest.raises(ImproperlyConfigured, match="object layout"):
            apply_with(SLOTTED_PATH)
        assert type(resolver) is DependencyResolver


class TestSettingsReloadWiring:
    """The `settings_reloaded` receiver applies the setting without a restart."""

    def test_override_swaps_the_class_and_restores_it_on_exit(self) -> None:
        """`override_next_settings` swaps the class for the block only."""
        with override_next_settings(DEPENDENCY_RESOLVER=WIDE_SKIP_PATH):
            assert type(resolver) is WideSkipResolver
            assert resolve_call(widget) == {"size": 3}
        assert type(resolver) is DependencyResolver
        assert resolve_call(widget) == {"size": 3, "label": "plain"}
