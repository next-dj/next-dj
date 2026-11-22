from collections.abc import Mapping

import pytest
from django.test import override_settings

from next.conf.defaults import DEFAULTS
from next.conf.defer import (
    SHAPE,
    backend_count,
    backend_option,
    backend_paths,
    deadline_mode,
    defer_count,
    defer_option,
    defer_read,
    defer_seconds,
    defer_template,
    entry_options,
    long_token_age,
    parallel,
    sqlite_parallel,
    thread_local_middleware,
    timeout_floor,
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
            ({"BATCH_WINDOW": -1}, ("BATCH_WINDOW",), "-1"),
            ({"LOADER_MAX_BATCH": 0}, ("LOADER_MAX_BATCH",), "0"),
            ({"LOADER_MAX_BATCH": True}, ("LOADER_MAX_BATCH",), "True"),
            ({"MAX_STREAMS": 0}, ("MAX_STREAMS",), "0"),
            ({"MAX_STREAMS": "many"}, ("MAX_STREAMS",), "'many'"),
            ({"DEADLINE_TO_DB": True}, ("DEADLINE_TO_DB",), "True"),
            ({"DEADLINE_TO_DB": 0}, ("DEADLINE_TO_DB",), "0"),
            ({"ERRORS": {"ATOMIC": 1}}, ("ERRORS", "ATOMIC"), "1"),
            ({"ERRORS": {"ATOMIC": "reads"}}, ("ERRORS", "ATOMIC"), "'reads'"),
            ({"ERRORS": {"KINDS": "brief"}}, ("ERRORS", "KINDS"), "'brief'"),
            ({"ERRORS": {"REPORTER": ""}}, ("ERRORS", "REPORTER"), "''"),
            ({"LOADING": 3}, ("LOADING",), "3"),
            ({"CONNECTION_STATE": ["a", ""]}, ("CONNECTION_STATE",), "a list"),
            ({"MORE": []}, ("MORE",), "a list"),
            ({"MORE": {"MAX_RESTORE": 0}}, ("MORE", "MAX_RESTORE"), "0"),
            (
                {"EXECUTOR": {"OPTIONS": {"PLACEMENT": {"MODE": "fixed"}}}},
                ("EXECUTOR", "OPTIONS", "PLACEMENT", "MODE"),
                "'fixed'",
            ),
            (
                {"EXECUTOR": {"OPTIONS": {"PLACEMENT": {"CPU_RATIO": 0}}}},
                ("EXECUTOR", "OPTIONS", "PLACEMENT", "CPU_RATIO"),
                "0",
            ),
            (
                {"EXECUTOR": {"OPTIONS": {"PLACEMENT": {"CPU_RATIO": 1.5}}}},
                ("EXECUTOR", "OPTIONS", "PLACEMENT", "CPU_RATIO"),
                "1.5",
            ),
            (
                {"EXECUTOR": {"OPTIONS": {"PLACEMENT": {"CPU_RATIO": False}}}},
                ("EXECUTOR", "OPTIONS", "PLACEMENT", "CPU_RATIO"),
                "False",
            ),
            (
                {"EXECUTOR": {"OPTIONS": {"PLACEMENT": {"EXPLORE": -1}}}},
                ("EXECUTOR", "OPTIONS", "PLACEMENT", "EXPLORE"),
                "-1",
            ),
            (
                {"EXECUTOR": {"OPTIONS": {"SQLITE": "always"}}},
                ("EXECUTOR", "OPTIONS", "SQLITE"),
                "'always'",
            ),
            (
                {"POLICY": {"OPTIONS": {"BOTS": "(unclosed"}}},
                ("POLICY", "OPTIONS", "BOTS"),
                "'(unclosed'",
            ),
            (
                {"POLICY": {"OPTIONS": {"SYNC_WORKERS": "thread"}}},
                ("POLICY", "OPTIONS", "SYNC_WORKERS"),
                "'thread'",
            ),
            (
                {"TRANSPORT": {"OPTIONS": {"INCREMENTAL": "no"}}},
                ("TRANSPORT", "OPTIONS", "INCREMENTAL"),
                "'no'",
            ),
            (
                {"TRANSPORT": {"OPTIONS": {"CONCURRENT": 1}}},
                ("TRANSPORT", "OPTIONS", "CONCURRENT"),
                "1",
            ),
            (
                {"TRANSPORT": {"OPTIONS": {"BUFFERED_MARKS": ["a.b"]}}},
                ("TRANSPORT", "OPTIONS", "BUFFERED_MARKS"),
                "a list",
            ),
            (
                {"TRANSPORT": {"OPTIONS": {"BUFFERED_MARKS": {"HTTP": {}}}}},
                ("TRANSPORT", "OPTIONS", "BUFFERED_MARKS"),
                "a dict",
            ),
            (
                {"TRANSPORT": {"OPTIONS": {"BUFFERED_MARKS": {"WSGI": []}}}},
                ("TRANSPORT", "OPTIONS", "BUFFERED_MARKS"),
                "a dict",
            ),
            (
                {"TRANSPORT": {"OPTIONS": {"BUFFERED_MARKS": {"WSGI": {"ALL": []}}}}},
                ("TRANSPORT", "OPTIONS", "BUFFERED_MARKS"),
                "a dict",
            ),
            (
                {
                    "TRANSPORT": {
                        "OPTIONS": {"BUFFERED_MARKS": {"ASGI": {"KEYS": [""]}}}
                    }
                },
                ("TRANSPORT", "OPTIONS", "BUFFERED_MARKS"),
                "a dict",
            ),
            (
                {"TOKENS": {"OPTIONS": {"BUCKET": -5}}},
                ("TOKENS", "OPTIONS", "BUCKET"),
                "-5",
            ),
            (
                {"TOKENS": {"OPTIONS": {"KEYS": "secret"}}},
                ("TOKENS", "OPTIONS", "KEYS"),
                "'secret'",
            ),
            ({"TOKENS": {"BACKEND": ""}}, ("TOKENS", "BACKEND"), "''"),
            (
                {"TOKENS": {"OPTIONS": {"KEYS": ["k" * 31]}}},
                ("TOKENS", "OPTIONS", "KEYS"),
                "a list",
            ),
            (
                {"TOKENS": {"OPTIONS": {"MAX_AGE": -1}}},
                ("TOKENS", "OPTIONS", "MAX_AGE"),
                "-1",
            ),
            (
                {"TOKENS": {"OPTIONS": {"MAX_AGE": 60, "BUCKET": 600}}},
                ("TOKENS", "OPTIONS", "BUCKET"),
                "600",
            ),
            (
                {"TOKENS": {"OPTIONS": {"MAX_AGE": 60, "BUCKET": 60}}},
                ("TOKENS", "OPTIONS", "BUCKET"),
                "60",
            ),
            (
                {"TOKENS": {"OPTIONS": {"BUCKET": 600}}},
                ("TOKENS", "OPTIONS", "BUCKET"),
                "600",
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
            {"DEADLINE": "500ms", "BATCH_WINDOW": 0, "MAX_STREAMS": 8},
            {"DEADLINE_TO_DB": False, "ERRORS": {"ATOMIC": True}},
            {"ERRORS": {"ATOMIC": False, "KINDS": "opaque", "REPORTER": "a.b"}},
            {"POLICY": {"OPTIONS": {"BOTS": r"bot|crawl", "SYNC_WORKERS": "stream"}}},
            {"TRANSPORT": {"OPTIONS": {"INCREMENTAL": False, "CONCURRENT": None}}},
            {
                "TRANSPORT": {
                    "OPTIONS": {
                        "BUFFERED_MARKS": {
                            "WSGI": {"KEYS": ["a.b"], "PREFIXES": ("c.",)},
                            "ASGI": {},
                        }
                    }
                }
            },
            {"TRANSPORT": {"BACKEND": "myapp.Transport", "OPTIONS": {"ANY": 1}}},
            {"TOKENS": {"OPTIONS": {"BUCKET": 30, "KEYS": ["k" * 32]}}},
            {"TOKENS": {"OPTIONS": {"BUCKET": 599, "MAX_AGE": 86_400 * 2}}},
            {"TOKENS": {"OPTIONS": {"BUCKET": None, "MAX_AGE": 1}}},
            {"EXECUTOR": {"OPTIONS": {"PLACEMENT": {"CPU_RATIO": 1, "MODE": "pool"}}}},
            {"THREAD_STATE": ("a.b",), "LOADING": "skeleton.djx"},
        ],
    )
    def test_a_usable_value_is_no_misfit(self, scope) -> None:
        assert defer_misfits(scope) == []

    def test_a_misfit_ceiling_leaves_the_bucket_unread(self) -> None:
        found = defer_misfits({"TOKENS": {"OPTIONS": {"BUCKET": 900, "MAX_AGE": -1}}})
        assert [misfit.path for misfit in found] == [("TOKENS", "OPTIONS", "MAX_AGE")]

    def test_a_bucket_misfit_names_its_ceiling(self) -> None:
        [misfit] = defer_misfits({"TOKENS": {"OPTIONS": {"BUCKET": 600}}})
        assert misfit.expected == "an integer below MAX_AGE, which is 600, or None"

    def test_the_options_of_a_foreign_backend_pass_unread(self) -> None:
        scope = {"POLICY": {"BACKEND": "myapp.Policy", "OPTIONS": {"ANY": object()}}}
        assert defer_misfits(scope) == []

    def test_the_named_shipped_backend_keeps_its_options_read(self) -> None:
        scope = {
            "POLICY": {
                "BACKEND": "next.suspense.DefaultStreamPolicy",
                "OPTIONS": {"ANY": 1},
            }
        }
        [misfit] = defer_misfits(scope)
        assert misfit == Misfit(
            ("POLICY", "OPTIONS"),
            unknown=("ANY",),
            allowed=("BOTS", "STREAM", "SYNC_WORKERS"),
        )


class TestReads:
    def test_a_missing_scope_reads_the_defaults(self) -> None:
        assert defer_count("LOADER_MAX_BATCH") == 500

    def test_a_read_follows_a_settings_reload(self) -> None:
        assert defer_count("MAX_DEPTH") == 4
        with override_settings(NEXT_FRAMEWORK={"DEFER": {"MAX_DEPTH": 2}}):
            assert defer_count("MAX_DEPTH") == 2
            assert defer_count("MAX_DEPTH") == 2
        assert defer_count("MAX_DEPTH") == 4

    def test_a_misfit_reads_as_its_default(self) -> None:
        with override_settings(NEXT_FRAMEWORK={"DEFER": {"MAX_DEPTH": "deep"}}):
            assert defer_count("MAX_DEPTH") == 4

    def test_a_typed_read_answers_the_value_its_predicate_passes(self) -> None:
        assert defer_read(text, "ADMISSION") == "next.suspense.QueueDelay"

    def test_a_reader_that_disagrees_with_the_shape_is_refused(self) -> None:
        with pytest.raises(TypeError, match=r"DEFER\['ADMISSION'\] holds"):
            defer_read(flag, "ADMISSION")

    def test_a_backend_count_reads_the_options_then_the_scope(self) -> None:
        assert backend_count({"MAX_WORKERS": 3}, "EXECUTOR", "MAX_WORKERS") == 3
        assert backend_count({}, "EXECUTOR", "MAX_QUEUE") == defer_count(
            "EXECUTOR", "OPTIONS", "MAX_QUEUE"
        )

    def test_a_backend_count_refuses_what_is_no_integer(self) -> None:
        match = r"DEFER\['EXECUTOR'\]\['OPTIONS'\]\['MAX_WORKERS'\] holds 'x'"
        with pytest.raises(TypeError, match=match):
            backend_count({"MAX_WORKERS": "x"}, "EXECUTOR", "MAX_WORKERS")

    def test_a_nested_key_keeps_its_siblings_defaults(self) -> None:
        with override_settings(NEXT_FRAMEWORK={"DEFER": {"ERRORS": {"RAISE": True}}}):
            assert defer_option("ERRORS", "RAISE") is True
            assert defer_option("ERRORS", "KINDS") == "detailed"
            assert defer_option("ERRORS", "ATOMIC") == "writes"

    @pytest.mark.parametrize(
        ("scope", "path", "value"),
        [
            ({"MORE": {"MAX_RESTORE": 3}}, ("MORE", "MAX_RESTORE"), 3),
            ({"MORE": {"MAX_RESTORE": 0}}, ("MORE", "MAX_RESTORE"), 10),
            ({"MORE": []}, ("MORE", "MAX_RESTORE"), 10),
            ({"MORE": {}}, ("MORE", "MAX_RESTORE"), 10),
            (
                {"EXECUTOR": {"OPTIONS": {"PLACEMENT": {"MODE": "pool"}}}},
                ("EXECUTOR", "OPTIONS", "PLACEMENT", "EXPLORE"),
                3,
            ),
            (
                {"EXECUTOR": {"OPTIONS": {"PLACEMENT": {"MODE": "pool"}}}},
                ("EXECUTOR", "OPTIONS", "MAX_WORKERS"),
                16,
            ),
            (
                {"TOKENS": {"OPTIONS": {"MAX_AGE": 60}}},
                ("TOKENS", "OPTIONS", "MAX_AGE"),
                60,
            ),
            (
                {"TOKENS": {"OPTIONS": {"MAX_AGE": 60, "BUCKET": 600}}},
                ("TOKENS", "OPTIONS", "BUCKET"),
                None,
            ),
            (
                {"TOKENS": {"OPTIONS": {"KEYS": ["short"]}}},
                ("TOKENS", "OPTIONS", "KEYS"),
                None,
            ),
            ({"DEADLINE": "9" * 400 + "s"}, ("DEADLINE",), 10.0),
        ],
    )
    def test_a_nested_read_defaults_per_key(
        self, scope: dict, path: tuple[str, ...], value
    ) -> None:
        with override_settings(NEXT_FRAMEWORK={"DEFER": scope}):
            assert defer_option(*path) == value

    def test_a_foreign_backend_reads_its_own_options(self) -> None:
        scope = {"POLICY": {"BACKEND": "myapp.Policy", "OPTIONS": {"ANY": {"a": 1}}}}
        with override_settings(NEXT_FRAMEWORK={"DEFER": scope}):
            assert defer_option("POLICY", "OPTIONS", "ANY") == {"a": 1}
            assert defer_option("POLICY", "OPTIONS", "MISSING") is None
            assert defer_option("POLICY", "OPTIONS", "ANY", "a") == 1

    def test_a_foreign_backend_option_the_framework_names_keeps_its_shape(self) -> None:
        scope = {"EXECUTOR": {"BACKEND": "myapp.Executor", "OPTIONS": {"PARALLEL": 1}}}
        with override_settings(NEXT_FRAMEWORK={"DEFER": scope}):
            [misfit] = defer_misfits(scope)
            assert misfit.path == ("EXECUTOR", "OPTIONS", "PARALLEL")
            assert defer_option("EXECUTOR", "OPTIONS", "PARALLEL") is True
            assert parallel() is True

    def test_a_section_reads_merged_over_its_defaults(self) -> None:
        with override_settings(NEXT_FRAMEWORK={"DEFER": {"MORE": {"MAX_RESTORE": 2}}}):
            assert dict(defer_option("MORE")) == {"MAX_RESTORE": 2}
            errors = defer_option("ERRORS")
        assert isinstance(errors, Mapping)
        assert errors["KINDS"] == "detailed"

    def test_a_read_is_held_for_the_same_mapping(self) -> None:
        with override_settings(NEXT_FRAMEWORK={"DEFER": {"ERRORS": {"RAISE": True}}}):
            first = defer_option("ERRORS")
            assert defer_option("ERRORS") is first

    def test_the_typed_reads_take_a_path(self) -> None:
        with override_settings(
            NEXT_FRAMEWORK={
                "DEFER": {
                    "MORE": {"MAX_RESTORE": 4},
                    "EXECUTOR": {"OPTIONS": {"PLACEMENT": {"HANDOFF": "2ms"}}},
                }
            }
        ):
            assert defer_count("MORE", "MAX_RESTORE") == 4
            assert defer_seconds("EXECUTOR", "OPTIONS", "PLACEMENT", "HANDOFF") == 0.002
            assert defer_template("LOADING") is None


class TestBackendOptions:
    @pytest.mark.parametrize(
        ("config", "options"),
        [
            (None, {}),
            ({}, {}),
            ({"OPTIONS": []}, {}),
            ({"OPTIONS": {"MAX_QUEUE": 2}}, {"MAX_QUEUE": 2}),
        ],
    )
    def test_the_options_of_an_entry(self, config, options) -> None:
        assert dict(entry_options(config)) == options

    def test_an_entry_option_wins_over_the_setting(self) -> None:
        assert backend_option({"MAX_QUEUE": 2}, "EXECUTOR", "MAX_QUEUE") == 2

    def test_an_omitted_option_reads_the_setting(self) -> None:
        with override_settings(
            NEXT_FRAMEWORK={"DEFER": {"EXECUTOR": {"OPTIONS": {"MAX_QUEUE": 7}}}}
        ):
            assert backend_option({}, "EXECUTOR", "MAX_QUEUE") == 7


class TestConnectionReads:
    def test_the_defaults(self) -> None:
        assert backend_paths("THREAD_STATE") == ()
        assert deadline_mode() == "state"
        assert timeout_floor() == defer_seconds("DB_TIMEOUT_FLOOR")
        assert parallel() is True
        assert sqlite_parallel() is False
        assert thread_local_middleware() == ()

    def test_written_values(self) -> None:
        scope = {
            "CONNECTION_STATE": ["myapp.State"],
            "DEADLINE_TO_DB": "always",
            "DB_TIMEOUT_FLOOR": "250ms",
            "EXECUTOR": {
                "OPTIONS": {
                    "PARALLEL": False,
                    "SQLITE": "parallel",
                    "THREAD_LOCAL_MIDDLEWARE": ["myapp.Middleware"],
                }
            },
        }
        with override_settings(NEXT_FRAMEWORK={"DEFER": scope}):
            assert backend_paths("CONNECTION_STATE") == ("myapp.State",)
            assert deadline_mode() == "always"
            assert timeout_floor() == 0.25
            assert parallel() is False
            assert sqlite_parallel() is True
            assert thread_local_middleware() == ("myapp.Middleware",)

    def test_misfits_read_as_their_defaults(self) -> None:
        scope = {
            "DEADLINE_TO_DB": True,
            "EXECUTOR": {"OPTIONS": {"PARALLEL": "no", "SQLITE": "always"}},
        }
        with override_settings(NEXT_FRAMEWORK={"DEFER": scope}):
            assert deadline_mode() == "state"
            assert parallel() is True
            assert sqlite_parallel() is False


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
