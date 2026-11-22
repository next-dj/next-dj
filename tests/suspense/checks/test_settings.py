import pytest
from django.test import override_settings

from next.suspense.checks import check_defer_settings
from next.suspense.checks.settings import shipped, value_at, where


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

    def test_a_foreign_backend_option_the_framework_reads_yields_e076(self) -> None:
        options = {"MAX_WORKERS": "x", "OWN": "kept"}
        scope = {"EXECUTOR": {"BACKEND": "myapp.Executor", "OPTIONS": options}}
        with override_settings(NEXT_FRAMEWORK={"DEFER": scope}):
            [message] = check_defer_settings()
        assert message.id == "next.E076"
        assert "['EXECUTOR']['OPTIONS']['MAX_WORKERS']" in message.msg

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


class TestPaths:
    """The `DEFER` paths the checks read and name."""

    def test_a_value_is_read_through_nested_mappings(self) -> None:
        scope = {"TOKENS": {"OPTIONS": {"EPOCH": 2}}}
        assert value_at(scope, ("TOKENS", "OPTIONS", "EPOCH")) == 2
        assert value_at(scope, ("TOKENS", "OPTIONS", "EPOCH", "DEEPER")) is None

    def test_the_shipped_value_and_the_name_of_a_path(self) -> None:
        assert shipped(("ADMISSION",)) == "next.suspense.QueueDelay"
        assert where(("TOKENS", "OPTIONS")) == (
            "NEXT_FRAMEWORK['DEFER']['TOKENS']['OPTIONS']"
        )
