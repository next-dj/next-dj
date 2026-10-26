import pytest

from next.server.checks import check_dev_server
from next.testing import override_next_settings
from tests.support import check_ids


class RecordingServer:
    def run(self, app, **options: object) -> None:
        """Serve nothing."""


def test_the_default_server_reports_nothing() -> None:
    assert check_dev_server() == []


def test_a_server_class_reports_nothing() -> None:
    with override_next_settings(DEV_SERVER=f"{__name__}.RecordingServer"):
        assert check_dev_server() == []


@pytest.mark.parametrize("path", [f"{__name__}.Missing", "builtins.object"])
def test_a_path_naming_no_server_reports_e212(path: str) -> None:
    with override_next_settings(DEV_SERVER=path):
        assert check_ids(check_dev_server()) == ["next.E212"]
