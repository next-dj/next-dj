"""Zone reload skeletons, the `loading.<zone>.djx` files beside a zone's template."""

from __future__ import annotations

from typing import TYPE_CHECKING, Final, cast

from django.template import Context
from django.template.base import Lexer, TokenType
from django.utils.text import unescape_string_literal

from next.caches import PageCache
from next.components.stash import loading_content
from next.seeding import TEMPLATE_PATH_KEY
from next.utils import template_edits_watched

from .boundaries import boundary_template
from .loaders import (
    PAGE_MODULE,
    layout_chain,
    load_python_module_memo,
    module_version,
    template_source,
)


if TYPE_CHECKING:
    from collections.abc import Iterator, Mapping
    from pathlib import Path


SKELETON_PREFIX: Final = "loading."
SKELETON_SUFFIX: Final = ".djx"
SKELETON_GLOB: Final = f"{SKELETON_PREFIX}*{SKELETON_SUFFIX}"
PAGE_BODY: Final = "template.djx"

_ZONE_TAG: Final = "zone"
_COMMENT_TAG: Final = "comment"
_END_COMMENT_TAG: Final = "endcomment"

_DECLARED: PageCache[Path, tuple[str, frozenset[str]]] = PageCache()
_SKELETONS: PageCache[Path, tuple[int, Mapping[str, Path]]] = PageCache()


def skeleton_zone(path: Path) -> str | None:
    """Return the zone a `loading.<zone>.djx` file name names, `None` for another."""
    name = path.name
    if not name.startswith(SKELETON_PREFIX) or not name.endswith(SKELETON_SUFFIX):
        return None
    zone = name[len(SKELETON_PREFIX) : -len(SKELETON_SUFFIX)]
    return zone or None


def skeleton_path(directory: Path, zone: str) -> Path:
    """Return where the reload skeleton of `zone` sits in `directory`."""
    return directory / f"{SKELETON_PREFIX}{zone}{SKELETON_SUFFIX}"


def _zone_names(text: str) -> Iterator[str]:
    """Yield the quoted name of every `{% zone %}` a parse of `text` reaches."""
    commented = False
    for token in Lexer(text).tokenize():
        if token.token_type is not TokenType.BLOCK:
            continue
        bits = token.split_contents()
        command = bits[0] if bits else ""
        if commented:
            commented = command != _END_COMMENT_TAG
        elif command == _COMMENT_TAG:
            commented = True
        elif command == _ZONE_TAG and len(bits) > 1:
            try:
                yield unescape_string_literal(bits[1]).strip()
            except ValueError:
                continue


def _source_text(source: Path) -> str | None:
    """Return the template text of `source`, the `template` string of a `page.py`."""
    if source.name != PAGE_MODULE:
        return template_source(source)
    text = getattr(load_python_module_memo(source), "template", None)
    return text if isinstance(text, str) else None


def declared_zones(source: Path) -> frozenset[str]:
    """Return the zones the template `source` declares, read again on an edit.

    A `page.py` declares the zones of its module-level `template` string.
    """
    text = _source_text(source)
    if text is None:
        return frozenset()
    held = _DECLARED.get(source)
    if held is not None and held[0] is text:
        return held[1]
    names = frozenset(_zone_names(text))
    _DECLARED[source] = (text, names)
    return names


def _discover(template_path: Path) -> Mapping[str, Path]:
    """Return the skeleton of each zone the page body and its layouts declare."""
    found: dict[str, Path] = {}
    page_dir = template_path.parent
    sources = (
        page_dir / PAGE_BODY,
        page_dir / PAGE_MODULE,
        *layout_chain(template_path),
    )
    for source in sources:
        for zone in declared_zones(source):
            skeleton = skeleton_path(source.parent, zone)
            if zone not in found and skeleton.is_file():
                found[zone] = skeleton
    return found


def zone_skeleton(template_path: Path, zone: str) -> Path | None:
    """Return the reload skeleton of `zone` on the page of `template_path`, if any.

    The walk is held until the page registry reloads, unless edits are watched.
    """
    if template_edits_watched():
        return _discover(template_path).get(zone)
    version = module_version()
    held = _SKELETONS.get(template_path)
    if held is None or held[0] != version:
        held = (version, _discover(template_path))
        _SKELETONS[template_path] = held
    return held[1].get(zone)


def render_skeleton(source: Path, context: Context) -> str:
    """Render the skeleton at `source` over the call-site `context` of its zone.

    A deferred call inside renders in place, as nothing assembles a skeleton.
    """
    data = cast("dict[str, object]", context.flatten())
    data[TEMPLATE_PATH_KEY] = str(source)
    with loading_content():
        return boundary_template(source).render(
            Context(
                data,
                autoescape=context.autoescape,
                use_l10n=context.use_l10n,
                use_tz=context.use_tz,
            )
        )


__all__ = [
    "PAGE_BODY",
    "SKELETON_GLOB",
    "SKELETON_PREFIX",
    "SKELETON_SUFFIX",
    "declared_zones",
    "render_skeleton",
    "skeleton_path",
    "skeleton_zone",
    "zone_skeleton",
]
