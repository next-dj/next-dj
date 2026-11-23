from pathlib import Path

import pytest
from django.test import override_settings

from next.pages import Page, PageMetadataConflictError, page
from next.pages.loaders import load_page_module
from next.pages.manager import reset_metadata_registry
from next.pages.metadata.markers import Segment, TitleSpec
from next.pages.metadata.registry import MetadataRegistrations
from next.testing import reset_check_caches
from tests.support import (
    attribution,
    handler_declared_here,
    resolve_page_metadata,
    touch_later,
    unified_view,
    write_page_chain,
)
from tests.support.pages import page_calls


ROOT = 'metadata = {"title": {"template": "{title} | Root"}, "description": "Root"}\n'


PLAIN = "x = 1\n"


DYNAMIC = """
from next.pages import page

calls = []


@page.metadata
def meta():
    calls.append(1)
    return {"title": "Dynamic"}
"""


TWICE = (
    DYNAMIC
    + """

@page.metadata
def other():
    return {"title": "Other"}
"""
)


ROOT_CALLABLE = """
from next.pages import page


@page.metadata{spelling}
def meta():
    return {{"description": "Root"}}
"""


LEAF_CALLABLE = """
from next.deps import Depends
from next.pages import page

calls = []


@page.metadata
def leaf_meta({parameters}):
    calls.append(1)
    return {{"title": {title}}}
"""


def _leaf_callable(parameters: str = "", title: str = '"Own"') -> str:
    return LEAF_CALLABLE.format(parameters=parameters, title=title)


class TestContextDecorator:
    """`@context` registers a keyed or a dict-merge callable for its declaring file."""

    @pytest.mark.parametrize(
        ("decorator_type", "expected_key"),
        [
            ("with_key", "user_name"),
            ("without_key", None),
            ("without_parentheses", None),
        ],
        ids=["with_key", "without_key", "without_parentheses"],
    )
    def test_context_decorator_variations(
        self, page_instance, decorator_type, expected_key
    ) -> None:
        """``@context`` keys on the declaring file whether or not a key is given."""
        if decorator_type == "with_key":

            @page_instance.context("user_name")
            def get_user_name() -> str:
                return "John Doe"

            func = get_user_name
        elif decorator_type == "without_key":

            @page_instance.context()
            def get_context_data():
                return {"key1": "value1", "key2": "value2"}

            func = get_context_data
        else:

            @page_instance.context
            def get_context_data_bare():
                return {"key1": "value1", "key2": "value2"}

            func = get_context_data_bare

        registry = page_instance.contexts._context_registry
        assert Path(__file__) in registry
        assert expected_key in registry[Path(__file__)]
        entry = registry[Path(__file__)][expected_key]
        assert entry.func == func
        assert entry.inherit_context is False
        assert entry.serialize is False

    def test_context_decorator_with_inherit_context(self, page_instance) -> None:
        """A keyed ``@context`` records ``inherit_context`` on the registry entry."""

        @page_instance.context("inherited_key", inherit_context=True)
        def get_inherited_value() -> str:
            return "inherited_value"

        registry = page_instance.contexts._context_registry
        assert Path(__file__) in registry
        assert "inherited_key" in registry[Path(__file__)]
        entry = registry[Path(__file__)]["inherited_key"]
        assert entry.func == get_inherited_value
        assert entry.inherit_context is True
        assert entry.serialize is False

    def test_context_decorator_without_key_inherit_context(self, page_instance) -> None:
        """A dict-merge ``@context`` registers under ``None``, keeping inheritance."""

        @page_instance.context(inherit_context=True)
        def get_context_data():
            return {"key1": "value1", "key2": "value2"}

        registry = page_instance.contexts._context_registry
        assert Path(__file__) in registry
        assert None in registry[Path(__file__)]
        entry = registry[Path(__file__)][None]
        assert entry.func == get_context_data
        assert entry.inherit_context is True
        assert entry.serialize is False

    def test_context_registry_defaultdict_behavior(
        self, page_instance, test_file_path
    ) -> None:
        """Registering a key creates the per-file entry without pre-seeding it."""
        page_instance.contexts.register_context(
            test_file_path, "test_key", lambda: "test_value"
        )

        assert test_file_path in page_instance.contexts._context_registry
        assert "test_key" in page_instance.contexts._context_registry[test_file_path]


class TestContextMisattribution:
    """A decorator run on a callable declared elsewhere is recorded as such."""

    def test_declaring_file_matching_the_caller_records_nothing(self) -> None:
        """A callable declared where the decorator runs is attributed cleanly."""
        instance = Page()

        @instance.context("here")
        def declared_here() -> str:
            return "value"

        assert instance.contexts.misattributed() == ()

    def test_helper_from_another_module_is_recorded_once(self) -> None:
        """Decorating an imported helper records the pair of files it spans."""
        instance = Page()

        instance.context("greeting")(handler_declared_here)
        instance.context("greeting_again")(handler_declared_here)

        records = instance.contexts.misattributed()
        assert [(r.registered_from, r.declared_in, r.name) for r in records] == [
            (Path(__file__), Path(attribution.__file__), "handler_declared_here")
        ]

    def test_reset_drops_recorded_misattributions(self) -> None:
        """A registry reset clears the diagnostic along with the registrations."""
        instance = Page()
        instance.context("greeting")(handler_declared_here)

        instance.contexts.reset()

        assert instance.contexts.misattributed() == ()


class TestDecorator:
    """`Page.metadata` registers the callable under the file that declared it."""

    @pytest.mark.parametrize(
        ("spelling", "inherited"),
        [("", None), ("()", None), ("(inherit=True)", "Root")],
        ids=["bare", "called", "inherit"],
    )
    def test_only_an_inherited_callable_runs_for_a_descendant(
        self, tmp_path: Path, spelling: str, inherited: str | None
    ) -> None:
        root, leaf = write_page_chain(
            tmp_path,
            [("root", ROOT_CALLABLE.format(spelling=spelling)), ("leaf", PLAIN)],
        )
        assert resolve_page_metadata(page, root).description == "Root"
        assert resolve_page_metadata(page, leaf).description == inherited

    def test_the_decorator_hands_the_callable_back(self) -> None:
        instance = Page()

        def meta() -> dict[str, str]:
            return {}

        assert instance.metadata(meta) is meta
        assert instance.metadata()(meta) is meta
        assert instance.metadata_registrations().names == {Path(__file__): ("meta",)}

    def test_a_local_callable_is_no_misattribution(self) -> None:
        instance = Page()

        @instance.metadata
        def meta() -> dict[str, str]:
            return {}

        assert instance.metadata_registrations().misattributed == ()

    def test_a_helper_from_another_module_is_recorded(self) -> None:
        instance = Page()
        instance.metadata(handler_declared_here)
        records = instance.metadata_registrations().misattributed
        assert [(r.registered_from, r.declared_in, r.name) for r in records] == [
            (Path(__file__), Path(attribution.__file__), "handler_declared_here")
        ]

    def test_two_callables_in_one_page_py_refuse_to_load(self, tmp_path: Path) -> None:
        (leaf,) = write_page_chain(tmp_path, [("leaf", TWICE)])
        module, error = load_page_module(leaf)
        assert module is None
        assert error is not None
        assert isinstance(error.__cause__, PageMetadataConflictError)
        assert "'meta' and 'other'" in str(error.__cause__)

    @override_settings(DEBUG=True)
    def test_an_edited_page_py_registers_again_without_a_conflict(
        self, tmp_path: Path
    ) -> None:
        (leaf,) = write_page_chain(tmp_path, [("leaf", DYNAMIC)])
        load_page_module(leaf)
        touch_later(leaf, DYNAMIC.replace("def meta", "def renamed"))
        module, error = load_page_module(leaf)
        assert module is not None
        assert error is None
        assert page.metadata_registrations().names == {leaf: ("renamed",)}


class TestStaticReads:
    """The request-free reads go through the registry of the instance."""

    def test_static_metadata_folds_the_chain(self, tmp_path: Path) -> None:
        instance = Page()
        _root, leaf = write_page_chain(
            tmp_path, [("root", ROOT), ("leaf", 'metadata = {"title": "Leaf"}\n')]
        )
        meta, refused = instance.static_metadata(leaf)
        assert str(meta.title) == "Leaf | Root"
        assert meta.description == "Root"
        assert not refused

    def test_the_declaration_the_chain_and_the_registrations_read_through(
        self, tmp_path: Path
    ) -> None:
        root, leaf = write_page_chain(
            tmp_path, [("root", ROOT), ("leaf", _leaf_callable())]
        )
        assert page.metadata_declaration(root).raw == {
            "title": {"template": "{title} | Root"},
            "description": "Root",
        }
        declaration = page.metadata_declaration(leaf)
        assert declaration.raw is None
        assert declaration.entry is not None
        assert declaration.entry.inherit is False
        assert [s.file_path for s in page.metadata_chain(leaf).sources] == [root, leaf]
        assert page.metadata_registrations() == MetadataRegistrations(
            names={leaf: ("leaf_meta",)}, misattributed=()
        )

    def test_fold_metadata_lays_an_overlay_under_the_chain_template(
        self, tmp_path: Path
    ) -> None:
        instance = Page()
        _root, leaf = write_page_chain(tmp_path, [("root", ROOT), ("leaf", PLAIN)])
        overlay = Segment("overlay", title=TitleSpec(text="Post"))
        assert str(instance.fold_metadata(leaf, overlay=overlay).title) == (
            "Post | Root"
        )
        assert instance.fold_metadata(leaf).description == "Root"

    def test_an_absolute_overlay_runs_no_inherited_callable_of_its_own_page(
        self, tmp_path: Path
    ) -> None:
        (leaf,) = write_page_chain(tmp_path, [("leaf", _leaf_callable())])
        overlay = Segment("overlay", title=TitleSpec(absolute="Post"))
        assert page.fold_metadata(leaf, overlay=overlay).title == "Post"
        assert page_calls(leaf) == []


class TestReset:
    """The shared registry and its chains drop together on a reset."""

    def test_reset_metadata_registry_drops_registrations_and_chains(
        self, tmp_path: Path
    ) -> None:
        (leaf,) = write_page_chain(tmp_path, [("leaf", DYNAMIC)])
        chain = page.metadata_chain(leaf)
        assert page.metadata_chain(leaf) is chain
        assert page.metadata_registrations().names == {leaf: ("meta",)}

        reset_metadata_registry()

        assert page.metadata_registrations() == MetadataRegistrations({}, ())
        assert page.metadata_declaration(leaf).entry is None
        assert page.metadata_chain(leaf) is not chain

    def test_a_removed_callable_is_gone_after_a_check_reset(
        self, tmp_path: Path
    ) -> None:
        (leaf,) = write_page_chain(tmp_path, [("leaf", DYNAMIC)])
        unified_view(page, leaf)
        assert resolve_page_metadata(page, leaf).title == "Dynamic"

        touch_later(leaf, PLAIN)
        reset_check_caches()

        assert resolve_page_metadata(page, leaf).title is None
