from collections.abc import Callable
from types import SimpleNamespace

import pytest
from django.test import override_settings

from next.components.fallback import retry_loads, unrun_error
from next.defer import BoundaryFailure, ErrorKind
from next.static import ScriptInjectionPolicy
from next.testing import override_next_settings


@pytest.fixture()
def script_policy(
    monkeypatch: pytest.MonkeyPatch,
) -> Callable[[ScriptInjectionPolicy], None]:
    """Return a setter of the injection policy the static manager reports."""

    def use(policy: ScriptInjectionPolicy) -> None:
        builder = SimpleNamespace(policy=policy)
        monkeypatch.setattr(
            "next.components.fallback.get_static_manager",
            lambda: SimpleNamespace(script_builder=lambda: builder),
        )

    return use


class TestRetryLoads:
    """A retry needs the runtime script, so it follows the injection policy."""

    @pytest.mark.parametrize(
        ("policy", "loads"),
        [
            (ScriptInjectionPolicy.AUTO, True),
            (ScriptInjectionPolicy.MANUAL, True),
            (ScriptInjectionPolicy.DISABLED, False),
        ],
    )
    def test_only_a_disabled_script_blocks_the_retry(
        self, script_policy, policy: ScriptInjectionPolicy, *, loads: bool
    ) -> None:
        script_policy(policy)
        assert retry_loads() is loads

    def test_the_default_policy_loads_the_script(self) -> None:
        assert retry_loads() is True


class TestUnrunError:
    """The error of a boundary that never ran carries no message and a fresh digest."""

    @pytest.mark.parametrize("kind", [ErrorKind.TIMEOUT, ErrorKind.OVERLOADED])
    def test_the_kind_is_shown_as_it_is(self, kind: ErrorKind) -> None:
        failure = unrun_error(kind)
        assert failure == BoundaryFailure(kind, failure.digest, kind.status, True)

    def test_every_error_has_its_own_digest(self) -> None:
        assert (
            unrun_error(ErrorKind.ERROR).digest != unrun_error(ErrorKind.ERROR).digest
        )

    def test_an_opaque_setting_shows_a_status_kind_as_an_error(self) -> None:
        with override_next_settings(DEFER={"ERRORS": {"KINDS": "opaque"}}):
            failure = unrun_error(ErrorKind.OVERLOADED)
        assert failure.kind is ErrorKind.ERROR
        assert failure.status is None

    def test_the_retry_follows_the_script_policy(self, script_policy) -> None:
        script_policy(ScriptInjectionPolicy.DISABLED)
        assert unrun_error(ErrorKind.TIMEOUT).retry is False

    @override_settings(DEBUG=True)
    def test_debug_keeps_the_message_and_the_traceback_empty(self) -> None:
        failure = unrun_error(ErrorKind.ERROR)
        assert (failure.message, failure.traceback) == ("", "")
