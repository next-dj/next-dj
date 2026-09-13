from __future__ import annotations

import re

import pytest
from django.conf import settings
from django.template import Context, Template, base as template_base, engines
from django.template.base import DebugLexer, Lexer, TokenType
from django.test import override_settings

from next.apps import templates as next_templates
from next.apps.templates import _BUILTIN_MODULES


_JINJA_BACKEND = "django.template.backends.jinja2.Jinja2"
_DJANGO_BACKEND = "django.template.backends.django.DjangoTemplates"

_DJANGO_TAG_PATTERN = r"({%.*?%}|{{.*?}}|{#.*?#})"
_WIDENED_TAG_PATTERN = r"((?s:{%.*?%})|{{.*?}}|{#.*?#})"

_MULTILINE_BLOCK_TAG = '{% component\n    "card"\n%}'

# A comment and a variable broken over a line break, which Django lexes as plain
# template text. Widening the block-tag branch alone must keep it that way.
_UNLEXED_SOURCE = "A {# note\nstill note #} B\nC {{ x\n}} D"


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


class TestBlockTagLexingSpansLines:
    """Dot-matches-newline reaches block tags and nothing else."""

    @pytest.mark.parametrize("lexer_class", [Lexer, DebugLexer], ids=("plain", "debug"))
    def test_multiline_block_tag_lexes_as_one_token(
        self, lexer_class: type[Lexer]
    ) -> None:
        """Both lexers read the widened pattern from the same module global."""
        tokens = lexer_class(_MULTILINE_BLOCK_TAG).tokenize()
        assert [token.token_type for token in tokens] == [TokenType.BLOCK]

    @pytest.mark.parametrize("lexer_class", [Lexer, DebugLexer], ids=("plain", "debug"))
    def test_multiline_comment_and_variable_stay_text(
        self, lexer_class: type[Lexer]
    ) -> None:
        tokens = lexer_class(_UNLEXED_SOURCE).tokenize()
        assert [token.token_type for token in tokens] == [TokenType.TEXT]

    def test_unlexed_source_renders_verbatim(self) -> None:
        assert Template(_UNLEXED_SOURCE).render(Context({"x": 1})) == _UNLEXED_SOURCE


class TestLexerInstall:
    """The rebind derives from Django's own pattern and repeats as a no-op."""

    def test_installed_pattern_pins_djangos_shape(self) -> None:
        """A Django release that respells the tag pattern fails here first."""
        assert template_base.tag_re.pattern == _WIDENED_TAG_PATTERN

    def test_widening_scopes_the_flag_to_the_block_branch(self) -> None:
        widened = next_templates._multiline_tag_pattern(_DJANGO_TAG_PATTERN)
        assert widened == _WIDENED_TAG_PATTERN

    def test_install_widens_an_unpatched_pattern(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(template_base, "tag_re", re.compile(_DJANGO_TAG_PATTERN))
        next_templates.install()
        assert template_base.tag_re.pattern == _WIDENED_TAG_PATTERN

    def test_second_install_keeps_the_compiled_pattern(self) -> None:
        next_templates.install()
        installed = template_base.tag_re
        next_templates.install()
        assert template_base.tag_re is installed

    def test_unrecognised_pattern_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """An unwidened pattern would turn every multi-line tag into text."""
        monkeypatch.setattr(template_base, "tag_re", re.compile(r"({{.*?}})"))
        with pytest.raises(RuntimeError, match="block-tag branch"):
            next_templates.install()


class TestBuiltinsFollowAnOverride:
    """``override_settings(TEMPLATES=...)`` rebuilds the engines, so builtins reinstall."""

    def test_next_tags_resolve_under_overridden_templates(self) -> None:
        with override_settings(TEMPLATES=[_django_engine()]):
            assert Template('{% component "card" %}').render(Context({})) == ""

    def test_engine_dicts_are_never_written_in_place(self) -> None:
        """The settings module owns those dicts and an override restores them by reference."""
        engine = _django_engine()
        with override_settings(TEMPLATES=[engine]):
            assert settings.TEMPLATES[0]["OPTIONS"]["builtins"]
        assert engine["OPTIONS"]["builtins"] == []

    def test_builtins_reach_engines_read_before_the_install(self) -> None:
        """A handler that already read `TEMPLATES` is dropped, so it reads again."""
        with override_settings(TEMPLATES=[_django_engine()]):
            # A plain write emits no `setting_changed`, so the handler below
            # reads the engines an app readied before next-dj would have read.
            settings.TEMPLATES = [_django_engine()]
            assert engines.templates["django"]["OPTIONS"]["builtins"] == []

            next_templates._install_builtins()

            assert Template('{% component "card" %}').render(Context({})) == ""

    def test_another_setting_leaves_templates_alone(self) -> None:
        before = settings.TEMPLATES
        next_templates._on_setting_changed(setting="INSTALLED_APPS")
        assert settings.TEMPLATES is before
