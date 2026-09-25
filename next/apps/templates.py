"""Register next-dj templatetag modules as builtins and widen block-tag lexing."""

from __future__ import annotations

import re
from typing import Any, cast

from django.conf import settings
from django.core.signals import setting_changed
from django.template import base as template_base, engines
from django.template.engine import Engine


_BUILTIN_MODULES = (
    "next.templatetags.forms",
    "next.templatetags.components",
    "next.templatetags.next_static",
    "next.templatetags.pages",
    "next.templatetags.partial",
)

_DJANGO_BACKEND = "django.template.backends.django.DjangoTemplates"

_BLOCK_TAG_BRANCH = "{%.*?%}"

_NEXT_TAG_NAMES = (
    "action_url",
    "asset",
    "collect_scripts",
    "collect_styles",
    "component",
    "form",
    "metadata",
    "set_slot",
    "slot",
    "template",
    "use_module",
    "use_script",
    "use_style",
    "zone",
)
_NEXT_TAG_ALTERNATION = "|".join(_NEXT_TAG_NAMES)
_NEXT_BLOCK_TAG_BRANCH = rf"{{%\s*#?(?:{_NEXT_TAG_ALTERNATION})\b(?s:.*?)%}}"
_MULTILINE_BLOCK_TAG_BRANCH = f"(?:{_NEXT_BLOCK_TAG_BRANCH}|{_BLOCK_TAG_BRANCH})"

_UNKNOWN_TAG_PATTERN = (
    "Django's template tag pattern no longer spells its block-tag branch "
    f"{_BLOCK_TAG_BRANCH!r}, so next-dj cannot widen it to span lines"
)


def _multiline_tag_pattern(pattern: str) -> str:
    """Return *pattern* with a line-spanning branch for next-dj's own block tags.

    Raises when Django's branch is not found, and every tag outside the next-dj
    set keeps that branch, so a stray `{%` elsewhere swallows no extra text.
    """
    if _MULTILINE_BLOCK_TAG_BRANCH in pattern:
        return pattern
    if _BLOCK_TAG_BRANCH not in pattern:
        raise RuntimeError(_UNKNOWN_TAG_PATTERN)
    return pattern.replace(_BLOCK_TAG_BRANCH, _MULTILINE_BLOCK_TAG_BRANCH, 1)


def _install_lexer() -> None:
    """Let a next-dj block tag span lines, leaving every other tag as Django lexes it.

    Both `Lexer.tokenize` and `DebugLexer` read the pattern as a module global, so the
    widened branch reaches every lexing path and the install repeats as a no-op.
    """
    pattern = _multiline_tag_pattern(template_base.tag_re.pattern)
    if pattern != template_base.tag_re.pattern:
        template_base.tag_re = re.compile(pattern)


def _engine_with_builtins(engine: dict[str, Any]) -> dict[str, Any] | None:
    """Return a copy of *engine* carrying the builtins, or `None` when it has them.

    Copies rather than writes in place, because the engine dicts belong to the
    settings module and an override restores them by reference.
    """
    options: dict[str, Any] = engine.get("OPTIONS", {})
    builtins: list[str] = list(options.get("builtins", []))
    missing = [module for module in _BUILTIN_MODULES if module not in builtins]
    if not missing:
        return None
    return {**engine, "OPTIONS": {**options, "builtins": [*builtins, *missing]}}


def _forget_engines() -> None:
    """Drop engines built from the `TEMPLATES` value that carried no builtins.

    Only a fresh read reaches an engine handler that already read the settings, and the
    memo behind `Engine.get_default` is a second one, read by every bare `Template`.
    """
    engines.__dict__.pop("templates", None)
    engines._templates = None  # type: ignore[attr-defined]
    engines._engines = {}  # type: ignore[attr-defined]
    Engine.get_default.cache_clear()  # type: ignore[attr-defined]


def _install_builtins() -> None:
    """Point `TEMPLATES` at engines that carry the next-dj tag libraries."""
    # The stubs type the entries as a TypedDict, the settings module holds
    # plain dicts that carry whatever keys a backend reads.
    updated = cast("list[dict[str, Any]]", list(settings.TEMPLATES))
    changed = False
    for index, engine in enumerate(updated):
        if engine.get("BACKEND") != _DJANGO_BACKEND:
            continue
        carrying = _engine_with_builtins(engine)
        if carrying is not None:
            updated[index] = carrying
            changed = True
    if changed:
        settings.TEMPLATES = updated
        _forget_engines()


def install() -> None:
    """Widen block-tag lexing and add next-dj tag modules to every Django engine."""
    _install_lexer()
    _install_builtins()


def _on_setting_changed(*, setting: str, **kwargs) -> None:
    """Reinstall the builtins when an override hands the engines a new `TEMPLATES`.

    Django rebuilds the engines from the new value, which carries no framework tags.
    """
    if setting == "TEMPLATES":
        install()


setting_changed.connect(_on_setting_changed)


__all__ = ["install"]
