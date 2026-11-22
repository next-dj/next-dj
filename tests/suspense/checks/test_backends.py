import pytest
from django.test import override_settings

from next.suspense.checks import check_defer_backends
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
            {"EXECUTOR": {"BACKEND": "next.suspense.LaneExecutor"}},
            {"ADMISSION": "next.suspense.QueueDelay"},
            {"EXECUTOR": "not a mapping", "ADMISSION": 3},
            {"EXECUTOR": {"BACKEND": ""}},
            {"EXECUTOR": {"OPTIONS": {"MAX_WORKERS": 2}}},
        ],
    )
    def test_usable_or_misshaped_values_report_nothing(
        self, defer: dict[str, object]
    ) -> None:
        with override_next_settings(DEFER=defer):
            assert check_defer_backends() == []

    def test_a_path_that_does_not_import_reports_e219(self) -> None:
        with override_next_settings(DEFER={"EXECUTOR": {"BACKEND": "nowhere.Pool"}}):
            messages = check_defer_backends()
        assert check_ids(messages) == ["next.E219"]
        assert (
            "['EXECUTOR']['BACKEND'] 'nowhere.Pool' does not import" in messages[0].msg
        )
        assert "'next.suspense.LaneExecutor'" in (messages[0].hint or "")

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
        (message,) = _messages({"ADMISSION": path})
        assert f"['ADMISSION'] {path!r} {words}" in message

    def test_abstract_roots_report_e219_for_each_family(self) -> None:
        defer = {
            "EXECUTOR": {"BACKEND": "next.suspense.BoundaryExecutor"},
            "ADMISSION": "next.suspense.Admission",
            "POLICY": {"BACKEND": "next.suspense.StreamPolicy"},
            "TRANSPORT": {"BACKEND": "next.suspense.StreamTransport"},
        }
        messages = _messages(defer)
        assert len(messages) == 4
        assert "['POLICY']['BACKEND']" in messages[2]
        assert "['TRANSPORT']['BACKEND']" in messages[3]
        assert all("is an abstract class" in message for message in messages)


class TestTokenPaths:
    """``check_defer_backends`` reports token paths that name nothing as ``next.E219``."""

    def test_a_token_backend_that_does_not_import_reports_e219(self) -> None:
        (message,) = _messages({"TOKENS": {"BACKEND": "nowhere.Tokens"}})
        assert "['TOKENS']['BACKEND'] 'nowhere.Tokens' does not import" in message

    @pytest.mark.parametrize(
        ("key", "path", "words"),
        [
            ("CODEC", "nowhere.Codec", "does not import"),
            ("STORAGE", "next.suspense.JsonCodec", "is no subclass of TokenStorage"),
            ("SEAL", "next.suspense.TokenSeal", "is an abstract class"),
            ("TRANSPORT", "builtins.int", "is no subclass of TokenTransport"),
            ("KEYS", "nowhere.keys", "does not import"),
            ("MAX_AGE", "next.suspense.tokens.claims.VERSION", "names no callable"),
            ("BINDING", "nowhere.binding", "does not import"),
            ("BINDING", "next.suspense.tokens.claims.VERSION", "names no callable"),
            ("BINDING", "builtins.int", "is no subclass of SubjectBinding"),
            ("BINDING", "next.identity.SubjectBinding", "is an abstract class"),
            ("BINDING", "next.suspense.CallableBinding", "takes arguments"),
        ],
    )
    def test_an_unusable_option_reports_e219(
        self, key: str, path: str, words: str
    ) -> None:
        (message,) = _messages(_tokens(**{key: path}))
        assert f"['TOKENS']['OPTIONS'][{key!r}] {path!r} {words}" in message

    def test_an_unusable_binding_hints_at_every_accepted_form(self) -> None:
        with override_next_settings(
            DEFER=_tokens(BINDING="next.suspense.CallableBinding")
        ):
            (message,) = check_defer_backends()
        assert message.id == "next.E219"
        assert "SubjectBinding subclass that takes no arguments" in (message.hint or "")
        assert "request callable" in (message.hint or "")

    @pytest.mark.parametrize(
        ("key", "path"),
        [
            ("BINDING", "session"),
            ("BINDING", "next.suspense.UserBinding"),
            ("BINDING", f"{__name__}.tenant"),
            ("KEYS", f"{__name__}.tenant"),
            ("MAX_AGE", f"{__name__}.tenant"),
            ("MAX_AGE", 60),
            ("SEAL", "next.suspense.SignedSeal"),
        ],
    )
    def test_a_usable_option_reports_nothing(self, key: str, path: object) -> None:
        assert _messages(_tokens(**{key: path})) == []

    def test_a_foreign_backend_owns_its_options(self) -> None:
        defer = {
            "TOKENS": {
                "BACKEND": "next.suspense.StackedTokenBackend",
                "OPTIONS": {"CODEC": "nowhere.Codec"},
            }
        }
        assert len(_messages(defer)) == 1
        defer["TOKENS"]["BACKEND"] = f"{__name__}.Foreign"
        assert all("Foreign" in message for message in _messages(defer))

    def test_a_tokens_value_that_is_no_mapping_reads_the_shipped_backend(self) -> None:
        assert _messages({"TOKENS": "nope"}) == []
