import pytest
from django.template import Context

from next.components.stash import (
    in_loading,
    loading_content,
    stash_loading,
    stash_markup,
    stashing,
)
from next.defer import (
    Addressing,
    BoundaryFailure,
    BoundaryOptions,
    DeferMode,
    DeferredCall,
    ErrorKind,
    Site,
)


class _Renderer:
    """A call renderer whose loading UI is fixed, counting how often it renders."""

    def __init__(self, loading: str) -> None:
        self.html = loading
        self.calls = 0

    def loading(self, _context: Context) -> tuple[str, BoundaryFailure | None]:
        self.calls += 1
        return self.html, None

    def body(self, _context: Context) -> tuple[str, BoundaryFailure | None]:
        return "B", None

    def error(self, _context: Context, kind: ErrorKind) -> tuple[str, BoundaryFailure]:
        return "E", BoundaryFailure(kind, "d", None, retry=False)


def _call(key: str, renderer: _Renderer) -> DeferredCall:
    return DeferredCall(
        site=Site(Addressing.COMPONENT, "card", DeferMode.INLINE, "page.djx"),
        options=BoundaryOptions(),
        key=key,
        tag="div",
        source=None,
        renderer=renderer,
    )


class TestStashMarkup:
    """A stash is a `<template>` named by its zone or boundary key."""

    def test_the_name_is_escaped(self) -> None:
        assert stash_markup('a"b', "<p>x</p>") == (
            '<template data-next-loading="a&quot;b"><p>x</p></template>'
        )


class TestStashLoading:
    """Inside a `stashing` block each key's loading UI is stashed once."""

    def test_outside_a_block_nothing_is_stashed(self) -> None:
        renderer = _Renderer("<i>L</i>")
        assert stash_loading(Context(), _call("k1", renderer)) == ""
        assert renderer.calls == 0

    def test_a_key_is_stashed_once(self) -> None:
        renderer = _Renderer("<i>L</i>")
        call = _call("k1", renderer)
        with stashing():
            first = stash_loading(Context(), call)
            second = stash_loading(Context(), call)
        assert first == '<template data-next-loading="k1"><i>L</i></template>'
        assert second == ""
        assert renderer.calls == 1

    def test_a_nested_block_joins_the_outer_one(self) -> None:
        call = _call("k1", _Renderer("<i>L</i>"))
        with stashing():
            with stashing():
                inner = stash_loading(Context(), call)
            outer = stash_loading(Context(), call)
        assert inner.startswith("<template")
        assert outer == ""

    def test_the_keys_end_with_the_block(self) -> None:
        call = _call("k1", _Renderer("<i>L</i>"))
        with stashing():
            stash_loading(Context(), call)
        with stashing():
            assert stash_loading(Context(), call).startswith("<template")

    def test_a_blank_loading_ui_is_not_stashed(self) -> None:
        with stashing():
            assert stash_loading(Context(), _call("k1", _Renderer(" \n"))) == ""


class TestStashContainment:
    """No closing `</template` inside the loading UI ends the stash around it."""

    def test_an_unmatched_end_tag_turns_to_text(self) -> None:
        assert stash_markup("k", "<i>x</i></template><script>1</script>") == (
            '<template data-next-loading="k"><i>x</i>&lt;/template>'
            "<script>1</script></template>"
        )

    def test_an_end_tag_in_any_case_turns_to_text(self) -> None:
        assert stash_markup("k", "</TEMPLATE >x") == (
            '<template data-next-loading="k">&lt;/TEMPLATE >x</template>'
        )

    @pytest.mark.parametrize(
        "html",
        [
            pytest.param(
                '<template id="n"><p>n</p></template><p>after</p>', id="nested-template"
            ),
            pytest.param("<template-row></template-row>", id="custom-element"),
        ],
    )
    def test_markup_that_ends_no_stash_is_left_alone(self, html: str) -> None:
        assert stash_markup("k", html) == (
            f'<template data-next-loading="k">{html}</template>'
        )

    def test_an_open_nested_template_is_closed(self) -> None:
        assert stash_markup("k", "<template><p>open") == (
            '<template data-next-loading="k"><template><p>open</template></template>'
        )


class TestLoadingContent:
    """A block renders as loading content, the flag reset after it."""

    def test_the_flag_holds_inside_the_block_only(self) -> None:
        assert in_loading() is False
        with loading_content():
            assert in_loading() is True
            with loading_content():
                assert in_loading() is True
            assert in_loading() is True
        assert in_loading() is False
