import threading
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest
from django.http import Http404, HttpRequest
from django.test import override_settings

import next.pages.loaders as loaders_module
from next.caches import BoundedCache
from next.deps import Depends
from next.diagnostics import degraded, watch_degraded
from next.pages.errors import PageMetadataConflictError, PageMetadataShapeError
from next.pages.loaders import (
    forget_page_roots,
    load_page_module,
    module_generation,
    reset_module_memo,
)
from next.pages.metadata import RESET, OpenGraph, Robots
from next.pages.metadata.chain import (
    REFUSED_ROBOTS,
    ChainEntry,
    MetadataDeclaration,
    MetadataOrigin,
    MetadataThunk,
    _failures,
    chain_entry,
    contained_chain,
    declared_metadata,
    fold_chain,
    metadata_origins,
    static_metadata as read_static_metadata,
)
from next.pages.metadata.markers import Metadata, Segment, TitleSpec
from next.pages.metadata.normalize import normalize_metadata
from next.pages.metadata.registry import PageMetadataRegistry
from next.pages.metadata.scope import SITE_NAME_SOURCE, site_segment
from tests.support import (
    bound_dependency,
    build_page_request,
    default_page_router_config,
    touch_later,
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
UNKNOWN_KEY = "headline"
REFUSED = f'metadata = {{"{UNKNOWN_KEY}": "Refused"}}\n'
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
HALF_WRITTEN = -1
RACE_EDITS = 20
STAGES = {"Root": 0, **{f"v{edit}": edit for edit in range(1, RACE_EDITS + 1)}}
SITE_DEFAULTS = {
    "site_name": "Acme",
    "title": {"template": "{title} · {site_name}", "default": "Acme"},
    "robots": {"index": True},
}


def _fold(
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
    return thunk.fold(context_data if context_data is not None else {})


def _overlay(title: str) -> Segment:
    return Segment("overlay", title=TitleSpec(text=title))


def static_metadata(registry: PageMetadataRegistry, file_path: Path) -> Metadata:
    return chain_entry(registry, file_path).static


def _kept(after: ChainEntry, before: ChainEntry) -> bool:
    """Whether a read revalidated the entry rather than walking the chain again."""
    return after.sources is before.sources and after.static is before.static


class TestDeclaration:
    """`declared_metadata` tells the dict form from the callable form."""

    def test_a_dict_is_the_raw_form(
        self, registry: PageMetadataRegistry, tmp_path: Path
    ) -> None:
        (leaf,) = write_page_chain(tmp_path, [("leaf", LEAF)])
        declaration = declared_metadata(registry, leaf)
        assert declaration == MetadataDeclaration(
            {"title": "Leaf", "og": {"type": "website"}}, None
        )

    def test_a_registered_callable_named_metadata_reads_as_the_raw_form(
        self, registry: PageMetadataRegistry, tmp_path: Path
    ) -> None:
        (leaf,) = write_page_chain(tmp_path, [("leaf", NAMED_METADATA)])
        module, _error = load_page_module(leaf)
        assert module is not None
        registry.register(leaf, module.metadata)
        declaration = declared_metadata(registry, leaf)
        assert declaration.raw is module.metadata
        assert declaration.entry is not None
        assert declaration.entry.func is module.metadata

    def test_a_missing_file_declares_nothing(
        self, registry: PageMetadataRegistry, tmp_path: Path
    ) -> None:
        assert declared_metadata(registry, tmp_path / "page.py") == (
            MetadataDeclaration(None, None)
        )


class TestStaticInheritance:
    """Static dicts inherit root to leaf, each block merging per field."""

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

    def test_og_merges_per_field(
        self, registry: PageMetadataRegistry, tmp_path: Path
    ) -> None:
        _root, leaf = write_page_chain(tmp_path, [("root", ROOT_OG), ("leaf", LEAF)])
        assert static_metadata(registry, leaf).og == OpenGraph(
            type="website", title="R", site_name="Acme"
        )

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
        assert static_metadata(registry, leaf) == Metadata()

    def test_a_missing_ancestor_file_contributes_nothing(
        self, registry: PageMetadataRegistry, tmp_path: Path
    ) -> None:
        leaf = tmp_path / "root" / "gap" / "leaf" / "page.py"
        leaf.parent.mkdir(parents=True)
        leaf.write_text(LEAF)
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


class TestOverlay:
    """An overlay folds last and replaces the page's own callable."""

    def test_the_ancestor_template_wraps_the_overlay_in_place_of_the_own_title(
        self, registry: PageMetadataRegistry, tmp_path: Path
    ) -> None:
        own = 'metadata = {"title": {"template": "{title} - Leaf", "default": "L"}}\n'
        _root, leaf = write_page_chain(tmp_path, [("root", ROOT), ("leaf", own)])
        meta = fold_chain(registry, leaf, dep_cache={}, overlay=_overlay("Post"))
        assert str(meta.title) == "Post | Root"
        assert meta.description == "Root"

    def test_the_overlay_lies_over_the_own_dict_block_by_block(
        self, registry: PageMetadataRegistry, tmp_path: Path
    ) -> None:
        own = 'metadata = {"title": "Own", "og": {"type": "article", "title": "O"}}\n'
        (leaf,) = write_page_chain(tmp_path, [("leaf", own)])
        overlay = normalize_metadata({"og": {"title": "Over"}}, source="overlay")
        meta = fold_chain(registry, leaf, dep_cache={}, overlay=overlay)
        assert meta.title == "Own"
        assert meta.og == OpenGraph(type="article", title="Over")

    def test_a_reset_in_the_overlay_drops_the_own_and_the_inherited_value(
        self, registry: PageMetadataRegistry, tmp_path: Path
    ) -> None:
        root = 'metadata = {"description": "Root", "title": {"template": "{title}!"}}\n'
        own = 'metadata = {"description": "Own", "title": "Own"}\n'
        _root, leaf = write_page_chain(tmp_path, [("root", root), ("leaf", own)])
        overlay = normalize_metadata(
            {"description": RESET, "title": RESET}, source="overlay"
        )
        meta = fold_chain(registry, leaf, dep_cache={}, overlay=overlay)
        assert meta.description is None
        assert meta.title is None

    def test_without_a_template_the_text_stands(
        self, registry: PageMetadataRegistry, tmp_path: Path
    ) -> None:
        (leaf,) = write_page_chain(tmp_path, [("leaf", LEAF)])
        meta = fold_chain(registry, leaf, dep_cache={}, overlay=_overlay("Post"))
        assert meta.title == "Post"

    def test_the_own_callable_is_skipped(
        self, registry: PageMetadataRegistry, tmp_path: Path
    ) -> None:
        _root, leaf = write_page_chain(tmp_path, [("root", ROOT), ("leaf", PLAIN)])
        calls: list[int] = []

        def own() -> dict[str, str]:
            calls.append(1)
            return {"title": "Dynamic"}

        registry.register(leaf, own)
        meta = fold_chain(registry, leaf, dep_cache={}, overlay=_overlay("Post"))
        assert str(meta.title) == "Post | Root"
        assert calls == []

    def test_an_inherited_callable_template_shapes_the_overlay(
        self, registry: PageMetadataRegistry, tmp_path: Path
    ) -> None:
        root, leaf = write_page_chain(tmp_path, [("root", PLAIN), ("leaf", LEAF)])
        registry.register(root, lambda: DYNAMIC_TEMPLATE, inherit=True)
        assert str(_fold(registry, leaf).title) == "Leaf | Dyn"
        meta = fold_chain(registry, leaf, dep_cache={}, overlay=_overlay("Post"))
        assert str(meta.title) == "Post | Dyn"

    def test_the_inherited_callable_reads_the_request_the_kwargs_and_the_context(
        self, registry: PageMetadataRegistry, tmp_path: Path
    ) -> None:
        root, leaf = write_page_chain(tmp_path, [("root", PLAIN), ("leaf", PLAIN)])

        def root_meta(request: HttpRequest, slug: str, board: str) -> dict[str, object]:
            template = f"{{title}} | {request.method} {slug} {board}"
            return {"title": {"template": template}}

        registry.register(root, root_meta, inherit=True)
        meta = fold_chain(
            registry,
            leaf,
            dep_cache={},
            overlay=_overlay("Post"),
            request=build_page_request(),
            url_kwargs={"slug": "s"},
            context_data=lambda: {"board": "B"},
        )
        assert str(meta.title) == "Post | GET s B"

    def test_the_context_is_built_only_for_a_callable_that_runs(
        self, registry: PageMetadataRegistry, tmp_path: Path
    ) -> None:
        _root, leaf = write_page_chain(tmp_path, [("root", ROOT), ("leaf", PLAIN)])
        builds: list[int] = []

        def context() -> dict[str, object]:
            builds.append(1)
            return {}

        registry.register(leaf, lambda: {"title": "Own"})
        meta = fold_chain(
            registry, leaf, dep_cache={}, overlay=_overlay("Post"), context_data=context
        )
        assert str(meta.title) == "Post | Root"
        assert builds == []

    def test_an_inherited_callable_without_a_context_reads_an_empty_one(
        self, registry: PageMetadataRegistry, tmp_path: Path
    ) -> None:
        root, leaf = write_page_chain(tmp_path, [("root", PLAIN), ("leaf", PLAIN)])
        registry.register(root, lambda: DYNAMIC_TEMPLATE, inherit=True)
        meta = fold_chain(registry, leaf, dep_cache={}, overlay=_overlay("Post"))
        assert str(meta.title) == "Post | Dyn"

    def test_the_own_site_name_still_fills_the_template(
        self, registry: PageMetadataRegistry, tmp_path: Path
    ) -> None:
        root = 'metadata = {"title": {"template": "{title} · {site_name}"}}\n'
        own = 'metadata = {"title": "Leaf", "site_name": "Leaf Co"}\n'
        _root, leaf = write_page_chain(tmp_path, [("root", root), ("leaf", own)])
        assert str(static_metadata(registry, leaf).title) == "Leaf · Leaf Co"
        meta = fold_chain(registry, leaf, dep_cache={}, overlay=_overlay("Post"))
        assert str(meta.title) == "Post · Leaf Co"


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
        before = chain_entry(registry, leaf)
        forget_page_roots()
        assert chain_entry(registry, leaf) is not before


class TestCallables:
    """A registered callable runs for its own page, and for descendants on demand."""

    def test_the_leaf_callable_merges_over_the_static_fold(
        self, registry: PageMetadataRegistry, tmp_path: Path
    ) -> None:
        _root, _mid, leaf = write_page_chain(
            tmp_path, [("root", ROOT_OG), ("mid", MID), ("leaf", PLAIN)]
        )
        registry.register(leaf, lambda: {"title": "Dynamic", "og": {"title": "D"}})
        meta = _fold(registry, leaf)
        assert meta.title == "Dynamic"
        assert meta.description == "Mid"
        assert meta.og == OpenGraph(type="article", title="D", site_name="Acme")

    def test_a_callable_may_read_a_dependency_a_url_kwarg_and_a_context_key(
        self, registry: PageMetadataRegistry, tmp_path: Path
    ) -> None:
        (leaf,) = write_page_chain(tmp_path, [("leaf", PLAIN)])

        def leaf_meta(slug: str, user: str, wallet: str = Depends("wallet")) -> dict:
            return {"title": f"{slug}/{user}/{wallet}"}

        registry.register(leaf, leaf_meta)
        with bound_dependency("wallet", lambda: "W"):
            meta = _fold(
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
            meta = _fold(registry, leaf, dep_cache=cache)
        assert meta.title == "cached"

    def test_a_none_value_from_a_callable_inherits(
        self, registry: PageMetadataRegistry, tmp_path: Path
    ) -> None:
        _root, leaf = write_page_chain(tmp_path, [("root", ROOT), ("leaf", PLAIN)])
        registry.register(leaf, lambda: {"description": None, "title": "L"})
        assert _fold(registry, leaf).description == "Root"

    def test_an_ancestor_callable_without_inherit_does_not_run_for_the_leaf(
        self, registry: PageMetadataRegistry, tmp_path: Path
    ) -> None:
        root, leaf = write_page_chain(tmp_path, [("root", PLAIN), ("leaf", LEAF)])
        calls: list[str] = []

        def root_meta() -> dict[str, str]:
            calls.append("root")
            return {"description": "Dynamic root"}

        registry.register(root, root_meta)
        meta = _fold(registry, leaf)
        assert calls == []
        assert meta.description is None
        assert _fold(registry, root).description == "Dynamic root"

    def test_an_ancestor_callable_with_inherit_runs_before_the_leaf(
        self, registry: PageMetadataRegistry, tmp_path: Path
    ) -> None:
        root, leaf = write_page_chain(tmp_path, [("root", PLAIN), ("leaf", LEAF)])
        registry.register(root, lambda: {"description": "Dynamic root"}, inherit=True)
        meta = _fold(registry, leaf)
        assert meta.description == "Dynamic root"
        assert meta.title == "Leaf"

    def test_a_dict_and_a_callable_in_one_file_conflict(
        self, registry: PageMetadataRegistry, tmp_path: Path
    ) -> None:
        root, leaf = write_page_chain(tmp_path, [("root", ROOT), ("leaf", LEAF)])
        registry.register(root, dict)
        with pytest.raises(PageMetadataConflictError) as excinfo:
            static_metadata(registry, leaf)
        assert excinfo.value.file_path == root

    def test_a_callable_named_metadata_conflicts_with_the_dict_form(
        self, registry: PageMetadataRegistry, tmp_path: Path
    ) -> None:
        _root, leaf = write_page_chain(
            tmp_path, [("root", ROOT), ("leaf", NAMED_METADATA)]
        )
        module, _error = load_page_module(leaf)
        assert module is not None
        registry.register(leaf, module.metadata)
        with pytest.raises(PageMetadataConflictError) as excinfo:
            chain_entry(registry, leaf)
        assert excinfo.value.file_path == leaf

    def test_a_non_mapping_result_names_the_callable_and_the_file(
        self, registry: PageMetadataRegistry, tmp_path: Path
    ) -> None:
        (leaf,) = write_page_chain(tmp_path, [("leaf", PLAIN)])

        def bad_meta() -> list[str]:
            return ["x"]

        registry.register(leaf, bad_meta)
        with (
            override_settings(DEBUG=True),
            pytest.raises(PageMetadataShapeError, match="expected a mapping") as info,
        ):
            _fold(registry, leaf)
        assert info.value.source == f"bad_meta in {leaf}"
        assert f"bad_meta in {leaf} raised" in info.value.__notes__[0]

    def test_http404_propagates_untouched(
        self, registry: PageMetadataRegistry, tmp_path: Path
    ) -> None:
        (leaf,) = write_page_chain(tmp_path, [("leaf", PLAIN)])

        def gone() -> dict[str, str]:
            raise Http404

        registry.register(leaf, gone)
        with pytest.raises(Http404):
            _fold(registry, leaf)


class TestContainedFailures:
    """A render omits a failing callable and logs it once, and raises under DEBUG."""

    @pytest.fixture(autouse=True)
    def _armed(self) -> None:
        _failures.clear()

    def test_a_raising_callable_is_left_out_and_logged_once(
        self,
        registry: PageMetadataRegistry,
        tmp_path: Path,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        _root, leaf = write_page_chain(tmp_path, [("root", ROOT), ("leaf", PLAIN)])

        def broken() -> dict[str, str]:
            raise KeyError(0)

        registry.register(leaf, broken)
        with caplog.at_level("ERROR", logger="next.pages.metadata.chain"):
            first = _fold(registry, leaf)
            second = _fold(registry, leaf)
        assert first == second
        assert first.description == "Root"
        assert len(caplog.records) == 1
        assert f"broken in {leaf} raised KeyError" in caplog.text

    def test_a_refused_shape_is_left_out(
        self, registry: PageMetadataRegistry, tmp_path: Path
    ) -> None:
        _root, leaf = write_page_chain(tmp_path, [("root", ROOT), ("leaf", PLAIN)])
        registry.register(leaf, lambda: {"title": 3})
        assert _fold(registry, leaf).description == "Root"

    def test_a_static_fold_skips_the_contained_path(
        self, registry: PageMetadataRegistry, tmp_path: Path
    ) -> None:
        (leaf,) = write_page_chain(tmp_path, [("leaf", LEAF)])
        assert str(_fold(registry, leaf).title) == "Leaf"

    def test_a_refused_chain_folds_the_site_defaults_alone(
        self,
        registry: PageMetadataRegistry,
        tmp_path: Path,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        root, leaf = write_page_chain(tmp_path, [("root", ROOT), ("leaf", LEAF)])
        registry.register(root, dict)
        thunk = MetadataThunk(registry, leaf, None, {}, {})
        with caplog.at_level("ERROR", logger="next.pages.metadata.chain"):
            first = thunk.folded()
            second = thunk.folded()
        assert first is second
        assert first is not None
        assert first.title is None
        assert first.noindex
        assert len(caplog.records) == 1
        assert "renders the site defaults under noindex" in caplog.text

    def test_a_refusal_is_memoised_and_degrades_every_read(
        self, registry: PageMetadataRegistry, tmp_path: Path
    ) -> None:
        (leaf,) = write_page_chain(tmp_path, [("leaf", REFUSED)])
        with patch(
            "next.pages.metadata.chain.normalize_metadata",
            side_effect=normalize_metadata,
        ) as normalize:
            first = contained_chain(registry, leaf)
            watch_degraded()
            second = contained_chain(registry, leaf)
        assert normalize.call_count == 1
        assert second is first
        assert degraded()
        assert isinstance(first.refusal, PageMetadataShapeError)

    def test_a_fixed_page_drops_the_refusal(
        self, registry: PageMetadataRegistry, tmp_path: Path
    ) -> None:
        (leaf,) = write_page_chain(tmp_path, [("leaf", REFUSED)])
        assert contained_chain(registry, leaf).refusal is not None
        touch_later(leaf, LEAF)
        load_page_module(leaf)
        entry = contained_chain(registry, leaf)
        assert entry.refusal is None
        assert str(entry.static.title) == "Leaf"

    def test_the_strict_read_raises_a_new_exception_per_call(
        self, registry: PageMetadataRegistry, tmp_path: Path
    ) -> None:
        (leaf,) = write_page_chain(tmp_path, [("leaf", REFUSED)])
        contained_chain(registry, leaf)
        raised = []
        for _ in range(2):
            with pytest.raises(PageMetadataShapeError) as caught:
                chain_entry(registry, leaf)
            raised.append(caught.value)
        assert raised[0] is not raised[1]
        assert registry.chain(leaf) is not None

    def test_the_static_read_reports_the_refusal(
        self, registry: PageMetadataRegistry, tmp_path: Path
    ) -> None:
        (leaf,) = write_page_chain(tmp_path, [("leaf", REFUSED)])
        meta, refused = read_static_metadata(registry, leaf)
        assert refused
        assert meta.robots == REFUSED_ROBOTS

    @override_settings(
        NEXT_FRAMEWORK={"METADATA": {"DEFAULTS": {"robots": "noindex, nofollow"}}}
    )
    def test_a_refusal_keeps_the_declared_site_directives(
        self, registry: PageMetadataRegistry, tmp_path: Path
    ) -> None:
        (leaf,) = write_page_chain(tmp_path, [("leaf", REFUSED)])
        assert read_static_metadata(registry, leaf).metadata.robots == (
            "noindex, nofollow"
        )

    def test_every_contained_read_reports_a_new_exception(
        self, registry: PageMetadataRegistry, tmp_path: Path
    ) -> None:
        (leaf,) = write_page_chain(tmp_path, [("leaf", REFUSED)])
        with patch.object(_failures, "contain") as contain:
            entry = contained_chain(registry, leaf)
            contained_chain(registry, leaf)
        first, second = (call.args[0] for call in contain.call_args_list)
        assert first is not second
        assert entry.refusal not in (first, second)
        assert str(first) == str(second) == str(entry.refusal)
        assert first.source == entry.refusal.source

    @override_settings(DEBUG=True)
    def test_a_refused_chain_fails_loudly_under_debug(
        self, registry: PageMetadataRegistry, tmp_path: Path
    ) -> None:
        root, leaf = write_page_chain(tmp_path, [("root", ROOT), ("leaf", LEAF)])
        registry.register(root, dict)
        thunk = MetadataThunk(registry, leaf, None, {}, {})
        with pytest.raises(PageMetadataConflictError) as caught:
            thunk.folded()
        assert "Run manage.py check" in caught.value.__notes__[0]

    def test_the_patch_fold_still_raises(
        self, registry: PageMetadataRegistry, tmp_path: Path
    ) -> None:
        """`fold_chain` lets the caller decide, so `Patches.meta` can skip its op."""
        (leaf,) = write_page_chain(tmp_path, [("leaf", PLAIN)])
        registry.register(leaf, lambda: {"title": 3})
        with pytest.raises(PageMetadataShapeError):
            fold_chain(registry, leaf, dep_cache={})


class TestPrefix:
    """The static head of a dynamic chain folds once, a request folds the tail."""

    def test_the_prefix_stops_at_the_first_callable(
        self, registry: PageMetadataRegistry, tmp_path: Path
    ) -> None:
        root, mid, leaf = write_page_chain(
            tmp_path, [("root", ROOT), ("mid", PLAIN), ("leaf", LEAF)]
        )
        registry.register(mid, lambda: {"description": "Mid"}, inherit=True)
        entry = chain_entry(registry, leaf)
        assert [source.file_path for source in entry.tail] == [mid, leaf]
        assert [source.file_path for source in entry.sources] == [root, mid, leaf]
        meta = _fold(registry, leaf)
        assert meta.description == "Mid"
        assert str(meta.title) == "Leaf | Root"

    def test_a_request_reuses_the_memoised_prefix(
        self, registry: PageMetadataRegistry, tmp_path: Path
    ) -> None:
        _root, leaf = write_page_chain(tmp_path, [("root", ROOT), ("leaf", PLAIN)])
        registry.register(leaf, lambda: {"title": "Dynamic"})
        prefix = chain_entry(registry, leaf).prefix
        _fold(registry, leaf)
        _fold(registry, leaf)
        assert chain_entry(registry, leaf).prefix is prefix


class TestMemo:
    """The chain memo validates on the module stamps, the registry and the settings."""

    def test_a_warm_static_chain_resolves_to_the_identical_object(
        self, registry: PageMetadataRegistry, tmp_path: Path
    ) -> None:
        _root, leaf = write_page_chain(tmp_path, [("root", ROOT), ("leaf", LEAF)])
        first = _fold(registry, leaf)
        assert _fold(registry, leaf) is first
        assert static_metadata(registry, leaf) is first

    def test_the_entry_is_valid_right_after_the_walk_loaded_every_module(
        self, registry: PageMetadataRegistry, tmp_path: Path
    ) -> None:
        _root, leaf = write_page_chain(tmp_path, [("root", ROOT), ("leaf", LEAF)])
        first = chain_entry(registry, leaf)
        assert chain_entry(registry, leaf) is first

    def test_a_dynamic_chain_folds_per_call(
        self, registry: PageMetadataRegistry, tmp_path: Path
    ) -> None:
        (leaf,) = write_page_chain(tmp_path, [("leaf", PLAIN)])
        registry.register(leaf, lambda: {"title": "Dynamic"})
        entry = chain_entry(registry, leaf)
        assert entry.folded is None
        assert _fold(registry, leaf) is not _fold(registry, leaf)

    @pytest.mark.parametrize(
        "invalidate",
        [
            lambda registry, leaf: registry.register(leaf, lambda: {"title": "D"}),
            lambda _registry, _leaf: reset_module_memo(),
            lambda registry, _leaf: registry.reset(),
            lambda _registry, leaf: touch_later(leaf, PLAIN) or load_page_module(leaf),
        ],
        ids=["registration", "module_reset", "registry_reset", "own_edit"],
    )
    def test_an_invalidation_rebuilds_the_entry(
        self,
        registry: PageMetadataRegistry,
        tmp_path: Path,
        invalidate: Callable[[PageMetadataRegistry, Path], object],
    ) -> None:
        _root, leaf = write_page_chain(tmp_path, [("root", ROOT), ("leaf", PLAIN)])
        before = chain_entry(registry, leaf)
        invalidate(registry, leaf)
        assert chain_entry(registry, leaf) is not before

    def test_the_same_callable_registered_again_keeps_the_entry(
        self, registry: PageMetadataRegistry, tmp_path: Path
    ) -> None:
        (leaf,) = write_page_chain(tmp_path, [("leaf", PLAIN)])

        def meta() -> dict[str, str]:
            return {"title": "Dynamic"}

        registry.register(leaf, meta)
        before = chain_entry(registry, leaf)
        registry.register(leaf, meta)
        assert _kept(chain_entry(registry, leaf), before)

    def test_a_load_elsewhere_in_the_tree_keeps_the_entry(
        self, registry: PageMetadataRegistry, tmp_path: Path
    ) -> None:
        _root, leaf = write_page_chain(tmp_path, [("root", ROOT), ("leaf", LEAF)])
        (other,) = write_page_chain(tmp_path / "root", [("other", MID)])
        before = chain_entry(registry, leaf)
        load_page_module(other)
        assert _kept(chain_entry(registry, leaf), before)

    def test_a_revalidated_entry_skips_the_stamps_on_the_next_read(
        self, registry: PageMetadataRegistry, tmp_path: Path
    ) -> None:
        _root, leaf = write_page_chain(tmp_path, [("root", ROOT), ("leaf", LEAF)])
        (other,) = write_page_chain(tmp_path / "root", [("other", MID)])
        chain_entry(registry, leaf)
        generation = module_generation()
        load_page_module(other)
        assert module_generation() != generation
        after = chain_entry(registry, leaf)
        with patch("next.pages.loaders.module_stamps", side_effect=AssertionError):
            assert chain_entry(registry, leaf) is after

    def test_an_evicted_ancestor_module_leaves_other_chains_alone(
        self,
        registry: PageMetadataRegistry,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setattr(loaders_module, "_MODULE_MEMO", BoundedCache(maxsize=1))
        root, leaf = write_page_chain(tmp_path, [("root", ROOT), ("leaf", LEAF)])
        (other,) = write_page_chain(tmp_path / "root", [("other", MID)])
        before = chain_entry(registry, leaf)
        load_page_module(other)
        load_page_module(root)
        assert _kept(chain_entry(registry, leaf), before)

    def test_an_edited_ancestor_rebuilds_once_it_loads_again(
        self, registry: PageMetadataRegistry, tmp_path: Path
    ) -> None:
        root, leaf = write_page_chain(tmp_path, [("root", ROOT), ("leaf", LEAF)])
        before = chain_entry(registry, leaf)
        touch_later(root, 'metadata = {"description": "Edited"}\n')
        assert chain_entry(registry, leaf) is before
        load_page_module(root)
        assert static_metadata(registry, leaf).description == "Edited"

    @override_settings(DEBUG=True)
    def test_under_a_watch_an_edited_ancestor_rebuilds_on_the_next_read(
        self, registry: PageMetadataRegistry, tmp_path: Path
    ) -> None:
        root, leaf = write_page_chain(tmp_path, [("root", ROOT), ("leaf", LEAF)])
        chain_entry(registry, leaf)
        touch_later(root, 'metadata = {"description": "Edited"}\n')
        assert static_metadata(registry, leaf).description == "Edited"

    @override_settings(DEBUG=True)
    def test_under_a_watch_an_untouched_chain_stays(
        self, registry: PageMetadataRegistry, tmp_path: Path
    ) -> None:
        _root, leaf = write_page_chain(tmp_path, [("root", ROOT), ("leaf", LEAF)])
        before = chain_entry(registry, leaf)
        assert chain_entry(registry, leaf) is before

    def test_a_deleted_ancestor_drops_out(
        self, registry: PageMetadataRegistry, tmp_path: Path
    ) -> None:
        root, leaf = write_page_chain(tmp_path, [("root", ROOT), ("leaf", LEAF)])
        chain_entry(registry, leaf)
        root.unlink()
        load_page_module(root)
        assert static_metadata(registry, leaf).description is None

    def test_a_settings_override_rebuilds_the_entry(
        self, registry: PageMetadataRegistry, tmp_path: Path
    ) -> None:
        _root, leaf = write_page_chain(tmp_path, [("root", ROOT), ("leaf", LEAF)])
        before = chain_entry(registry, leaf)
        with override_settings(
            NEXT_FRAMEWORK={"METADATA": {"DEFAULTS": SITE_DEFAULTS}}
        ):
            inside = chain_entry(registry, leaf)
            assert inside is not before
            assert inside.site is site_segment()
            assert inside.static.site_name == "Acme"
        assert chain_entry(registry, leaf) is not inside


class TestConcurrentReads:
    """Concurrent threads reading one unbuilt chain all receive the same fold."""

    THREADS = 8

    def _read_together(self, read: Callable[[], Metadata]) -> list[Metadata]:
        barrier = threading.Barrier(self.THREADS)

        def at_once(_index: int) -> Metadata:
            barrier.wait()
            return read()

        with ThreadPoolExecutor(self.THREADS) as pool:
            return list(pool.map(at_once, range(self.THREADS)))

    def test_a_cold_static_chain_folds_alike_in_every_thread(
        self, registry: PageMetadataRegistry, tmp_path: Path
    ) -> None:
        _root, leaf = write_page_chain(tmp_path, [("root", ROOT), ("leaf", LEAF)])
        folds = self._read_together(lambda: static_metadata(registry, leaf))
        assert {(str(meta.title), meta.description) for meta in folds} == {
            ("Leaf | Root", "Root")
        }
        settled = chain_entry(registry, leaf)
        assert chain_entry(registry, leaf) is settled

    def test_a_dynamic_chain_runs_its_callable_once_per_fold(
        self, registry: PageMetadataRegistry, tmp_path: Path
    ) -> None:
        _root, leaf = write_page_chain(tmp_path, [("root", ROOT), ("leaf", PLAIN)])
        calls: list[int] = []
        lock = threading.Lock()

        def meta() -> dict[str, str]:
            with lock:
                calls.append(1)
            return {"title": "Dynamic"}

        registry.register(leaf, meta)
        folds = self._read_together(lambda: _fold(registry, leaf))
        assert {str(meta.title) for meta in folds} == {"Dynamic | Root"}
        assert len(calls) == self.THREADS


class TestConcurrentInvalidation:
    """Concurrent readers never read an older fold once an edit of an ancestor loaded.

    A read of a partially written file folds to no description, accepted mid-write only.
    """

    READERS = 6
    EDITS = RACE_EDITS

    def _race(
        self,
        registry: PageMetadataRegistry,
        tmp_path: Path,
        settle: Callable[[Path], object],
    ) -> list[tuple[int, int]]:
        root, leaf = write_page_chain(tmp_path, [("root", ROOT), ("leaf", LEAF)])
        settled = [0]
        stop = threading.Event()
        caught_up = [threading.Event() for _ in range(self.READERS)]
        seen: list[tuple[int, int]] = []
        lock = threading.Lock()

        def read(done: threading.Event) -> None:
            # Signal even when a read raises, so the editor never waits on a dead reader.
            try:
                while not stop.is_set():
                    floor = settled[0]
                    description = static_metadata(registry, leaf).description
                    edit = STAGES.get(description, HALF_WRITTEN)
                    with lock:
                        seen.append((floor, edit))
                    if floor == self.EDITS:
                        done.set()
                    # Release the GIL, otherwise the reader loops block the editor thread.
                    time.sleep(0)
            finally:
                done.set()

        with ThreadPoolExecutor(self.READERS) as pool:
            readers = [pool.submit(read, done) for done in caught_up]
            for edit in range(1, self.EDITS + 1):
                touch_later(root, f'metadata = {{"description": "v{edit}"}}\n')
                settle(root)
                settled[0] = edit
            for done in caught_up:
                done.wait()
            stop.set()
            for reader in readers:
                reader.result()
        return seen

    def _stale(self, seen: list[tuple[int, int]]) -> list[tuple[int, int]]:
        """Return the reads that saw an edit older than the one loaded before they began."""
        return [
            (floor, edit)
            for floor, edit in seen
            if (edit == HALF_WRITTEN and floor == self.EDITS)
            or (edit != HALF_WRITTEN and edit < floor)
        ]

    def test_a_load_settles_every_later_read(
        self, registry: PageMetadataRegistry, tmp_path: Path
    ) -> None:
        seen = self._race(registry, tmp_path, load_page_module)
        assert self._stale(seen)[:3] == []
        assert (self.EDITS, self.EDITS) in seen

    @override_settings(DEBUG=True)
    def test_under_a_watch_the_write_settles_every_later_read(
        self, registry: PageMetadataRegistry, tmp_path: Path
    ) -> None:
        seen = self._race(registry, tmp_path, lambda _root: None)
        assert self._stale(seen)[:3] == []
        assert (self.EDITS, self.EDITS) in seen

    @pytest.mark.parametrize("watched", [False, True], ids=["loaded", "watched"])
    def test_a_load_landing_mid_build_is_not_vouched_for(
        self, registry: PageMetadataRegistry, tmp_path: Path, *, watched: bool
    ) -> None:
        root, leaf = write_page_chain(tmp_path, [("root", ROOT), ("leaf", LEAF)])
        edits: list[Path] = []

        def edited_mid_build(raw: object, *, source: str) -> Segment:
            if not edits:
                edits.append(root)
                touch_later(root, 'metadata = {"description": "v1"}\n')
                load_page_module(root)
            return normalize_metadata(raw, source=source)

        with override_settings(DEBUG=watched):
            with patch(
                "next.pages.metadata.chain.normalize_metadata", edited_mid_build
            ):
                assert static_metadata(registry, leaf).description == "Root"
            assert static_metadata(registry, leaf).description == "v1"


class TestOrigins:
    """`metadata_origins` names the source of every key, callables last."""

    def test_each_key_names_its_page_and_the_callable_is_listed(
        self, registry: PageMetadataRegistry, tmp_path: Path
    ) -> None:
        root, leaf = write_page_chain(tmp_path, [("root", ROOT), ("leaf", PLAIN)])

        def leaf_meta() -> dict[str, str]:
            return {"title": "Dynamic"}

        registry.register(leaf, leaf_meta)
        with override_settings(NEXT_FRAMEWORK={"SITE": {"NAME": "Acme"}}):
            origins = metadata_origins(chain_entry(registry, leaf))
        assert origins == (
            MetadataOrigin("description", str(root)),
            MetadataOrigin("site_name", SITE_NAME_SOURCE),
            MetadataOrigin("title", str(root)),
            MetadataOrigin("*", f"leaf_meta in {leaf}"),
        )


class TestThunk:
    """The render-time thunk folds against the context passed to each read."""

    def test_each_read_folds_against_the_context_it_is_handed(
        self, registry: PageMetadataRegistry, tmp_path: Path
    ) -> None:
        (leaf,) = write_page_chain(tmp_path, [("leaf", PLAIN)])
        registry.register(leaf, lambda user: {"title": user})
        thunk = MetadataThunk(registry, leaf, None, {}, {})
        assert thunk.folded() is None
        assert thunk.fold({"user": "Ann"}).title == "Ann"
        assert thunk.fold({"user": "Bob"}).title == "Bob"

    def test_a_static_thunk_answers_the_fold_without_a_context(
        self, registry: PageMetadataRegistry, tmp_path: Path
    ) -> None:
        (leaf,) = write_page_chain(tmp_path, [("leaf", LEAF)])
        thunk = MetadataThunk(registry, leaf, None, {}, {})
        assert thunk.folded() is thunk.fold({})
