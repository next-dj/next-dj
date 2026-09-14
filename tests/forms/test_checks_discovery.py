import textwrap
from pathlib import Path

from django.test import override_settings

from next.forms.checks import (
    check_form_wizard_steps,
    check_wizard_step_field_collisions,
)
from tests.support import default_page_router_config, isolated_form_registries


_COLLIDING_WIZARD_PAGE = """
    from typing import ClassVar

    from django import forms

    from next.forms import Form, FormWizard


    class FirstStep(Form):
        shared = forms.CharField(max_length=10)


    class SecondStep(Form):
        shared = forms.CharField(max_length=10)


    class CollidingWizard(FormWizard):
        class Meta:
            steps: ClassVar = [("first", FirstStep), ("second", SecondStep)]
            url_param = "step"
"""


_STEPLESS_WIZARD_PAGE = """
    from next.forms import FormWizard


    class SteplessWizard(FormWizard):
        pass
"""


def _write_page(tmp_path: Path, body: str) -> Path:
    directory = tmp_path / "flow" / "[step]"
    directory.mkdir(parents=True)
    page_file = directory / "page.py"
    page_file.write_text(textwrap.dedent(body))
    return page_file


def _pages_settings(tmp_path: Path) -> dict[str, object]:
    return {"PAGE_BACKENDS": default_page_router_config(tmp_path)}


class TestFormsChecksDiscoverTheirOwnPages:
    """A forms check imports the routed pages instead of waiting for another check."""

    def test_page_scoped_wizard_reaches_the_field_collision_check(
        self, tmp_path: Path
    ) -> None:
        _write_page(tmp_path, _COLLIDING_WIZARD_PAGE)
        with (
            isolated_form_registries(),
            override_settings(
                BASE_DIR=tmp_path, NEXT_FRAMEWORK=_pages_settings(tmp_path)
            ),
        ):
            warnings = check_wizard_step_field_collisions()
        assert [warning.id for warning in warnings] == ["next.W059"]
        assert "shared" in warnings[0].msg

    def test_page_scoped_wizard_reaches_a_diagnostics_check(
        self, tmp_path: Path
    ) -> None:
        _write_page(tmp_path, _STEPLESS_WIZARD_PAGE)
        with (
            isolated_form_registries(),
            override_settings(
                BASE_DIR=tmp_path, NEXT_FRAMEWORK=_pages_settings(tmp_path)
            ),
        ):
            errors = check_form_wizard_steps()
        assert [error.id for error in errors] == ["next.E050"]
        assert "SteplessWizard" in errors[0].msg
