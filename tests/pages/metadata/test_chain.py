from collections.abc import Callable
from dataclasses import FrozenInstanceError
from pathlib import Path
from typing import Any

import pytest
from django.http import Http404, HttpRequest
from django.test import override_settings

from next.deps import Depends
from next.pages.errors import PageMetadataConflictError, PageMetadataShapeError
from next.pages.loaders import forget_page_roots, load_page_module, reset_module_memo
from next.pages.metadata import (
    Metadata,
    MetadataThunk,
    PageMetadataRegistry,
    Segment,
    chain_entry,
    chain_title,
    site_segment,
)
from next.pages.metadata.chain import PARENT_KEY, ChainEntry, ChainSource
from next.pages.metadata.schema import EMPTY_METADATA, OpenGraph, Robots
from tests.support import (
    bound_dependency,
    build_page_request,
    default_page_router_config,
    write_page_chain,
)


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
DYNAMIC_TEMPLATE = {"title": {"template": "{title} | Dyn"}}
ABOVE_THE_TREE = """
from pathlib import Path

Path(__file__).with_name("loaded").touch()
metadata = {"description": "Above"}
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
    thunk = MetadataThunk(
        registry,
        file_path,
        None,
        url_kwargs or {},
        dep_cache if dep_cache is not None else {},
    )
    return thunk.resolve(context_data if context_data is not None else {})


def static_metadata(registry: PageMetadataRegistry, file_path: Path) -> Metadata:
    return chain_entry(registry, file_path).static


@pytest.fixture()
def registry() -> PageMetadataRegistry:
    return PageMetadataRegistry()


class TestValueObjects:
    """The chain records are frozen and the parent key is fixed."""

    def test_the_parent_is_published_under_a_fixed_key(self) -> None:
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


class TestChainTitle:
    """The title a page would render for a text of its own."""

    def test_applies_the_ancestor_template_and_ignores_the_own_dict(
        self, registry: PageMetadataRegistry, tmp_path: Path
    ) -> None:
        own = 'metadata = {"title": {"template": "{title} - Leaf", "default": "L"}}\n'
        _root, leaf = write_page_chain(tmp_path, [("root", ROOT), ("leaf", own)])
        assert str(chain_title(registry, leaf, "Post")) == "Post | Root"

    def test_without_a_template_the_text_stands(
        self, registry: PageMetadataRegistry, tmp_path: Path
    ) -> None:
        (leaf,) = write_page_chain(tmp_path, [("leaf", LEAF)])
        assert chain_title(registry, leaf, "Post") == "Post"

    def test_a_callable_source_of_the_page_is_skipped_as_well(
        self, registry: PageMetadataRegistry, tmp_path: Path
    ) -> None:
        _root, leaf = write_page_chain(tmp_path, [("root", ROOT), ("leaf", PLAIN)])
        registry.register(leaf, lambda: {"title": "Dynamic"})
        assert str(chain_title(registry, leaf, "Post")) == "Post | Root"

    def test_an_inherited_callable_template_shapes_the_text_as_in_the_render(
        self, registry: PageMetadataRegistry, tmp_path: Path
    ) -> None:
        root, leaf = write_page_chain(tmp_path, [("root", PLAIN), ("leaf", LEAF)])
        registry.register(root, lambda: DYNAMIC_TEMPLATE, inherit=True)
        assert str(_resolve(registry, leaf).title) == "Leaf | Dyn"
        assert str(chain_title(registry, leaf, "Post")) == "Post | Dyn"

    def test_the_inherited_callable_reads_the_request_the_kwargs_and_the_context(
        self, registry: PageMetadataRegistry, tmp_path: Path
    ) -> None:
        root, leaf = write_page_chain(tmp_path, [("root", PLAIN), ("leaf", PLAIN)])

        def root_meta(request: HttpRequest, slug: str, board: str) -> dict[str, object]:
            template = f"{{title}} | {request.method} {slug} {board}"
            return {"title": {"template": template}}

        registry.register(root, root_meta, inherit=True)
        title = chain_title(
            registry,
            leaf,
            "Post",
            request=build_page_request(),
            url_kwargs={"slug": "s"},
            context_data=lambda: {"board": "B"},
        )
        assert str(title) == "Post | GET s B"

    def test_the_context_is_built_only_for_an_inherited_callable(
        self, registry: PageMetadataRegistry, tmp_path: Path
    ) -> None:
        _root, leaf = write_page_chain(tmp_path, [("root", ROOT), ("leaf", PLAIN)])
        builds: list[int] = []

        def context() -> dict[str, object]:
            builds.append(1)
            return {}

        registry.register(leaf, lambda: {"title": "Own"})
        title = chain_title(registry, leaf, "Post", context_data=context)
        assert str(title) == "Post | Root"
        assert builds == []

    def test_an_inherited_callable_without_a_context_reads_an_empty_one(
        self, registry: PageMetadataRegistry, tmp_path: Path
    ) -> None:
        root, leaf = write_page_chain(tmp_path, [("root", PLAIN), ("leaf", PLAIN)])
        registry.register(root, lambda: DYNAMIC_TEMPLATE, inherit=True)
        assert str(chain_title(registry, leaf, "Post")) == "Post | Dyn"

    def test_the_own_site_name_still_fills_the_template(
        self, registry: PageMetadataRegistry, tmp_path: Path
    ) -> None:
        root = 'metadata = {"title": {"template": "{title} · {site_name}"}}\n'
        own = 'metadata = {"title": "Leaf", "site_name": "Leaf Co"}\n'
        _root, leaf = write_page_chain(tmp_path, [("root", root), ("leaf", own)])
        assert str(static_metadata(registry, leaf).title) == "Leaf · Leaf Co"
        assert str(chain_title(registry, leaf, "Post")) == "Post · Leaf Co"


class TestPageTreeRoot:
    """The walk stops at the page tree the page belongs to."""

    def test_a_page_py_above_the_page_tree_is_never_loaded(
        self, registry: PageMetadataRegistry, tmp_path: Path
    ) -> None:
        (tmp_path / "page.py").write_text(ABOVE_THE_TREE)
        tree, leaf = write_page_chain(tmp_path, [("tree", ROOT), ("leaf", LEAF)])
        config = default_page_router_config(tree.parent)
        with override_settings(NEXT_FRAMEWORK={"PAGE_BACKENDS": config}):
            meta = static_metadata(registry, leaf)
        assert str(meta.title) == "Leaf | Root"
        assert meta.description == "Root"
        assert not (tmp_path / "loaded").exists()

    def test_a_page_outside_every_tree_walks_every_ancestor(
        self, registry: PageMetadataRegistry, tmp_path: Path
    ) -> None:
        (tmp_path / "page.py").write_text(ABOVE_THE_TREE)
        _tree, leaf = write_page_chain(tmp_path, [("tree", PLAIN), ("leaf", LEAF)])
        assert static_metadata(registry, leaf).description == "Above"
        assert (tmp_path / "loaded").exists()

    def test_moved_page_trees_rebuild_the_entry(
        self, registry: PageMetadataRegistry, tmp_path: Path
    ) -> None:
        _tree, leaf = write_page_chain(tmp_path, [("tree", ROOT), ("leaf", LEAF)])
        chain_entry(registry, leaf)
        before = chain_entry(registry, leaf)
        forget_page_roots()
        assert chain_entry(registry, leaf) is not before


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
        module, _error = load_page_module(leaf)
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

    @pytest.mark.parametrize(
        "invalidate",
        [
            lambda registry, leaf: registry.register(leaf, lambda: {"title": "D"}),
            lambda _registry, _leaf: reset_module_memo(),
            lambda registry, _leaf: registry.reset(),
        ],
        ids=["registration", "module_reload", "registry_reset"],
    )
    def test_an_invalidation_rebuilds_the_entry(
        self,
        registry: PageMetadataRegistry,
        tmp_path: Path,
        invalidate: Callable[[PageMetadataRegistry, Path], object],
    ) -> None:
        _root, leaf = write_page_chain(tmp_path, [("root", ROOT), ("leaf", PLAIN)])
        chain_entry(registry, leaf)
        before = chain_entry(registry, leaf)
        invalidate(registry, leaf)
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


class TestThunk:
    """The render-time thunk folds against whatever context each read hands it."""

    def test_each_read_folds_against_the_context_it_is_handed(
        self, registry: PageMetadataRegistry, tmp_path: Path
    ) -> None:
        (leaf,) = write_page_chain(tmp_path, [("leaf", PLAIN)])
        registry.register(leaf, lambda user: {"title": user})
        thunk = MetadataThunk(registry, leaf, None, {}, {})
        assert thunk.resolve({"user": "Ann"}).title == "Ann"
        assert thunk.resolve({"user": "Bob"}).title == "Bob"

    def test_the_thunk_carries_what_the_resolve_needs_and_no_context(
        self, registry: PageMetadataRegistry, tmp_path: Path
    ) -> None:
        leaf = tmp_path / "page.py"
        cache: dict[str, Any] = {}
        thunk = MetadataThunk(registry, leaf, None, {"slug": "s"}, cache)
        assert thunk.registry is registry
        assert thunk.file_path == leaf
        assert thunk.request is None
        assert thunk.url_kwargs == {"slug": "s"}
        assert thunk.dep_cache is cache
        assert not hasattr(thunk, "context_data")
