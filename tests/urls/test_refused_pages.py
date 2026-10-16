from collections.abc import Iterator

import pytest
from django.core.management import call_command
from django.core.management.base import SystemCheckError

from next.checks import reset_check_caches
from tests.support import installed_apps, write_app


@pytest.fixture(autouse=True)
def _fresh_checks() -> Iterator[None]:
    reset_check_caches()
    yield
    reset_check_caches()


@pytest.mark.parametrize("tags", [None, ["next"]], ids=["all", "next"])
class TestRefusedPagesAtCheckTime:
    """A refused page is reported by the checks, not raised by the URLconf."""

    def test_a_segment_repeating_a_prefix_kwarg_is_e154(self, tmp_path, tags) -> None:
        write_app(
            tmp_path,
            "wallet_app",
            {"pages": ("", "dup/[int:wallet]")},
            mounts={"pages": "w/<int:wallet>/"},
        )

        with (
            installed_apps(tmp_path, "wallet_app.apps.Config"),
            pytest.raises(SystemCheckError, match=r"next\.E154") as excinfo,
        ):
            call_command("check", tags=tags)

        assert "w/<int:wallet>/" in str(excinfo.value)

    def test_a_segment_repeating_a_segment_kwarg_is_e028(self, tmp_path, tags) -> None:
        write_app(tmp_path, "twice_app", {"pages": ("", "twice/[x]/[x]")})

        with (
            installed_apps(tmp_path, "twice_app.apps.Config"),
            pytest.raises(SystemCheckError, match=r"next\.E028"),
        ):
            call_command("check", tags=tags)
