import pytest
from django.test import override_settings

from next.defer.checks import check_defer_settings
from next.defer.checks.settings import shipped, value_at, where
from tests.support.checks import written


class TestCheckDeferSettings:
    def test_silent_without_the_setting(self) -> None:
        with override_settings(NEXT_FRAMEWORK={}):
            assert check_defer_settings() == []

    def test_silent_on_a_non_dict_setting(self) -> None:
        with override_settings(NEXT_FRAMEWORK=42):
            assert check_defer_settings() == []

    def test_silent_on_a_non_dict_scope(self) -> None:
        with written({"DEFER": []}):
            assert check_defer_settings() == []

    @pytest.mark.parametrize(
        ("defer", "check_id", "text"),
        [
            pytest.param(
                {"DELIVERY": {"OPTIONS": {"SYNC_WORKERS": "thread"}}},
                "next.E076",
                "NEXT_FRAMEWORK['DEFER']['DELIVERY']['OPTIONS']['SYNC_WORKERS'] must be "
                "'inline' or 'stream', got 'thread'. The first read of the scope "
                "raises ImproperlyConfigured for it.",
                id="misfit-value",
            ),
            pytest.param(
                {"MORE": {"MAX_RESTORE": 3}},
                "next.E035",
                "NEXT_FRAMEWORK['DEFER'] has unknown keys 'MORE'. Allowed keys are "
                "ADMISSION, CONNECTION_STATE, DEADLINE, DEADLINE_TO_DB, DELIVERY, "
                "EXECUTOR, MAX_BOUNDARIES, MAX_DEPTH, MAX_INSTANCES, MAX_PER_SITE, "
                "MAX_STREAMS, NOSCRIPT, REQUEST_CONCURRENCY, SERVER, THREAD_STATE, "
                "TOKENS, WRITE_GRACE, WRITE_TIMEOUT.",
                id="unknown-key",
            ),
            pytest.param(
                {"EXECUTOR": {"MAX_WORKERS": 4}},
                "next.E035",
                "NEXT_FRAMEWORK['DEFER']['EXECUTOR'] has unknown keys 'MAX_WORKERS'. "
                "Allowed keys are BACKEND, MAX_REQUEST_JOBS, OPTIONS, PARALLEL, "
                "PER_REQUEST, QUEUE_TIMEOUT, SHED_DELAY, SQLITE, "
                "THREAD_LOCAL_MIDDLEWARE.",
                id="unknown-section",
            ),
        ],
    )
    def test_a_bad_entry_yields_its_check(
        self, defer: dict[str, object], check_id: str, text: str
    ) -> None:
        with override_settings(NEXT_FRAMEWORK={"DEFER": defer}):
            [message] = check_defer_settings()
        assert (message.id, message.msg) == (check_id, text)

    def test_a_foreign_backend_option_the_framework_reads_yields_e076(self) -> None:
        options = {"STREAM": "x", "OWN": "kept"}
        scope = {"DELIVERY": {"BACKEND": "myapp.Policy", "OPTIONS": options}}
        with override_settings(NEXT_FRAMEWORK={"DEFER": scope}):
            [message] = check_defer_settings()
        assert message.id == "next.E076"
        assert "['DELIVERY']['OPTIONS']['STREAM']" in message.msg

    def test_a_foreign_executor_keeps_its_own_options(self) -> None:
        executor = {"BACKEND": "myapp.Executor", "OPTIONS": {"LANES": 3}}
        with override_settings(NEXT_FRAMEWORK={"DEFER": {"EXECUTOR": executor}}):
            assert check_defer_settings() == []

    @pytest.mark.parametrize(
        ("admission", "check_id", "words"),
        [
            pytest.param(
                {"BACKEND": "next.defer.QueueDelay", "OPTIONS": {"LIMIT": 3}},
                "next.E035",
                "['ADMISSION']['OPTIONS'] has unknown keys 'LIMIT'",
                id="option",
            ),
            pytest.param(
                "next.defer.QueueDelay",
                "next.E076",
                "['ADMISSION'] must be a dict",
                id="path",
            ),
        ],
    )
    def test_the_admission_is_an_entry_whose_shipped_backend_takes_no_option(
        self, admission: object, check_id: str, words: str
    ) -> None:
        with override_settings(NEXT_FRAMEWORK={"DEFER": {"ADMISSION": admission}}):
            [message] = check_defer_settings()
        assert message.id == check_id
        assert words in message.msg

    @pytest.mark.parametrize("key", ["KEYS", "BUCKET", "CODEC"])
    def test_a_removed_token_option_is_an_unknown_key(self, key: str) -> None:
        with override_settings(
            NEXT_FRAMEWORK={"DEFER": {"TOKENS": {"OPTIONS": {key: None}}}}
        ):
            [message] = check_defer_settings()
        assert message.id == "next.E035"

    @pytest.mark.parametrize("age", [-1, 0])
    def test_a_max_age_below_one_yields_e233(self, age: int) -> None:
        with override_settings(
            NEXT_FRAMEWORK={"DEFER": {"TOKENS": {"OPTIONS": {"MAX_AGE": age}}}}
        ):
            [message] = check_defer_settings()
        assert message.id == "next.E233"
        assert "an integer from 1 or a dotted path" in message.msg
        assert "born expired" in message.msg
        assert "Keep MAX_AGE an integer from 1" in (message.hint or "")

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
        assert shipped(("ADMISSION", "BACKEND")) == "next.defer.QueueDelay"
        assert where(("TOKENS", "OPTIONS")) == (
            "NEXT_FRAMEWORK['DEFER']['TOKENS']['OPTIONS']"
        )
