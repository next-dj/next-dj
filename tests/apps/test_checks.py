from __future__ import annotations

from django.core.checks import Warning as DjangoWarning
from django.test import override_settings

from next.apps.checks import check_django_templates_backend_present


_DJANGO_BACKEND = "django.template.backends.django.DjangoTemplates"
_JINJA_BACKEND = "django.template.backends.jinja2.Jinja2"


def _ids(messages: list) -> list[str]:
    return [m.id for m in messages]


class TestDjangoTemplatesBackendCheck:
    """``check_django_templates_backend_present`` guards tag availability."""

    def test_missing_backend_emits_w062(self) -> None:
        with override_settings(TEMPLATES=[{"BACKEND": _JINJA_BACKEND}]):
            messages = check_django_templates_backend_present(app_configs=None)
        assert _ids(messages) == ["next.W062"]
        assert isinstance(messages[0], DjangoWarning)

    def test_empty_templates_emits_w062(self) -> None:
        with override_settings(TEMPLATES=[]):
            messages = check_django_templates_backend_present(app_configs=None)
        assert _ids(messages) == ["next.W062"]

    def test_present_backend_emits_nothing(self) -> None:
        with override_settings(TEMPLATES=[{"BACKEND": _DJANGO_BACKEND}]):
            messages = check_django_templates_backend_present(app_configs=None)
        assert messages == []

    def test_mixed_engines_with_django_backend_pass(self) -> None:
        with override_settings(
            TEMPLATES=[
                {"BACKEND": _JINJA_BACKEND},
                {"BACKEND": _DJANGO_BACKEND},
            ],
        ):
            messages = check_django_templates_backend_present(app_configs=None)
        assert messages == []
