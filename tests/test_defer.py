import time

import pytest
from django.template import Context, Template
from django.test import override_settings

from next.defer import (
    DEFAULT_TAG,
    DEFERRALS_KEY,
    MAX_DURATION,
    RESERVED_OPTIONS,
    BoundaryError,
    BoundaryOptionError,
    BoundaryOptions,
    DebugBoundaryError,
    DeferMode,
    Delivery,
    ErrorKind,
    WrapperTag,
    deadline,
    parse_duration,
    remaining,
    stable_key,
)


class TestVocabulary:
    def test_the_modes_are_the_five_a_boundary_takes(self) -> None:
        assert list(DeferMode) == ["stream", "load", "visible", "manual", "inline"]

    def test_the_deliveries_are_the_four_a_response_takes(self) -> None:
        assert list(Delivery) == ["stream", "inline", "shell", "prefetch"]

    def test_the_error_kinds_include_the_client_ones(self) -> None:
        assert list(ErrorKind) == [
            "error",
            "timeout",
            "overloaded",
            "not_found",
            "forbidden",
            "bad_request",
            "incomplete",
            "network",
        ]

    @pytest.mark.parametrize(
        ("kind", "status"),
        [
            (ErrorKind.NOT_FOUND, 404),
            (ErrorKind.FORBIDDEN, 403),
            (ErrorKind.BAD_REQUEST, 400),
            (ErrorKind.OVERLOADED, 503),
            (ErrorKind.ERROR, None),
            (ErrorKind.TIMEOUT, None),
            (ErrorKind.NETWORK, None),
        ],
    )
    def test_each_kind_names_its_status(self, kind: ErrorKind, status) -> None:
        assert kind.status == status

    def test_the_reserved_options_are_the_tag_keywords(self) -> None:
        assert frozenset({"defer", "timeout", "reload", "delay"}) == RESERVED_OPTIONS

    def test_the_sink_has_its_own_context_key(self) -> None:
        assert DEFERRALS_KEY == "_next_deferrals"


class TestParseDuration:
    @pytest.mark.parametrize(
        ("value", "seconds"),
        [
            (0, 0.0),
            (3, 3.0),
            (0.25, 0.25),
            ("2s", 2.0),
            ("1.5s", 1.5),
            ("500ms", 0.5),
            ("0ms", 0.0),
            (86_400, 86_400.0),
            ("86400s", 86_400.0),
        ],
    )
    def test_a_usable_duration_reads_as_seconds(self, value, seconds) -> None:
        assert parse_duration(value) == seconds

    @pytest.mark.parametrize(
        "value",
        [True, None, [1], -1, float("inf"), float("nan"), "5", "2 s", "-2s", "2m", ""],
    )
    def test_an_unusable_duration_is_refused(self, value) -> None:
        with pytest.raises(BoundaryOptionError, match="duration"):
            parse_duration(value)

    @pytest.mark.parametrize(
        ("value", "shown"),
        [
            pytest.param(86_401, "86401", id="above-a-day"),
            pytest.param(86_400.5, "86400.5", id="float-above-a-day"),
            pytest.param("86401s", "'86401s'", id="literal-above-a-day"),
            pytest.param("9" * 400 + "s", "'99999", id="infinite-literal"),
            pytest.param(10**400, "a very large integer", id="overflowing-integer"),
            pytest.param(-(10**400), "a very large integer", id="negative-overflow"),
        ],
    )
    def test_a_duration_past_one_day_is_refused(self, value, shown: str) -> None:
        with pytest.raises(BoundaryOptionError, match="up to one day") as caught:
            parse_duration(value)
        assert shown in str(caught.value)

    def test_the_cap_is_one_day(self) -> None:
        assert MAX_DURATION == 86_400


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
        "tokens",
        [
            {"bucket": 60, "max_age": 60},
            {"bucket": 600, "max_age": 60},
            {"bucket": 1, "max_age": 1},
        ],
    )
    def test_a_bucket_from_max_age_is_refused(self, tokens: dict) -> None:
        with pytest.raises(BoundaryOptionError, match="must stay below max_age"):
            BoundaryOptions.parse(tokens=tokens)

    @pytest.mark.parametrize(
        "tokens",
        [
            {"bucket": 59, "max_age": 60},
            {"bucket": None, "max_age": 60},
            {"bucket": 600},
            {"bucket": 0, "max_age": 1},
            {"max_age": 1},
        ],
    )
    def test_a_bucket_below_max_age_passes(self, tokens: dict) -> None:
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
            tokens={"max_age": 60, "binding": "user", "bucket": None, "max_bytes": 1},
        )
        assert options == BoundaryOptions(
            mode=DeferMode.VISIBLE,
            timeout=3.0,
            props=("sku", "range"),
            tag=None,
            tag_declared=True,
            parallel=False,
            tokens=(
                ("binding", "user"),
                ("bucket", None),
                ("max_age", 60),
                ("max_bytes", 1),
            ),
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
            ({"props": ("timeout",)}, "boundary option of the component tag"),
            ({"props": ("a", "a")}, "declared twice"),
            ({"tag": "1x"}, "tag names an HTML element"),
            ({"tag": 3}, "tag names an HTML element"),
            ({"parallel": 1}, "parallel must be None, True or False"),
            ({"tokens": [("max_age", 1)]}, "tokens takes a mapping"),
            ({"tokens": {"epoch": 1}}, "tokens takes the keys"),
            ({"tokens": {"binding": ""}}, "unusable value"),
            ({"tokens": {"bucket": -1}}, "unusable value"),
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


class TestPaced:
    """A call site's `reload=` and `delay=` are durations kept on the options."""

    def test_both_are_read_as_seconds(self) -> None:
        options = BoundaryOptions.parse().paced(reload="2s", delay="150ms")
        assert (options.reload, options.delay) == (2.0, 0.15)

    def test_absent_values_stay_unset(self) -> None:
        assert BoundaryOptions.parse().paced() == BoundaryOptions.parse()

    def test_a_misfit_is_refused(self) -> None:
        with pytest.raises(BoundaryOptionError, match="duration"):
            BoundaryOptions.parse().paced(delay="soon")


class TestBoundaryError:
    def test_the_detail_is_absent_outside_debug(self) -> None:
        error = BoundaryError.of(
            ErrorKind.NOT_FOUND, "a1b2c3d4e5f6", retry=True, message="x", traceback="tb"
        )
        assert error == BoundaryError(ErrorKind.NOT_FOUND, "a1b2c3d4e5f6", 404, True)
        assert not hasattr(error, "message")
        assert not hasattr(error, "traceback")

    def test_a_template_reads_the_absent_detail_as_empty(self) -> None:
        error = BoundaryError.of(
            ErrorKind.ERROR, "a1b2c3d4e5f6", retry=True, message="x", traceback="tb"
        )
        template = Template(
            "{{ error.kind }}:{{ error.message }}{% if error.traceback %}!{% endif %}"
        )
        assert template.render(Context({"error": error})) == "error:"

    @override_settings(DEBUG=True)
    def test_the_detail_is_kept_under_debug(self) -> None:
        error = BoundaryError.of(
            ErrorKind.TIMEOUT, "a1b2c3d4e5f6", retry=False, message="x", traceback="tb"
        )
        assert error == DebugBoundaryError(
            ErrorKind.TIMEOUT, "a1b2c3d4e5f6", None, False, "x", "tb"
        )
        assert "tb" not in repr(error)


class TestRemaining:
    def test_no_deadline_answers_none(self) -> None:
        assert remaining() is None

    def test_a_deadline_answers_the_seconds_left(self) -> None:
        with deadline(time.monotonic() + 60):
            left = remaining()
        assert left is not None
        assert 0 < left <= 60
        assert remaining() is None

    def test_a_passed_deadline_answers_zero(self) -> None:
        with deadline(time.monotonic() - 1):
            assert remaining() == 0.0

    def test_a_nested_none_lifts_the_deadline_for_its_block(self) -> None:
        with deadline(time.monotonic() + 60):
            with deadline(None):
                assert remaining() is None
            assert remaining() is not None


class TestStableKey:
    """The key is eight hex characters of the site, the same in every process."""

    def test_the_key_names_the_site(self) -> None:
        key = stable_key("page.html", 0)
        assert len(key) == 8
        assert key == stable_key("page.html", 0)
        assert key != stable_key("page.html", 1)
        assert key != stable_key("other.html", 0)
        assert stable_key("page.html", "0.1") != key
