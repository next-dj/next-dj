from collections.abc import Mapping

import pytest
from django.core.checks import run_checks
from django.test import override_settings

from next.checks import NEXT
from next.conf.checks import check_defer_settings
from next.conf.defaults import DEFAULTS
from next.conf.defer import (
    SHAPE,
    Misfit,
    Section,
    Shape,
    defer_count,
    defer_option,
    defer_seconds,
    defer_template,
    long_token_age,
    misfits,
)


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
        assert misfits(DEFAULTS["DEFER"]) == []

    @pytest.mark.parametrize("path", sorted(_leaves(SHAPE)))
    def test_every_leaf_refuses_a_list(self, path: tuple[str, ...]) -> None:
        scope: dict = {}
        target = scope
        for key in path[:-1]:
            target = target.setdefault(key, {})
        target[path[-1]] = [object()]
        [misfit] = misfits(scope)
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
        [misfit] = misfits(scope)
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
        assert misfits(scope) == []

    def test_unknown_keys_are_reported_at_every_level(self) -> None:
        found = misfits(
            {
                "SPEED": 1,
                "ERRORS": {"LOUD": True},
                "EXECUTOR": {"OPTIONS": {"PLACEMENT": {"GUESS": 1}}},
            }
        )
        assert [(m.path, m.unknown) for m in found] == [
            ((), ("SPEED",)),
            (("EXECUTOR", "OPTIONS", "PLACEMENT"), ("GUESS",)),
            (("ERRORS",), ("LOUD",)),
        ]
        assert "DEADLINE" in found[0].allowed

    def test_a_misfit_ceiling_leaves_the_bucket_unread(self) -> None:
        found = misfits({"TOKENS": {"OPTIONS": {"BUCKET": 900, "MAX_AGE": -1}}})
        assert [misfit.path for misfit in found] == [("TOKENS", "OPTIONS", "MAX_AGE")]

    def test_a_bucket_misfit_names_its_ceiling(self) -> None:
        [misfit] = misfits({"TOKENS": {"OPTIONS": {"BUCKET": 600}}})
        assert misfit.expected == "an integer below MAX_AGE, which is 600, or None"

    def test_the_options_of_a_foreign_backend_pass_unread(self) -> None:
        scope = {"POLICY": {"BACKEND": "myapp.Policy", "OPTIONS": {"ANY": object()}}}
        assert misfits(scope) == []

    def test_the_named_shipped_backend_keeps_its_options_read(self) -> None:
        scope = {
            "POLICY": {
                "BACKEND": "next.suspense.DefaultStreamPolicy",
                "OPTIONS": {"ANY": 1},
            }
        }
        [misfit] = misfits(scope)
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


class TestCheckDeferSettings:
    def test_silent_without_the_setting(self) -> None:
        with override_settings(NEXT_FRAMEWORK={}):
            assert check_defer_settings() == []

    def test_silent_on_a_non_dict_setting(self) -> None:
        with override_settings(NEXT_FRAMEWORK=42):
            assert check_defer_settings() == []

    def test_silent_on_a_non_dict_scope(self) -> None:
        with override_settings(NEXT_FRAMEWORK={"DEFER": []}):
            assert check_defer_settings() == []

    def test_a_misfit_value_yields_e076(self) -> None:
        with override_settings(
            NEXT_FRAMEWORK={"DEFER": {"ERRORS": {"KINDS": "brief"}}}
        ):
            [message] = check_defer_settings()
        assert message.id == "next.E076"
        assert message.msg == (
            "NEXT_FRAMEWORK['DEFER']['ERRORS']['KINDS'] must be 'detailed' or "
            "'opaque', got 'brief'. The framework ignores this value and keeps "
            "its default."
        )

    def test_an_unknown_key_yields_e035(self) -> None:
        with override_settings(NEXT_FRAMEWORK={"DEFER": {"MORE": {"PAGES": 3}}}):
            [message] = check_defer_settings()
        assert message.id == "next.E035"
        assert message.msg == (
            "NEXT_FRAMEWORK['DEFER']['MORE'] has unknown keys 'PAGES'. Allowed keys "
            "are MAX_RESTORE."
        )

    def test_a_short_token_key_yields_e233(self) -> None:
        with override_settings(
            NEXT_FRAMEWORK={"DEFER": {"TOKENS": {"OPTIONS": {"KEYS": ["short"]}}}}
        ):
            [message] = check_defer_settings()
        assert message.id == "next.E233"
        assert message.msg.startswith(
            "NEXT_FRAMEWORK['DEFER']['TOKENS']['OPTIONS']['KEYS'] must be None, a "
            "dotted path or a list of keys of 32 characters or more, got a list."
        )
        assert "forge" in message.msg

    @pytest.mark.parametrize("keys", [[], ""])
    def test_an_empty_key_ring_yields_e233(self, keys: object) -> None:
        with override_settings(
            NEXT_FRAMEWORK={"DEFER": {"TOKENS": {"OPTIONS": {"KEYS": keys}}}}
        ):
            [message] = check_defer_settings()
        assert message.id == "next.E233"

    @pytest.mark.parametrize("keys", [None, "myapp.secrets.ring", ["k" * 32, "f" * 40]])
    def test_a_usable_key_ring_passes(self, keys: object) -> None:
        with override_settings(
            NEXT_FRAMEWORK={"DEFER": {"TOKENS": {"OPTIONS": {"KEYS": keys}}}}
        ):
            assert check_defer_settings() == []

    def test_a_bucket_from_max_age_yields_e233(self) -> None:
        with override_settings(
            NEXT_FRAMEWORK={"DEFER": {"TOKENS": {"OPTIONS": {"BUCKET": 600}}}}
        ):
            [message] = check_defer_settings()
        assert message.id == "next.E233"
        assert "must be an integer below MAX_AGE, which is 600, or None" in message.msg
        assert "born expired" in message.msg

    @pytest.mark.parametrize("age", [-1, 0])
    def test_a_max_age_below_one_yields_e233(self, age: int) -> None:
        with override_settings(
            NEXT_FRAMEWORK={"DEFER": {"TOKENS": {"OPTIONS": {"MAX_AGE": age}}}}
        ):
            [message] = check_defer_settings()
        assert message.id == "next.E233"
        assert "an integer from 1 or a dotted path" in message.msg
        assert "Keep MAX_AGE from 1" in (message.hint or "")

    def test_spill_bytes_is_no_token_option(self) -> None:
        with override_settings(
            NEXT_FRAMEWORK={"DEFER": {"TOKENS": {"OPTIONS": {"SPILL_BYTES": 128}}}}
        ):
            [message] = check_defer_settings()
        assert message.id == "next.E035"

    def test_a_dotted_max_age_passes(self) -> None:
        with override_settings(
            NEXT_FRAMEWORK={
                "DEFER": {"TOKENS": {"OPTIONS": {"MAX_AGE": "myapp.tokens.age"}}}
            }
        ):
            assert check_defer_settings() == []

    def test_a_max_age_above_a_day_yields_w175(self) -> None:
        with override_settings(
            NEXT_FRAMEWORK={"DEFER": {"TOKENS": {"OPTIONS": {"MAX_AGE": 172_800}}}}
        ):
            [message] = check_defer_settings()
        assert message.id == "next.W175"
        assert message.msg == (
            "NEXT_FRAMEWORK['DEFER']['TOKENS']['OPTIONS']['MAX_AGE'] is 172800 "
            "seconds, above one day. A leaked reload token stays usable that long."
        )

    def test_registered_check_reaches_manage_py_check_path(self) -> None:
        with override_settings(NEXT_FRAMEWORK={"DEFER": {"DEADLINE": 0}}):
            messages = run_checks(tags=[NEXT])
        assert any(
            m.id == "next.E076" and "['DEFER']['DEADLINE']" in m.msg for m in messages
        )
