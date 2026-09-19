from __future__ import annotations

from dataclasses import FrozenInstanceError
from typing import TYPE_CHECKING

import pytest
from django.core.exceptions import SuspiciousFileOperation

from next.static import (
    KindRegistry,
    StaticAsset,
    StaticAssetTraversalError,
    default_kinds,
    static_name,
)
from next.static.assets import StaticNamespace, _with_query_param, with_version
from tests.support import (
    STATIC_NAME_CASES,
    VERSIONED_URL_CASES,
    StaticNameCase,
    VersionedUrlCase,
)


if TYPE_CHECKING:
    from pathlib import Path


CSS_URL = "https://example.com/a.css"

CONTAINED_NAME_CASES = [case for case in STATIC_NAME_CASES if not case.traverses]
TRAVERSING_NAME_CASES = [case for case in STATIC_NAME_CASES if case.traverses]


class TestStaticAsset:
    """StaticAsset is a slotted, frozen value object."""

    def test_defaults(self) -> None:
        asset = StaticAsset(url=CSS_URL, kind="css")
        assert asset.url == CSS_URL
        assert asset.kind == "css"
        assert asset.source_path is None
        assert asset.inline is None

    def test_with_source_path(self, tmp_path: Path) -> None:
        asset = StaticAsset(url=CSS_URL, kind="css", source_path=tmp_path)
        assert asset.source_path == tmp_path

    def test_is_frozen(self) -> None:
        asset = StaticAsset(url=CSS_URL, kind="css")
        with pytest.raises(FrozenInstanceError, match="cannot assign to field 'url'"):
            asset.url = "mutated"  # type: ignore[misc]


class TestKindRegistryStartsEmpty:
    """A fresh registry ships with zero registered kinds."""

    def test_empty_after_init(self) -> None:
        reg = KindRegistry()
        assert reg.kinds() == ()
        assert "css" not in reg


class TestKindRegistryRegister:
    """register stores extension, slot, and renderer per kind."""

    def test_register_makes_lookups_succeed(self) -> None:
        reg = KindRegistry()
        reg.register("css", extension=".css", slot="styles", renderer="render_link_tag")
        assert reg.extension("css") == ".css"
        assert reg.slot("css") == "styles"
        assert reg.renderer("css") == "render_link_tag"
        assert "css" in reg

    def test_register_is_idempotent_for_identical_params(self) -> None:
        reg = KindRegistry()
        reg.register("css", extension=".css", slot="styles", renderer="render_link_tag")
        reg.register("css", extension=".css", slot="styles", renderer="render_link_tag")
        assert reg.kinds() == ("css",)

    def test_register_stores_inline_tag(self) -> None:
        reg = KindRegistry()
        reg.register(
            "css",
            extension=".css",
            slot="styles",
            renderer="render_link_tag",
            inline_tag="style",
        )
        assert reg.inline_tag("css") == "style"

    def test_inline_tag_defaults_to_none(self) -> None:
        reg = KindRegistry()
        reg.register("css", extension=".css", slot="styles", renderer="render_link_tag")
        assert reg.inline_tag("css") is None

    def test_inline_tag_none_for_unregistered_kind(self) -> None:
        reg = KindRegistry()
        assert reg.inline_tag("ghost") is None

    def test_register_is_idempotent_with_matching_inline_tag(self) -> None:
        reg = KindRegistry()
        reg.register(
            "css",
            extension=".css",
            slot="styles",
            renderer="render_link_tag",
            inline_tag="style",
        )
        reg.register(
            "css",
            extension=".css",
            slot="styles",
            renderer="render_link_tag",
            inline_tag="style",
        )
        assert reg.kinds() == ("css",)

    def test_register_rejects_conflicting_inline_tag(self) -> None:
        reg = KindRegistry()
        reg.register(
            "css",
            extension=".css",
            slot="styles",
            renderer="render_link_tag",
            inline_tag="style",
        )
        with pytest.raises(ValueError, match="already registered"):
            reg.register(
                "css",
                extension=".css",
                slot="styles",
                renderer="render_link_tag",
                inline_tag="span",
            )

    def test_register_rejects_conflicting_re_registration(self) -> None:
        reg = KindRegistry()
        reg.register("css", extension=".css", slot="styles", renderer="render_link_tag")
        with pytest.raises(ValueError, match="already registered"):
            reg.register(
                "css", extension=".sass", slot="styles", renderer="render_link_tag"
            )

    def test_register_rejects_empty_kind(self) -> None:
        reg = KindRegistry()
        with pytest.raises(ValueError, match="Invalid kind"):
            reg.register("", extension=".x", slot="s", renderer="r")

    def test_register_rejects_non_identifier_kind(self) -> None:
        reg = KindRegistry()
        with pytest.raises(ValueError, match="Invalid kind"):
            reg.register("has-dash", extension=".x", slot="s", renderer="r")

    def test_register_rejects_extension_without_dot(self) -> None:
        reg = KindRegistry()
        with pytest.raises(ValueError, match="must start with"):
            reg.register("foo", extension="foo", slot="s", renderer="r")

    def test_register_rejects_empty_slot(self) -> None:
        reg = KindRegistry()
        with pytest.raises(ValueError, match="Slot name"):
            reg.register("foo", extension=".x", slot="", renderer="r")

    def test_register_rejects_empty_renderer(self) -> None:
        reg = KindRegistry()
        with pytest.raises(ValueError, match="Renderer"):
            reg.register("foo", extension=".x", slot="s", renderer="")


class TestKindRegistryLookups:
    """Lookups raise KeyError on unregistered kinds."""

    def test_extension_raises_for_unknown(self) -> None:
        reg = KindRegistry()
        with pytest.raises(KeyError, match="Unsupported asset kind"):
            reg.extension("ghost")

    def test_slot_raises_for_unknown(self) -> None:
        reg = KindRegistry()
        with pytest.raises(KeyError, match="Unsupported asset kind"):
            reg.slot("ghost")

    def test_renderer_raises_for_unknown(self) -> None:
        reg = KindRegistry()
        with pytest.raises(KeyError, match="Unsupported asset kind"):
            reg.renderer("ghost")

    def test_kind_for_extension(self) -> None:
        reg = KindRegistry()
        reg.register("css", extension=".css", slot="styles", renderer="render_link_tag")
        assert reg.kind_for_extension(".css") == "css"
        assert reg.kind_for_extension(".missing") is None

    def test_kinds_preserves_order(self) -> None:
        reg = KindRegistry()
        reg.register("css", extension=".css", slot="styles", renderer="render_link_tag")
        reg.register(
            "js", extension=".js", slot="scripts", renderer="render_script_tag"
        )
        reg.register("jsx", extension=".jsx", slot="scripts", renderer="render_babel")
        assert reg.kinds() == ("css", "js", "jsx")

    def test_contains_rejects_non_string(self) -> None:
        reg = KindRegistry()
        assert 42 not in reg  # type: ignore[comparison-overlap]


class TestDefaultKinds:
    """The module-level default_kinds is shared across the framework."""

    def test_is_a_kind_registry(self) -> None:
        assert isinstance(default_kinds, KindRegistry)

    def test_bootstrap_registered_css_and_js(self) -> None:
        assert "css" in default_kinds
        assert "js" in default_kinds
        assert default_kinds.slot("css") == "styles"
        assert default_kinds.slot("js") == "scripts"
        assert default_kinds.renderer("css") == "render_link_tag"
        assert default_kinds.renderer("js") == "render_script_tag"
        assert default_kinds.inline_tag("css") == "style"
        assert default_kinds.inline_tag("js") == "script"


class TestKindRegistryLoad:
    """load maps a kind to the client insertion verb of its renderer."""

    @pytest.mark.parametrize(
        ("renderer", "expected"),
        [
            ("render_link_tag", "link"),
            ("render_script_tag", "script"),
            ("render_module_tag", "module"),
        ],
    )
    def test_builtin_renderers_resolve_to_a_verb(
        self, renderer: str, expected: str
    ) -> None:
        reg = KindRegistry()
        reg.register("thing", extension=".thing", slot="scripts", renderer=renderer)
        assert reg.load("thing") == expected

    def test_custom_renderer_has_no_verb(self) -> None:
        reg = KindRegistry()
        reg.register("jsx", extension=".jsx", slot="scripts", renderer="render_babel")
        assert reg.load("jsx") is None

    def test_unregistered_kind_has_no_verb(self) -> None:
        assert KindRegistry().load("whatever") is None

    def test_default_kinds_cover_every_builtin(self) -> None:
        assert default_kinds.load("css") == "link"
        assert default_kinds.load("js") == "script"
        assert default_kinds.load("module") == "module"


class TestKindRegistryInlineLoad:
    """The inline form only carries a verb the full render agrees with."""

    def test_builtin_inline_pairs_keep_their_verb(self) -> None:
        assert default_kinds.load("css", inline=True) == "link"
        assert default_kinds.load("js", inline=True) == "script"

    def test_module_inline_body_has_no_verb(self) -> None:
        assert default_kinds.load("module", inline=True) is None

    def test_kind_without_inline_tag_has_no_inline_verb(self) -> None:
        reg = KindRegistry()
        reg.register(
            "snippet",
            extension=".snippet",
            slot="scripts",
            renderer="render_script_tag",
        )
        assert reg.load("snippet") == "script"
        assert reg.load("snippet", inline=True) is None

    def test_mismatched_inline_tag_has_no_inline_verb(self) -> None:
        reg = KindRegistry()
        reg.register(
            "banner",
            extension=".banner",
            slot="styles",
            renderer="render_link_tag",
            inline_tag="template",
        )
        assert reg.load("banner") == "link"
        assert reg.load("banner", inline=True) is None

    def test_custom_renderer_has_no_inline_verb(self) -> None:
        reg = KindRegistry()
        reg.register(
            "jsx",
            extension=".jsx",
            slot="scripts",
            renderer="render_babel",
            inline_tag="script",
        )
        assert reg.load("jsx", inline=True) is None

    def test_unregistered_kind_has_no_inline_verb(self) -> None:
        assert KindRegistry().load("whatever", inline=True) is None


class TestStaticNamespace:
    """StaticNamespace exposes string constants for URL construction."""

    def test_next_namespace(self) -> None:
        assert StaticNamespace.NEXT == "next"


class TestStaticName:
    """`static_name` normalises a reference or refuses to leave the static root."""

    @pytest.mark.parametrize("case", CONTAINED_NAME_CASES, ids=lambda case: case.id)
    def test_a_contained_reference_answers_with_its_normalised_name(
        self, case: StaticNameCase
    ) -> None:
        assert static_name(case.reference) == case.name

    @pytest.mark.parametrize("case", TRAVERSING_NAME_CASES, ids=lambda case: case.id)
    def test_a_reference_leaving_the_root_is_refused(
        self, case: StaticNameCase
    ) -> None:
        with pytest.raises(StaticAssetTraversalError) as excinfo:
            static_name(case.reference)

        assert excinfo.value.reference == case.reference
        assert repr(case.reference) in str(excinfo.value)

    def test_the_refusal_reads_as_a_suspicious_file_operation(self) -> None:
        """Django answers a bad request for the base class, which is what this is."""
        assert issubclass(StaticAssetTraversalError, SuspiciousFileOperation)


class TestWithVersion:
    """`with_version` stamps the `v` query parameter a project pins its assets to."""

    @pytest.mark.parametrize("case", VERSIONED_URL_CASES, ids=lambda case: case.id)
    def test_the_version_lands_where_the_url_shape_puts_it(
        self, case: VersionedUrlCase
    ) -> None:
        assert with_version(case.url, case.version) == case.expected

    def test_a_second_stamp_replaces_the_pair_the_first_left(self) -> None:
        assert with_version(with_version("/a.css", "1"), "2") == "/a.css?v=2"

    def test_a_valueless_pair_named_v_is_replaced_rather_than_kept(self) -> None:
        assert with_version("/a.css?v&x=1", "7") == "/a.css?x=1&v=7"


class TestWithQueryParam:
    """The query rewriter `with_version` is built on, asked for its own pair."""

    def test_a_url_without_a_query_gains_the_pair(self) -> None:
        """The only pair on the URL is the one the caller asked for."""
        assert _with_query_param("/static/a.css", "v", "7") == "/static/a.css?v=7"

    def test_an_existing_pair_of_that_name_is_replaced(self) -> None:
        """Exactly one pair of the name survives, whatever the URL arrived with."""
        assert _with_query_param("/a.css?v=1", "v", "2") == "/a.css?v=2"

    def test_every_repeat_of_the_name_is_replaced_by_the_single_pair(self) -> None:
        """A URL that already doubled the name leaves with one pair, not three."""
        assert _with_query_param("/a.css?v=1&v=2", "v", "3") == "/a.css?v=3"

    def test_a_valueless_pair_of_that_name_is_replaced_too(self) -> None:
        """A bare `?v` names the same parameter, so the set overwrites it."""
        assert _with_query_param("/a.css?v&x=1", "v", "7") == "/a.css?x=1&v=7"

    def test_a_percent_encoded_name_is_matched_after_it_is_decoded(self) -> None:
        """The wire spelling of the name varies, so the comparison unquotes first."""
        assert _with_query_param("/a.css?a%20b=1", "a b", "2") == "/a.css?a%20b=2"

    def test_the_neighbouring_pairs_reach_the_document_as_authored(self) -> None:
        """No round trip through a parser, so `%20` never becomes `+`."""
        assert _with_query_param("/a.css?a=b%20c&flag", "v", "7") == (
            "/a.css?a=b%20c&flag&v=7"
        )

    def test_an_empty_pair_between_separators_is_dropped(self) -> None:
        """An empty pair carries nothing, so it is not worth spelling back out."""
        assert _with_query_param("/a.css?a=1&&b=2", "v", "7") == "/a.css?a=1&b=2&v=7"

    def test_the_value_is_quoted(self) -> None:
        """A reserved character in the value would otherwise split the query."""
        assert _with_query_param("/a.css", "v", "a&b") == "/a.css?v=a%26b"

    def test_the_key_is_quoted_whole(self) -> None:
        """Nothing in a key stays safe, so even a slash is escaped."""
        assert _with_query_param("/a.css", "a/b", "1") == "/a.css?a%2Fb=1"

    def test_the_scheme_host_and_fragment_survive(self) -> None:
        """Only the query is rebuilt, so the rest of the URL passes through."""
        assert _with_query_param("https://cdn/a.css#top", "v", "7") == (
            "https://cdn/a.css?v=7#top"
        )
