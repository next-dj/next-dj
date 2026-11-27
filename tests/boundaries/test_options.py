import pytest

from next.boundaries import BoundaryOptionError
from next.boundaries.options import (
    DEFAULT_TAG,
    RESERVED_OPTIONS,
    BoundaryOptions,
    DeferMode,
    WrapperTag,
)


class TestVocabulary:
    def test_the_modes_are_the_five_a_boundary_takes(self) -> None:
        assert list(DeferMode) == ["stream", "load", "visible", "manual", "inline"]

    def test_the_reserved_options_are_the_tag_keywords(self) -> None:
        assert frozenset({"defer", "timeout", "reload", "delay"}) == RESERVED_OPTIONS


class TestBoundaryOptions:
    def test_the_defaults_stream_in_a_div(self) -> None:
        options = BoundaryOptions.parse()
        assert options == BoundaryOptions(mode=DeferMode.STREAM)
        assert (options.tag, options.tag_declared) == (DEFAULT_TAG, False)

    @pytest.mark.parametrize(
        ("tag", "held"),
        [("div", "div"), ("section", "section"), ("my-card", "my-card"), (None, None)],
    )
    def test_a_written_tag_is_declared(self, tag: str | None, held) -> None:
        options = BoundaryOptions.parse(tag=tag)
        assert (options.tag, options.tag_declared) == (held, True)

    def test_the_site_tag_is_left_undeclared(self) -> None:
        options = BoundaryOptions.parse(tag=WrapperTag.SITE)
        assert (options.tag, options.tag_declared) == ("div", False)

    @pytest.mark.parametrize(
        "tag",
        [
            "script",
            "SCRIPT",
            "style",
            "textarea",
            "title",
            "template",
            "noscript",
            "iframe",
            "object",
            "embed",
            "img",
            "br",
            "input",
            "hr",
            "xmp",
            "html",
        ],
    )
    def test_a_tag_that_cannot_wrap_markup_is_refused(self, tag: str) -> None:
        with pytest.raises(BoundaryOptionError, match="cannot wrap the loading HTML"):
            BoundaryOptions.parse(tag=tag)

    @pytest.mark.parametrize(
        "tokens", [{"max_age": 1}, {"max_bytes": 64, "max_age": 60}, {"binding": "x"}]
    )
    def test_usable_overrides_pass_sorted(self, tokens: dict) -> None:
        assert BoundaryOptions.parse(tokens=tokens).tokens == tuple(
            sorted(tokens.items())
        )

    def test_a_full_declaration_is_parsed(self) -> None:
        options = BoundaryOptions.parse(
            "visible",
            timeout="3s",
            props=["sku", "range"],
            tag=None,
            parallel=False,
            tokens={"max_age": 60, "binding": "user", "max_bytes": 1},
        )
        assert options == BoundaryOptions(
            mode=DeferMode.VISIBLE,
            timeout=3.0,
            props=("sku", "range"),
            tag=None,
            tag_declared=True,
            parallel=False,
            tokens=(("binding", "user"), ("max_age", 60), ("max_bytes", 1)),
        )

    def test_no_mode_declares_props_alone(self) -> None:
        assert BoundaryOptions.parse(None, props=("sku",)).mode is None

    def test_options_are_hashable(self) -> None:
        options = BoundaryOptions.parse(tokens={"max_age": 5})
        assert hash(options) == hash(BoundaryOptions.parse(tokens={"max_age": 5}))

    @pytest.mark.parametrize(
        ("options", "message"),
        [
            ({"mode": "lazy"}, "mode must be one of"),
            ({"mode": 1}, "mode must be one of"),
            ({"timeout": 0}, "timeout must be above zero"),
            ({"timeout": "soon"}, "duration literal"),
            ({"props": "sku"}, "props takes a tuple"),
            ({"props": (1,)}, "Python identifier"),
            ({"props": ("a-b",)}, "Python identifier"),
            ({"props": ("class",)}, "Python identifier"),
            ({"props": ("timeout",)}, "reserved by the component tag"),
            ({"props": ("reload",)}, "reserved by the component tag"),
            ({"props": ("a", "a")}, "declared twice"),
            ({"tag": "1x"}, "tag names an HTML element"),
            ({"tag": 3}, "tag names an HTML element"),
            ({"parallel": 1}, "parallel must be True or False"),
            ({"parallel": None}, "parallel must be True or False"),
            ({"tokens": [("max_age", 1)]}, "tokens takes a mapping"),
            ({"tokens": {"epoch": 1}}, "tokens takes the keys"),
            ({"tokens": {"binding": ""}}, "unusable value"),
            ({"tokens": {"bucket": 60}}, "tokens takes the keys"),
            ({"tokens": {"max_age": True}}, "unusable value"),
            ({"tokens": {"max_bytes": 0}}, "unusable value"),
            ({"tokens": {"max_age": -1}}, "unusable value"),
            ({"tokens": {"max_age": 0}}, "unusable value"),
            ({"timeout": 10**400}, "up to one day"),
        ],
    )
    def test_an_unusable_option_is_refused(self, options, message) -> None:
        with pytest.raises(BoundaryOptionError, match=message):
            BoundaryOptions.parse(**options)
