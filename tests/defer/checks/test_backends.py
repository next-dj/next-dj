import pytest
from django.test import override_settings

from next.defer.checks import check_defer_backends
from next.testing import override_next_settings
from tests.support import check_ids


def tenant(request) -> str | None:
    return None


class Foreign:
    """A token backend path the shipped checks leave alone."""


def _tokens(**options: object) -> dict[str, object]:
    return {"TOKENS": {"OPTIONS": options}}


def _messages(defer: dict[str, object]) -> list[str]:
    with override_next_settings(DEFER=defer):
        return [message.msg for message in check_defer_backends()]


class TestCheckDeferBackends:
    """``check_defer_backends`` reports an unusable backend path as ``next.E219``."""

    def test_the_defaults_report_nothing(self) -> None:
        assert check_defer_backends() == []

    def test_settings_without_a_defer_scope_report_nothing(self) -> None:
        with override_settings(NEXT_FRAMEWORK={}):
            assert check_defer_backends() == []

    @pytest.mark.parametrize(
        "defer",
        [
            {"ADMISSION": {"BACKEND": "next.defer.QueueDelay"}},
            {"DELIVERY": "not a mapping", "ADMISSION": 3},
            {"DELIVERY": {"BACKEND": ""}},
            {"EXECUTOR": {"OPTIONS": {"MAX_WORKERS": 2}}},
        ],
    )
    def test_usable_or_misshaped_values_report_nothing(
        self, defer: dict[str, object]
    ) -> None:
        with override_next_settings(DEFER=defer):
            assert check_defer_backends() == []

    def test_a_path_that_does_not_import_reports_e219(self) -> None:
        with override_next_settings(DEFER={"DELIVERY": {"BACKEND": "nowhere.Policy"}}):
            messages = check_defer_backends()
        assert check_ids(messages) == ["next.E219"]
        assert (
            "['DELIVERY']['BACKEND'] 'nowhere.Policy' does not import"
            in messages[0].msg
        )
        assert "'next.defer.DefaultDeliveryPolicy'" in (messages[0].hint or "")

    @pytest.mark.parametrize(
        ("path", "words"),
        [
            ("builtins.object", "is no subclass of Admission"),
            ("os.path.join", "names no class"),
        ],
    )
    def test_a_path_outside_the_family_reports_e219(
        self, path: str, words: str
    ) -> None:
        (message,) = _messages({"ADMISSION": {"BACKEND": path}})
        assert f"['ADMISSION']['BACKEND'] {path!r} {words}" in message

    def test_an_executor_that_does_not_import_reports_e219(self) -> None:
        with override_next_settings(DEFER={"EXECUTOR": {"BACKEND": "nowhere.Pool"}}):
            (message,) = check_defer_backends()
        assert "['EXECUTOR']['BACKEND'] 'nowhere.Pool' does not import" in message.msg
        assert "'next.defer.LaneExecutor'" in (message.hint or "")

    def test_abstract_roots_report_e219_for_each_family(self) -> None:
        defer = {
            "EXECUTOR": {"BACKEND": "next.defer.BoundaryExecutor"},
            "ADMISSION": {"BACKEND": "next.defer.Admission"},
            "DELIVERY": {"BACKEND": "next.defer.DeliveryPolicy"},
            "SERVER": {"BACKEND": "next.defer.ServerProbe"},
        }
        messages = _messages(defer)
        assert len(messages) == 4
        assert "['EXECUTOR']['BACKEND']" in messages[0]
        assert "['ADMISSION']['BACKEND']" in messages[1]
        assert "['DELIVERY']['BACKEND']" in messages[2]
        assert "['SERVER']['BACKEND']" in messages[3]
        assert all("is an abstract class" in message for message in messages)


class TestStatePaths:
    """Each ``THREAD_STATE`` and ``CONNECTION_STATE`` entry names a usable backend."""

    @pytest.mark.parametrize(
        ("key", "base"),
        [("THREAD_STATE", "ThreadState"), ("CONNECTION_STATE", "ConnectionState")],
    )
    def test_an_entry_that_names_no_backend_reports_e219(self, key, base) -> None:
        with override_next_settings(DEFER={key: ["nowhere.State", "builtins.object"]}):
            messages = check_defer_backends()
        assert check_ids(messages) == ["next.E219", "next.E219"]
        assert f"['{key}'] 'nowhere.State' does not import" in messages[0].msg
        assert f"is no subclass of {base}" in messages[1].msg

    def test_a_list_that_is_no_list_is_left_to_its_shape(self) -> None:
        with override_next_settings(DEFER={"THREAD_STATE": "nowhere.State"}):
            assert check_defer_backends() == []


class TestTokenPaths:
    """``check_defer_backends`` reports token paths that name nothing as ``next.E219``."""

    def test_a_token_backend_that_does_not_import_reports_e219(self) -> None:
        (message,) = _messages({"TOKENS": {"BACKEND": "nowhere.Tokens"}})
        assert "['TOKENS']['BACKEND'] 'nowhere.Tokens' does not import" in message

    @pytest.mark.parametrize(
        ("key", "path", "words"),
        [
            ("STORAGE", "nowhere.Storage", "does not import"),
            ("STORAGE", "next.defer.SignedSeal", "is no subclass of TokenStorage"),
            ("SEAL", "next.defer.TokenSeal", "is an abstract class"),
            ("MAX_AGE", "next.defer.tokens.claims.VERSION", "names no callable"),
            ("BINDING", "nowhere.binding", "does not import"),
            ("BINDING", "next.defer.tokens.claims.VERSION", "names no callable"),
            ("BINDING", "builtins.int", "is no subclass of SubjectBinding"),
            ("BINDING", "next.identity.SubjectBinding", "is an abstract class"),
            ("BINDING", "next.defer.tokens.binding.CallableBinding", "takes arguments"),
        ],
    )
    def test_an_unusable_option_reports_e219(
        self, key: str, path: str, words: str
    ) -> None:
        (message,) = _messages(_tokens(**{key: path}))
        assert f"['TOKENS']['OPTIONS'][{key!r}] {path!r} {words}" in message

    def test_an_unusable_binding_hints_at_every_accepted_form(self) -> None:
        with override_next_settings(
            DEFER=_tokens(BINDING="next.defer.tokens.binding.CallableBinding")
        ):
            (message,) = check_defer_backends()
        assert message.id == "next.E219"
        assert "SubjectBinding subclass that takes no arguments" in (message.hint or "")
        assert "request callable" in (message.hint or "")

    @pytest.mark.parametrize(
        ("key", "path"),
        [
            ("BINDING", "session"),
            ("BINDING", "next.defer.tokens.binding.UserBinding"),
            ("BINDING", f"{__name__}.tenant"),
            ("MAX_AGE", f"{__name__}.tenant"),
            ("MAX_AGE", 60),
            ("SEAL", "next.defer.SignedSeal"),
        ],
    )
    def test_a_usable_option_reports_nothing(self, key: str, path: object) -> None:
        assert _messages(_tokens(**{key: path})) == []

    def test_a_foreign_backend_owns_its_options(self) -> None:
        defer = {
            "TOKENS": {
                "BACKEND": "next.defer.StackedTokenBackend",
                "OPTIONS": {"STORAGE": "nowhere.Storage"},
            }
        }
        assert len(_messages(defer)) == 1
        defer["TOKENS"]["BACKEND"] = f"{__name__}.Foreign"
        assert all("Foreign" in message for message in _messages(defer))

    def test_a_tokens_value_that_is_no_mapping_reads_the_shipped_backend(self) -> None:
        assert _messages({"TOKENS": "nope"}) == []
