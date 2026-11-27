import re
from collections.abc import Mapping

import pytest
from django.core.exceptions import ImproperlyConfigured
from django.test import override_settings

from next.conf.defaults import DEFAULTS
from next.conf.defer import (
    SHAPE,
    backend_count,
    backend_option,
    backend_paths,
    backend_seconds,
    deadline_mode,
    defer_count,
    defer_option,
    defer_read,
    defer_seconds,
    entry_options,
    long_token_age,
    parallel,
    sqlite_parallel,
    thread_local_middleware,
)
from next.conf.merge import FROZEN_DEFAULTS
from next.conf.shapes import Misfit, Section, Shape, flag, misfits, text


def defer_misfits(scope: Mapping[str, object]) -> list[Misfit]:
    return misfits(scope, SHAPE, FROZEN_DEFAULTS["DEFER"])


def _leaves(section: Section, path: tuple[str, ...] = ()):
    for key, shape in section.keys.items():
        if isinstance(shape, Shape):
            yield (*path, key)
        else:
            yield from _leaves(shape, (*path, key))


class TestShape:
    def test_the_shape_names_every_default_key(self) -> None:
        def keys(mapping: dict, path: tuple[str, ...] = ()):
            for key, value in mapping.items():
                if isinstance(value, dict):
                    yield from keys(value, (*path, key))
                else:
                    yield (*path, key)

        assert sorted(_leaves(SHAPE)) == sorted(keys(DEFAULTS["DEFER"]))

    def test_the_defaults_fit_their_shape(self) -> None:
        assert defer_misfits(DEFAULTS["DEFER"]) == []

    @pytest.mark.parametrize("path", sorted(_leaves(SHAPE)))
    def test_every_leaf_refuses_a_list(self, path: tuple[str, ...]) -> None:
        scope: dict = {}
        target = scope
        for key in path[:-1]:
            target = target.setdefault(key, {})
        target[path[-1]] = [object()]
        [misfit] = defer_misfits(scope)
        assert misfit.path == path
        assert misfit.found == "a list"


class TestMisfits:
    @pytest.mark.parametrize(
        ("scope", "path", "found"),
        [
            ({"DEADLINE": 0}, ("DEADLINE",), "0"),
            ({"DEADLINE": "-1s"}, ("DEADLINE",), "'-1s'"),
            ({"MAX_STREAMS": 0}, ("MAX_STREAMS",), "0"),
            ({"MAX_STREAMS": "many"}, ("MAX_STREAMS",), "'many'"),
            ({"DEADLINE_TO_DB": True}, ("DEADLINE_TO_DB",), "True"),
            ({"DEADLINE_TO_DB": 0}, ("DEADLINE_TO_DB",), "0"),
            ({"NOSCRIPT": 1}, ("NOSCRIPT",), "1"),
            ({"CONNECTION_STATE": ["a", ""]}, ("CONNECTION_STATE",), "a list"),
            ({"EXECUTOR": []}, ("EXECUTOR",), "a list"),
            (
                {"EXECUTOR": {"OPTIONS": {"MAX_WORKERS": 0}}},
                ("EXECUTOR", "OPTIONS", "MAX_WORKERS"),
                "0",
            ),
            (
                {"ADMISSION": "next.defer.QueueDelay"},
                ("ADMISSION",),
                "'next.defer.QueueDelay'",
            ),
            ({"EXECUTOR": {"SQLITE": "always"}}, ("EXECUTOR", "SQLITE"), "'always'"),
            (
                {"DELIVERY": {"OPTIONS": {"BOTS": "(unclosed"}}},
                ("DELIVERY", "OPTIONS", "BOTS"),
                "'(unclosed'",
            ),
            (
                {"DELIVERY": {"OPTIONS": {"SYNC_WORKERS": "thread"}}},
                ("DELIVERY", "OPTIONS", "SYNC_WORKERS"),
                "'thread'",
            ),
            (
                {"SERVER": {"OPTIONS": {"INCREMENTAL": "no"}}},
                ("SERVER", "OPTIONS", "INCREMENTAL"),
                "'no'",
            ),
            (
                {"SERVER": {"OPTIONS": {"CONCURRENT": 1}}},
                ("SERVER", "OPTIONS", "CONCURRENT"),
                "1",
            ),
            (
                {"SERVER": {"OPTIONS": {"BUFFERED_MARKS": ["a.b"]}}},
                ("SERVER", "OPTIONS", "BUFFERED_MARKS"),
                "a list",
            ),
            (
                {"SERVER": {"OPTIONS": {"BUFFERED_MARKS": {"HTTP": {}}}}},
                ("SERVER", "OPTIONS", "BUFFERED_MARKS"),
                "a dict",
            ),
            (
                {"SERVER": {"OPTIONS": {"BUFFERED_MARKS": {"WSGI": []}}}},
                ("SERVER", "OPTIONS", "BUFFERED_MARKS"),
                "a dict",
            ),
            (
                {"SERVER": {"OPTIONS": {"BUFFERED_MARKS": {"WSGI": {"ALL": []}}}}},
                ("SERVER", "OPTIONS", "BUFFERED_MARKS"),
                "a dict",
            ),
            (
                {"SERVER": {"OPTIONS": {"BUFFERED_MARKS": {"ASGI": {"KEYS": [""]}}}}},
                ("SERVER", "OPTIONS", "BUFFERED_MARKS"),
                "a dict",
            ),
            (
                {"TOKENS": {"OPTIONS": {"CARRIER": "next.defer.BodyCarrier"}}},
                ("TOKENS", "OPTIONS", "CARRIER"),
                "'next.defer.BodyCarrier'",
            ),
            ({"TOKENS": {"BACKEND": ""}}, ("TOKENS", "BACKEND"), "''"),
            (
                {"TOKENS": {"OPTIONS": {"MAX_AGE": -1}}},
                ("TOKENS", "OPTIONS", "MAX_AGE"),
                "-1",
            ),
            ({"DEADLINE": "9" * 400 + "s"}, ("DEADLINE",), "'" + "9" * 400 + "s'"),
            ({"DEADLINE": 86_401}, ("DEADLINE",), "86401"),
        ],
    )
    def test_a_value_outside_its_shape_is_a_misfit(self, scope, path, found) -> None:
        [misfit] = defer_misfits(scope)
        assert (misfit.path, misfit.found) == (path, found)
        assert misfit.expected

    @pytest.mark.parametrize(
        "scope",
        [
            {"DEADLINE": "500ms", "WRITE_GRACE": 0, "MAX_STREAMS": 8},
            {"DEADLINE_TO_DB": False, "NOSCRIPT": False},
            {"DELIVERY": {"OPTIONS": {"BOTS": r"bot|crawl", "SYNC_WORKERS": "stream"}}},
            {"SERVER": {"OPTIONS": {"INCREMENTAL": False, "CONCURRENT": None}}},
            {
                "SERVER": {
                    "OPTIONS": {
                        "BUFFERED_MARKS": {
                            "WSGI": {"KEYS": ["a.b"], "PREFIXES": ("c.",)},
                            "ASGI": {},
                        }
                    }
                }
            },
            {"SERVER": {"BACKEND": "myapp.Probe", "OPTIONS": {"ANY": 1}}},
            {"TOKENS": {"OPTIONS": {"CARRIER": "query", "MAX_AGE": 86_400 * 2}}},
            {"TOKENS": {"OPTIONS": {"CARRIER": "body", "MAX_AGE": 1}}},
            {"EXECUTOR": {"PER_REQUEST": 1, "SQLITE": "parallel"}},
            {"EXECUTOR": {"BACKEND": "myapp.Executor", "OPTIONS": {"ANY": 1}}},
            {"ADMISSION": {"BACKEND": "myapp.Admission", "OPTIONS": {"ANY": 1}}},
            {"THREAD_STATE": ("a.b",)},
        ],
    )
    def test_a_usable_value_is_no_misfit(self, scope) -> None:
        assert defer_misfits(scope) == []

    def test_the_options_of_a_foreign_backend_pass_unread(self) -> None:
        scope = {"DELIVERY": {"BACKEND": "myapp.Policy", "OPTIONS": {"ANY": object()}}}
        assert defer_misfits(scope) == []

    def test_the_named_shipped_backend_keeps_its_options_read(self) -> None:
        scope = {
            "DELIVERY": {
                "BACKEND": "next.defer.DefaultDeliveryPolicy",
                "OPTIONS": {"ANY": 1},
            }
        }
        [misfit] = defer_misfits(scope)
        assert misfit == Misfit(
            ("DELIVERY", "OPTIONS"),
            unknown=("ANY",),
            allowed=("BOTS", "STREAM", "SYNC_WORKERS"),
        )


class TestReads:
    def test_a_missing_scope_reads_the_defaults(self) -> None:
        assert defer_count("MAX_BOUNDARIES") == 64

    def test_a_read_follows_a_settings_reload(self) -> None:
        assert defer_count("MAX_DEPTH") == 4
        with override_settings(NEXT_FRAMEWORK={"DEFER": {"MAX_DEPTH": 2}}):
            assert defer_count("MAX_DEPTH") == 2
            assert defer_count("MAX_DEPTH") == 2
        assert defer_count("MAX_DEPTH") == 4

    def test_a_misfit_raises_on_the_first_read(self) -> None:
        expected = (
            "NEXT_FRAMEWORK['DEFER']['MAX_DEPTH'] must be an integer from 1, "
            "got 'deep'."
        )
        with override_settings(NEXT_FRAMEWORK={"DEFER": {"MAX_DEPTH": "deep"}}):
            for _ in range(2):
                with pytest.raises(ImproperlyConfigured) as raised:
                    defer_count("MAX_DEPTH")
                assert str(raised.value) == expected

    def test_a_typed_read_answers_the_value_its_predicate_passes(self) -> None:
        assert defer_read(text, "ADMISSION", "BACKEND") == "next.defer.QueueDelay"

    def test_a_reader_that_disagrees_with_the_shape_is_refused(self) -> None:
        with pytest.raises(TypeError, match=r"DEFER\['ADMISSION'\]\['BACKEND'\] holds"):
            defer_read(flag, "ADMISSION", "BACKEND")

    def test_a_nested_key_keeps_its_siblings_defaults(self) -> None:
        scope = {"DELIVERY": {"OPTIONS": {"STREAM": False}}}
        with override_settings(NEXT_FRAMEWORK={"DEFER": scope}):
            assert defer_option("DELIVERY", "OPTIONS", "STREAM") is False
            assert defer_option("DELIVERY", "OPTIONS", "SYNC_WORKERS") == "inline"
            assert (
                defer_option("DELIVERY", "BACKEND")
                == "next.defer.DefaultDeliveryPolicy"
            )

    @pytest.mark.parametrize(
        ("scope", "path", "value"),
        [
            (
                {"EXECUTOR": {"OPTIONS": {"MAX_QUEUE": 3}}},
                ("EXECUTOR", "OPTIONS", "MAX_QUEUE"),
                3,
            ),
            (
                {"EXECUTOR": {"PER_REQUEST": 2}},
                ("EXECUTOR", "OPTIONS", "MAX_WORKERS"),
                DEFAULTS["DEFER"]["EXECUTOR"]["OPTIONS"]["MAX_WORKERS"],
            ),
            (
                {"TOKENS": {"OPTIONS": {"MAX_AGE": 60}}},
                ("TOKENS", "OPTIONS", "MAX_AGE"),
                60,
            ),
        ],
    )
    def test_a_nested_read_defaults_per_key(
        self, scope: dict, path: tuple[str, ...], value
    ) -> None:
        with override_settings(NEXT_FRAMEWORK={"DEFER": scope}):
            assert defer_option(*path) == value

    @pytest.mark.parametrize(
        ("scope", "where"),
        [
            (
                {"EXECUTOR": {"OPTIONS": {"MAX_QUEUE": 0}}},
                "['EXECUTOR']['OPTIONS']['MAX_QUEUE']",
            ),
            ({"EXECUTOR": []}, "['EXECUTOR']"),
            ({"TOKENS": {"OPTIONS": {"MAX_AGE": 0}}}, "['OPTIONS']['MAX_AGE']"),
            ({"DEADLINE": "9" * 400 + "s"}, "['DEADLINE']"),
        ],
    )
    def test_a_nested_misfit_raises_naming_its_key(
        self, scope: dict, where: str
    ) -> None:
        with (
            override_settings(NEXT_FRAMEWORK={"DEFER": scope}),
            pytest.raises(ImproperlyConfigured, match=re.escape(where)),
        ):
            defer_option("EXECUTOR")

    def test_a_foreign_backend_reads_its_own_options(self) -> None:
        scope = {"DELIVERY": {"BACKEND": "myapp.Policy", "OPTIONS": {"ANY": {"a": 1}}}}
        with override_settings(NEXT_FRAMEWORK={"DEFER": scope}):
            assert defer_option("DELIVERY", "OPTIONS", "ANY") == {"a": 1}
            assert defer_option("DELIVERY", "OPTIONS", "MISSING") is None
            assert defer_option("DELIVERY", "OPTIONS", "ANY", "a") == 1

    def test_a_foreign_backend_option_the_framework_names_keeps_its_shape(self) -> None:
        scope = {"DELIVERY": {"BACKEND": "myapp.Policy", "OPTIONS": {"STREAM": 1}}}
        [misfit] = defer_misfits(scope)
        assert misfit.path == ("DELIVERY", "OPTIONS", "STREAM")

    def test_the_shipped_executor_keeps_its_options_read(self) -> None:
        scope = {"EXECUTOR": {"OPTIONS": {"LANES": 2}}}
        [misfit] = defer_misfits(scope)
        assert misfit == Misfit(
            ("EXECUTOR", "OPTIONS"),
            unknown=("LANES",),
            allowed=("CONN_IDLE", "MAX_QUEUE", "MAX_WORKERS", "SHUTDOWN_TIMEOUT"),
        )

    def test_a_section_reads_merged_over_its_defaults(self) -> None:
        with override_settings(
            NEXT_FRAMEWORK={"DEFER": {"EXECUTOR": {"OPTIONS": {"MAX_QUEUE": 2}}}}
        ):
            options = defer_option("EXECUTOR")["OPTIONS"]
            assert options["MAX_QUEUE"] == 2
            assert options["MAX_WORKERS"] == defer_count(
                "EXECUTOR", "OPTIONS", "MAX_WORKERS"
            )
            policy = defer_option("DELIVERY", "OPTIONS")
        assert isinstance(policy, Mapping)
        assert policy["SYNC_WORKERS"] == "inline"

    def test_a_read_is_held_for_the_same_mapping(self) -> None:
        with override_settings(
            NEXT_FRAMEWORK={"DEFER": {"EXECUTOR": {"PER_REQUEST": 2}}}
        ):
            first = defer_option("EXECUTOR")
            assert defer_option("EXECUTOR") is first

    def test_the_typed_reads_take_a_path(self) -> None:
        with override_settings(
            NEXT_FRAMEWORK={
                "DEFER": {"EXECUTOR": {"PER_REQUEST": 3, "QUEUE_TIMEOUT": "2ms"}}
            }
        ):
            assert defer_count("EXECUTOR", "PER_REQUEST") == 3
            assert defer_seconds("EXECUTOR", "QUEUE_TIMEOUT") == 0.002


class TestBackendOptions:
    @pytest.mark.parametrize(
        ("config", "options"),
        [
            (None, {}),
            ({}, {}),
            ({"OPTIONS": []}, {}),
            ({"OPTIONS": {"BOTS": "x"}}, {"BOTS": "x"}),
        ],
    )
    def test_the_options_of_an_entry(self, config, options) -> None:
        assert dict(entry_options(config)) == options

    def test_an_entry_option_wins_over_the_setting(self) -> None:
        assert backend_option({"STREAM": False}, "DELIVERY", "STREAM") is False

    def test_a_typed_option_reads_the_entry_then_the_setting(self) -> None:
        scope = {"EXECUTOR": {"OPTIONS": {"MAX_QUEUE": 3, "CONN_IDLE": "2s"}}}
        with override_settings(NEXT_FRAMEWORK={"DEFER": scope}):
            assert backend_count({}, "EXECUTOR", "MAX_QUEUE") == 3
            assert backend_count({"MAX_QUEUE": 5}, "EXECUTOR", "MAX_QUEUE") == 5
            assert backend_seconds({}, "EXECUTOR", "CONN_IDLE") == 2.0
            assert backend_seconds({"CONN_IDLE": "1s"}, "EXECUTOR", "CONN_IDLE") == 1.0

    def test_a_typed_option_its_reader_refuses_names_its_key(self) -> None:
        match = r"DEFER\['EXECUTOR'\]\['OPTIONS'\]\['MAX_QUEUE'\] holds"
        with pytest.raises(TypeError, match=match):
            backend_count({"MAX_QUEUE": "many"}, "EXECUTOR", "MAX_QUEUE")

    def test_an_omitted_option_reads_the_setting(self) -> None:
        scope = {"DELIVERY": {"OPTIONS": {"SYNC_WORKERS": "stream"}}}
        with override_settings(NEXT_FRAMEWORK={"DEFER": scope}):
            assert backend_option({}, "DELIVERY", "SYNC_WORKERS") == "stream"


class TestConnectionReads:
    def test_the_defaults(self) -> None:
        assert backend_paths("THREAD_STATE") == ()
        assert deadline_mode() == "always"
        assert parallel() is True
        assert sqlite_parallel() is False
        assert thread_local_middleware() == ()

    def test_written_values(self) -> None:
        scope = {
            "CONNECTION_STATE": ["myapp.State"],
            "DEADLINE_TO_DB": "state",
            "EXECUTOR": {
                "PARALLEL": False,
                "SQLITE": "parallel",
                "THREAD_LOCAL_MIDDLEWARE": ["myapp.Middleware"],
            },
        }
        with override_settings(NEXT_FRAMEWORK={"DEFER": scope}):
            assert backend_paths("CONNECTION_STATE") == ("myapp.State",)
            assert deadline_mode() == "state"
            assert parallel() is False
            assert sqlite_parallel() is True
            assert thread_local_middleware() == ("myapp.Middleware",)

    def test_misfits_raise_together(self) -> None:
        scope = {
            "DEADLINE_TO_DB": True,
            "EXECUTOR": {"PARALLEL": "no", "SQLITE": "always"},
        }
        with override_settings(NEXT_FRAMEWORK={"DEFER": scope}):
            for read in (deadline_mode, parallel, sqlite_parallel):
                with pytest.raises(ImproperlyConfigured) as raised:
                    read()
                assert str(raised.value).count(" must be ") == 3


class TestLongTokenAge:
    @pytest.mark.parametrize(
        ("scope", "age"),
        [
            ({}, None),
            ({"TOKENS": []}, None),
            ({"TOKENS": {"OPTIONS": {"MAX_AGE": 86_400}}}, None),
            ({"TOKENS": {"OPTIONS": {"MAX_AGE": 86_401}}}, 86_401),
            ({"TOKENS": {"OPTIONS": {"MAX_AGE": True}}}, None),
            ({"TOKENS": {"OPTIONS": {"MAX_AGE": "long"}}}, None),
            (
                {"TOKENS": {"BACKEND": "myapp.Tokens", "OPTIONS": {"MAX_AGE": 10**6}}},
                None,
            ),
        ],
    )
    def test_only_a_shipped_age_above_a_day_is_named(self, scope, age) -> None:
        assert long_token_age(scope) == age
