from dataclasses import FrozenInstanceError
from pathlib import Path
from typing import Any

import pytest
from django.http import Http404
from django.test import override_settings

from next.deps import Depends
from next.pages.errors import PageMetadataConflictError, PageMetadataShapeError
from next.pages.loaders import _load_python_module_memo, reset_module_memo
from next.pages.metadata import (
    EMPTY_METADATA,
    PARENT_KEY,
    ChainEntry,
    ChainSource,
    Metadata,
    MetadataThunk,
    OpenGraph,
    PageMetadataRegistry,
    Robots,
    Segment,
    chain_entry,
    resolve_metadata,
    site_segment,
    static_metadata,
    templated_title,
)
from tests.support import bound_dependency, write_page_chain


ROOT = 'metadata = {"title": {"template": "{title} | Root"}, "description": "Root"}\n'
ROOT_OG = (
    'metadata = {"description": "Root", '
    '"og": {"type": "article", "title": "R", "site_name": "Acme"}}\n'
)
MID = 'metadata = {"description": "Mid"}\n'
LEAF = 'metadata = {"title": "Leaf", "og": {"type": "website"}}\n'
PLAIN = "x = 1\n"
NAMED_METADATA = """
def metadata() -> dict:
    return {"title": "Named"}
"""
SITE_DEFAULTS = {
    "site_name": "Acme",
    "title": {"template": "{title} · {site_name}", "default": "Acme"},
    "robots": {"index": True},
}


def _resolve(
    registry: PageMetadataRegistry,
    file_path: Path,
    *,
    url_kwargs: dict[str, object] | None = None,
    dep_cache: dict[str, Any] | None = None,
    context_data: dict[str, object] | None = None,
) -> Metadata:
    return resolve_metadata(
        registry,
        file_path,
        request=None,
        url_kwargs=url_kwargs or {},
        dep_cache=dep_cache if dep_cache is not None else {},
        context_data=context_data if context_data is not None else {},
    )


@pytest.fixture()
def registry() -> PageMetadataRegistry:
    return PageMetadataRegistry()


class TestValueObjects:
    """The chain records are frozen and the parent key is fixed."""

    def test_parent_key(self) -> None:
        assert PARENT_KEY == "_next_metadata_parent"

    def test_chain_source_is_frozen(self, tmp_path: Path) -> None:
        source = ChainSource(tmp_path / "page.py", Segment("s"))
        assert source.func is None
        with pytest.raises(FrozenInstanceError):
            source.func = print  # type: ignore[misc]

    def test_chain_entry_is_frozen(self) -> None:
        entry = ChainEntry(0, 0, Segment("s"), (), EMPTY_METADATA, EMPTY_METADATA)
        with pytest.raises(FrozenInstanceError):
            entry.version = 1  # type: ignore[misc]


class TestStaticInheritance:
    """Static dicts inherit root to leaf, the nearer one winning per key."""

    def test_an_ancestor_dict_inherits_and_the_nearer_wins_per_key(
        self, registry: PageMetadataRegistry, tmp_path: Path
    ) -> None:
        root, _mid, leaf = write_page_chain(
            tmp_path, [("root", ROOT), ("mid", MID), ("leaf", LEAF)]
        )
        meta = static_metadata(registry, leaf)
        assert meta.description == "Mid"
        assert str(meta.title) == "Leaf | Root"
        assert static_metadata(registry, root).description == "Root"

    def test_og_is_replaced_whole(
        self, registry: PageMetadataRegistry, tmp_path: Path
    ) -> None:
        _root, leaf = write_page_chain(tmp_path, [("root", ROOT_OG), ("leaf", LEAF)])
        assert static_metadata(registry, leaf).og == OpenGraph(type="website")

    def test_the_settings_tier_sits_outermost(
        self, registry: PageMetadataRegistry, tmp_path: Path
    ) -> None:
        _root, leaf = write_page_chain(tmp_path, [("root", ROOT), ("leaf", LEAF)])
        defaults = {**SITE_DEFAULTS, "description": "Site"}
        with override_settings(NEXT_FRAMEWORK={"METADATA": {"DEFAULTS": defaults}}):
            meta = static_metadata(registry, leaf)
        assert meta.robots == Robots(index=True)
        assert meta.site_name == "Acme"
        assert meta.description == "Root"

    def test_a_page_without_any_source_folds_to_the_settings_tier(
        self, registry: PageMetadataRegistry, tmp_path: Path
    ) -> None:
        (leaf,) = write_page_chain(tmp_path, [("leaf", PLAIN)])
        assert static_metadata(registry, leaf) == EMPTY_METADATA

    def test_a_missing_ancestor_file_contributes_nothing(
        self, registry: PageMetadataRegistry, tmp_path: Path
    ) -> None:
        leaf = tmp_path / "root" / "gap" / "leaf" / "page.py"
        leaf.parent.mkdir(parents=True)
        leaf.write_text(LEAF)
        (tmp_path / "root").mkdir(exist_ok=True)
        (tmp_path / "root" / "page.py").write_text(ROOT)
        assert str(static_metadata(registry, leaf).title) == "Leaf | Root"


class TestTitleTemplates:
    """The template of an ancestor shapes the text of a descendant."""

    def test_absolute_ignores_the_template(
        self, registry: PageMetadataRegistry, tmp_path: Path
    ) -> None:
        source = 'metadata = {"title": {"absolute": "Bare"}}\n'
        _root, leaf = write_page_chain(tmp_path, [("root", ROOT), ("leaf", source)])
        assert static_metadata(registry, leaf).title == "Bare"

    def test_default_fills_a_leaf_without_a_title(
        self, registry: PageMetadataRegistry, tmp_path: Path
    ) -> None:
        root = 'metadata = {"title": {"template": "{title} | R", "default": "Home"}}\n'
        _root, leaf = write_page_chain(tmp_path, [("root", root), ("leaf", MID)])
        assert static_metadata(registry, leaf).title == "Home"

    def test_the_settings_template_applies_to_the_root_page_too(
        self, registry: PageMetadataRegistry, tmp_path: Path
    ) -> None:
        (root,) = write_page_chain(tmp_path, [("root", 'metadata = {"title": "R"}\n')])
        with override_settings(
            NEXT_FRAMEWORK={"METADATA": {"DEFAULTS": SITE_DEFAULTS}}
        ):
            assert str(static_metadata(registry, root).title) == "R · Acme"


class TestTemplatedTitle:
    """The title a page would render for a text of its own."""

    def test_applies_the_ancestor_template_and_ignores_the_own_dict(
        self, registry: PageMetadataRegistry, tmp_path: Path
    ) -> None:
        own = 'metadata = {"title": {"template": "{title} - Leaf", "default": "L"}}\n'
        _root, leaf = write_page_chain(tmp_path, [("root", ROOT), ("leaf", own)])
        assert str(templated_title(registry, leaf, "Post")) == "Post | Root"

    def test_absolute_returns_the_bare_text(
        self, registry: PageMetadataRegistry, tmp_path: Path
    ) -> None:
        _root, leaf = write_page_chain(tmp_path, [("root", ROOT), ("leaf", LEAF)])
        assert templated_title(registry, leaf, "Post", absolute=True) == "Post"

    def test_without_a_template_the_text_stands(
        self, registry: PageMetadataRegistry, tmp_path: Path
    ) -> None:
        (leaf,) = write_page_chain(tmp_path, [("leaf", LEAF)])
        assert templated_title(registry, leaf, "Post") == "Post"

    def test_a_callable_source_of_the_page_is_skipped_as_well(
        self, registry: PageMetadataRegistry, tmp_path: Path
    ) -> None:
        _root, leaf = write_page_chain(tmp_path, [("root", ROOT), ("leaf", PLAIN)])
        registry.register(leaf, lambda: {"title": "Dynamic"})
        assert str(templated_title(registry, leaf, "Post")) == "Post | Root"


class TestCallables:
    """A registered callable runs for its own page, and for descendants on demand."""

    def test_the_leaf_callable_runs_over_the_static_fold(
        self, registry: PageMetadataRegistry, tmp_path: Path
    ) -> None:
        _root, mid, leaf = write_page_chain(
            tmp_path, [("root", ROOT), ("mid", MID), ("leaf", PLAIN)]
        )
        seen: list[Metadata] = []

        def leaf_meta(parent: Metadata) -> dict[str, str]:
            seen.append(parent)
            return {"title": "Dynamic"}

        registry.register(leaf, leaf_meta)
        meta = _resolve(registry, leaf)

        assert str(meta.title) == "Dynamic | Root"
        assert meta.description == "Mid"
        assert seen[0] == static_metadata(registry, mid)

    def test_a_callable_may_read_a_dependency_a_url_kwarg_and_a_context_key(
        self, registry: PageMetadataRegistry, tmp_path: Path
    ) -> None:
        (leaf,) = write_page_chain(tmp_path, [("leaf", PLAIN)])

        def leaf_meta(slug: str, user: str, wallet: str = Depends("wallet")) -> dict:
            return {"title": f"{slug}/{user}/{wallet}"}

        registry.register(leaf, leaf_meta)
        with bound_dependency("wallet", lambda: "W"):
            meta = _resolve(
                registry, leaf, url_kwargs={"slug": "s"}, context_data={"user": "U"}
            )
        assert meta.title == "s/U/W"

    def test_the_dependency_cache_is_the_one_handed_in(
        self, registry: PageMetadataRegistry, tmp_path: Path
    ) -> None:
        (leaf,) = write_page_chain(tmp_path, [("leaf", PLAIN)])
        registry.register(leaf, lambda wallet=Depends("wallet"): {"title": wallet})
        cache: dict[str, Any] = {"wallet": "cached"}
        with bound_dependency("wallet", lambda: "fresh"):
            meta = _resolve(registry, leaf, dep_cache=cache)
        assert meta.title == "cached"

    def test_an_ancestor_callable_without_inherit_does_not_run_for_the_leaf(
        self, registry: PageMetadataRegistry, tmp_path: Path
    ) -> None:
        root, leaf = write_page_chain(tmp_path, [("root", PLAIN), ("leaf", LEAF)])
        calls: list[str] = []

        def root_meta() -> dict[str, str]:
            calls.append("root")
            return {"description": "Dynamic root"}

        registry.register(root, root_meta)
        meta = _resolve(registry, leaf)
        assert calls == []
        assert meta.description is None
        assert _resolve(registry, root).description == "Dynamic root"

    def test_an_ancestor_callable_with_inherit_runs_before_the_leaf(
        self, registry: PageMetadataRegistry, tmp_path: Path
    ) -> None:
        root, leaf = write_page_chain(tmp_path, [("root", PLAIN), ("leaf", LEAF)])
        registry.register(root, lambda: {"description": "Dynamic root"}, inherit=True)
        meta = _resolve(registry, leaf)
        assert meta.description == "Dynamic root"
        assert meta.title == "Leaf"

    def test_the_parent_of_a_later_callable_carries_the_earlier_one(
        self, registry: PageMetadataRegistry, tmp_path: Path
    ) -> None:
        root, leaf = write_page_chain(tmp_path, [("root", PLAIN), ("leaf", PLAIN)])
        registry.register(root, lambda: {"description": "From root"}, inherit=True)
        seen: list[Metadata] = []

        def leaf_meta(parent: Metadata) -> dict[str, str]:
            seen.append(parent)
            return {"title": "Leaf"}

        registry.register(leaf, leaf_meta)
        _resolve(registry, leaf)
        assert seen[0].description == "From root"

    def test_a_dict_and_a_callable_in_one_file_conflict(
        self, registry: PageMetadataRegistry, tmp_path: Path
    ) -> None:
        root, leaf = write_page_chain(tmp_path, [("root", ROOT), ("leaf", LEAF)])
        registry.register(root, dict)
        with pytest.raises(PageMetadataConflictError) as excinfo:
            static_metadata(registry, leaf)
        assert excinfo.value.file_path == root

    def test_a_callable_named_metadata_is_the_callable_form(
        self, registry: PageMetadataRegistry, tmp_path: Path
    ) -> None:
        _root, leaf = write_page_chain(
            tmp_path, [("root", ROOT), ("leaf", NAMED_METADATA)]
        )
        module = _load_python_module_memo(leaf)
        assert module is not None
        registry.register(leaf, module.metadata)

        entry = chain_entry(registry, leaf)

        assert entry.folded is None
        assert [source.func is not None for source in entry.sources] == [False, True]
        assert static_metadata(registry, leaf).description == "Root"
        assert str(_resolve(registry, leaf).title) == "Named | Root"

    def test_a_non_mapping_result_names_the_callable_and_the_file(
        self, registry: PageMetadataRegistry, tmp_path: Path
    ) -> None:
        (leaf,) = write_page_chain(tmp_path, [("leaf", PLAIN)])

        def bad_meta() -> list[str]:
            return ["x"]

        registry.register(leaf, bad_meta)
        with pytest.raises(PageMetadataShapeError, match="expected a mapping") as info:
            _resolve(registry, leaf)
        assert info.value.source == f"bad_meta in {leaf}"

    def test_http404_propagates_untouched(
        self, registry: PageMetadataRegistry, tmp_path: Path
    ) -> None:
        (leaf,) = write_page_chain(tmp_path, [("leaf", PLAIN)])

        def gone() -> dict[str, str]:
            raise Http404

        registry.register(leaf, gone)
        with pytest.raises(Http404):
            _resolve(registry, leaf)


class TestMemo:
    """The chain memo self-validates on the registry, the loads and the settings."""

    def test_a_warm_static_chain_resolves_to_the_identical_object(
        self, registry: PageMetadataRegistry, tmp_path: Path
    ) -> None:
        _root, leaf = write_page_chain(tmp_path, [("root", ROOT), ("leaf", LEAF)])
        _resolve(registry, leaf)
        first = _resolve(registry, leaf)
        assert _resolve(registry, leaf) is first
        assert static_metadata(registry, leaf) is first

    def test_the_entry_settles_once_the_walk_has_loaded_every_module(
        self, registry: PageMetadataRegistry, tmp_path: Path
    ) -> None:
        _root, leaf = write_page_chain(tmp_path, [("root", ROOT), ("leaf", LEAF)])
        first = chain_entry(registry, leaf)
        second = chain_entry(registry, leaf)
        assert first is not second
        assert chain_entry(registry, leaf) is second

    def test_a_dynamic_chain_folds_per_call(
        self, registry: PageMetadataRegistry, tmp_path: Path
    ) -> None:
        (leaf,) = write_page_chain(tmp_path, [("leaf", PLAIN)])
        registry.register(leaf, lambda: {"title": "Dynamic"})
        entry = chain_entry(registry, leaf)
        assert entry.folded is None
        assert _resolve(registry, leaf) is not _resolve(registry, leaf)

    def test_a_registration_rebuilds_the_entry(
        self, registry: PageMetadataRegistry, tmp_path: Path
    ) -> None:
        _root, leaf = write_page_chain(tmp_path, [("root", ROOT), ("leaf", PLAIN)])
        chain_entry(registry, leaf)
        before = chain_entry(registry, leaf)
        registry.register(leaf, lambda: {"title": "Dynamic"})
        after = chain_entry(registry, leaf)
        assert after is not before
        assert after.folded is None

    def test_a_module_reload_rebuilds_the_entry(
        self, registry: PageMetadataRegistry, tmp_path: Path
    ) -> None:
        _root, leaf = write_page_chain(tmp_path, [("root", ROOT), ("leaf", LEAF)])
        chain_entry(registry, leaf)
        before = chain_entry(registry, leaf)
        reset_module_memo()
        assert chain_entry(registry, leaf) is not before

    def test_a_settings_override_rebuilds_the_entry(
        self, registry: PageMetadataRegistry, tmp_path: Path
    ) -> None:
        _root, leaf = write_page_chain(tmp_path, [("root", ROOT), ("leaf", LEAF)])
        chain_entry(registry, leaf)
        before = chain_entry(registry, leaf)
        with override_settings(
            NEXT_FRAMEWORK={"METADATA": {"DEFAULTS": SITE_DEFAULTS}}
        ):
            inside = chain_entry(registry, leaf)
            assert inside is not before
            assert inside.site is site_segment()
            assert inside.static.site_name == "Acme"
        assert chain_entry(registry, leaf) is not inside

    def test_forget_chains_drops_the_entry(
        self, registry: PageMetadataRegistry, tmp_path: Path
    ) -> None:
        (leaf,) = write_page_chain(tmp_path, [("leaf", LEAF)])
        chain_entry(registry, leaf)
        before = chain_entry(registry, leaf)
        registry.forget_chains()
        assert chain_entry(registry, leaf) is not before


class TestThunk:
    """The render-time thunk folds once and answers the same object after that."""

    def test_resolve_memoises_the_metadata(
        self, registry: PageMetadataRegistry, tmp_path: Path
    ) -> None:
        (leaf,) = write_page_chain(tmp_path, [("leaf", PLAIN)])
        calls: list[int] = []

        def leaf_meta() -> dict[str, str]:
            calls.append(1)
            return {"title": "Dynamic"}

        registry.register(leaf, leaf_meta)
        thunk = MetadataThunk(registry, leaf, None, {}, {}, {})
        first = thunk.resolve()
        assert thunk.resolve() is first
        assert first.title == "Dynamic"
        assert len(calls) == 1

    def test_the_thunk_carries_what_the_resolve_needs(
        self, registry: PageMetadataRegistry, tmp_path: Path
    ) -> None:
        leaf = tmp_path / "page.py"
        cache: dict[str, Any] = {}
        context: dict[str, object] = {}
        thunk = MetadataThunk(registry, leaf, None, {"slug": "s"}, cache, context)
        assert thunk.registry is registry
        assert thunk.file_path == leaf
        assert thunk.request is None
        assert thunk.url_kwargs == {"slug": "s"}
        assert thunk.dep_cache is cache
        assert thunk.context_data is context
