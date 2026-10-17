from django.core.checks import Error, Info, Warning as DjangoWarning, run_checks

from next.boundaries import imports as imports_module
from next.boundaries.checks import check_boundaries
from next.boundaries.rules import (
    DISABLED,
    DOCTRINE,
    INSTALL_HINT,
    OFF_MESSAGE,
    RULES,
    SCAN,
    STRICT_OFF,
    UNRECORDED,
)
from next.checks import BOUNDARIES, NEXT, register_all
from tests.support.boundaries import boundary_project


_PROJECT = {
    "shop": {"views.py": "from crm.utils import helper\n"},
    "crm": {"utils.py": "helper = 1\n"},
}

_W = "next.W136"

_STRICT = {"BOUNDARIES": "strict"}


def _boundary_ids(messages) -> list[str]:
    return [message.id for message in messages if message.id in RULES]


class TestCheckBoundaries:
    """Each finding is one check message, its hint pointing to the doctrine."""

    def test_a_warning_and_the_strict_error(self, tmp_path) -> None:
        with boundary_project(tmp_path, _PROJECT, framework={"BOUNDARIES": "strict"}):
            warning, error = check_boundaries()

        assert type(warning) is DjangoWarning
        assert warning.id == _W
        assert warning.obj == "shop/views.py:1"
        assert warning.msg == (
            "shop imports crm.utils, which crm does not publish. Only its models, "
            "services and signals modules are public."
        )
        assert warning.hint == (
            "Import from the models, services or signals module of the other app, or "
            f"move what you need there. See {DOCTRINE}#public-modules."
        )
        assert type(error) is Error
        assert (error.id, error.obj) == (UNRECORDED, "boundaries.toml")

    def test_a_finding_without_a_file_names_no_object(self, tmp_path) -> None:
        broken = {"shop": {"views.py": "def broken(:\n"}}

        with boundary_project(tmp_path, broken):
            (message,) = check_boundaries()

        assert (type(message), message.id, message.obj) == (Error, SCAN, None)

    def test_without_grimp_one_info(self, tmp_path, monkeypatch) -> None:
        monkeypatch.setattr(imports_module, "AVAILABLE", False)
        register_all()

        with boundary_project(tmp_path, _PROJECT):
            (message,) = run_checks(tags=[BOUNDARIES])

        assert type(message) is Info
        assert (message.id, message.obj, message.msg) == (DISABLED, None, OFF_MESSAGE)
        assert message.hint == f"{INSTALL_HINT} See {DOCTRINE}#installing."
        assert INSTALL_HINT == "Install the next.dj[boundaries] extra to turn them on."

    def test_a_clean_project_has_no_message(self, tmp_path) -> None:
        with boundary_project(tmp_path, {"shop": {"views.py": "x = 1\n"}}):
            assert check_boundaries() == []


class TestTags:
    """The boundary checks answer to their own tag and stay out of `next`."""

    def test_the_next_tag_leaves_them_out(self, tmp_path) -> None:
        register_all()

        with boundary_project(tmp_path, _PROJECT):
            messages = run_checks(tags=[NEXT])

        assert _boundary_ids(messages) == []

    def test_the_boundaries_tag_runs_them_alone(self, tmp_path) -> None:
        register_all()

        with boundary_project(tmp_path, _PROJECT):
            messages = run_checks(tags=[BOUNDARIES])

        assert [message.id for message in messages] == [_W]


class TestStrictOff:
    """In the strict mode unchecked boundaries are a warning, not an info."""

    def test_without_grimp(self, tmp_path, monkeypatch) -> None:
        monkeypatch.setattr(imports_module, "AVAILABLE", False)
        register_all()

        with boundary_project(tmp_path, _PROJECT, framework=_STRICT):
            (message,) = run_checks(tags=[BOUNDARIES])

        assert type(message) is DjangoWarning
        assert message.id == STRICT_OFF
        assert message.hint == (
            "Install the next.dj[boundaries] extra and point BASE_DIR at the project. "
            f"See {DOCTRINE}#installing."
        )
