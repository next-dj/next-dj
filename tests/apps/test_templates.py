from __future__ import annotations

from django.conf import settings
from django.test import override_settings

from next.apps import templates as next_templates
from next.apps.templates import _BUILTIN_MODULES


_JINJA_BACKEND = "django.template.backends.jinja2.Jinja2"
_DJANGO_BACKEND = "django.template.backends.django.DjangoTemplates"


def _django_engine() -> dict[str, object]:
    return {"BACKEND": _DJANGO_BACKEND, "OPTIONS": {"builtins": []}}


def _jinja_engine() -> dict[str, object]:
    return {"BACKEND": _JINJA_BACKEND, "OPTIONS": {}}


class TestInstallTargetsDjangoEngine:
    """``install`` only touches DjangoTemplates engines."""

    def test_builtins_land_in_django_engine_only(self) -> None:
        with override_settings(TEMPLATES=[_jinja_engine(), _django_engine()]):
            next_templates.install()
            jinja, django = settings.TEMPLATES
            django_builtins = django["OPTIONS"]["builtins"]
            for module in _BUILTIN_MODULES:
                assert module in django_builtins
            assert "builtins" not in jinja["OPTIONS"]

    def test_django_engine_at_index_one(self) -> None:
        """Index 0 being Jinja2 must not redirect builtins to the wrong engine."""
        with override_settings(TEMPLATES=[_jinja_engine(), _django_engine()]):
            next_templates.install()
            assert settings.TEMPLATES[0]["BACKEND"] == _JINJA_BACKEND
            assert settings.TEMPLATES[1]["OPTIONS"]["builtins"]


class TestInstallIdempotent:
    """A repeated ``install`` does not duplicate modules."""

    def test_second_call_keeps_single_entries(self) -> None:
        with override_settings(TEMPLATES=[_django_engine()]):
            next_templates.install()
            next_templates.install()
            builtins = settings.TEMPLATES[0]["OPTIONS"]["builtins"]
            for module in _BUILTIN_MODULES:
                assert builtins.count(module) == 1


class TestInstallMalformedEntry:
    """``install`` tolerates an engine that omits ``OPTIONS``."""

    def test_options_created_when_absent(self) -> None:
        with override_settings(TEMPLATES=[{"BACKEND": _DJANGO_BACKEND}]):
            next_templates.install()
            options = settings.TEMPLATES[0]["OPTIONS"]
            for module in _BUILTIN_MODULES:
                assert module in options["builtins"]

    def test_preserves_existing_builtins(self) -> None:
        engine = {"BACKEND": _DJANGO_BACKEND, "OPTIONS": {"builtins": ["x.y"]}}
        with override_settings(TEMPLATES=[engine]):
            next_templates.install()
            builtins = settings.TEMPLATES[0]["OPTIONS"]["builtins"]
            assert builtins[0] == "x.y"
            for module in _BUILTIN_MODULES:
                assert module in builtins


class TestInstallNoDjangoEngine:
    """With only a Jinja2 engine ``install`` writes nothing."""

    def test_jinja_only_untouched(self) -> None:
        with override_settings(TEMPLATES=[_jinja_engine()]):
            next_templates.install()
            assert "builtins" not in settings.TEMPLATES[0]["OPTIONS"]
