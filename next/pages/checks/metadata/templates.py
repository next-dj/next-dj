"""The system check that a page declaring metadata renders `{% metadata %}` somewhere.

The id is `next.W085`.
"""

from __future__ import annotations

from itertools import chain
from pathlib import Path
from typing import TYPE_CHECKING, Final, cast

from django.core.checks import CheckMessage, Tags, Warning as DjangoWarning, register
from django.template import Engine, Template, TemplateDoesNotExist, TemplateSyntaxError
from django.template.loader_tags import IncludeNode

from next.checks import NEXT
from next.components.sources import get_components_manager
from next.pages.checks.composed import iter_composed_pages
from next.pages.loaders import load_page_module
from next.pages.metadata.nodes import MetadataNode
from next.pages.paths import page_path_info
from next.ports import component_tags_slot

from .pages import loaded_metadata_pages


if TYPE_CHECKING:
    from django.template.base import NodeList

    from next.components.manager import ComponentsManager


_MAX_DEPTH: Final = 8

type _Memo = dict[Path | str, bool | None]


def _renders_itself(page_path: Path) -> bool:
    """Whether the page answers through its own `render()` instead of a template."""
    module, _error = load_page_module(page_path)
    return module is not None and callable(getattr(module, "render", None))


def _renders_metadata(
    nodelist: NodeList, template_path: Path, memo: _Memo, depth: int
) -> bool | None:
    """Tell whether the nodes render `{% metadata %}`, or `None` when unknowable.

    What the check cannot resolve answers `None`, sparing a false alarm.
    """
    if nodelist.get_nodes_by_type(MetadataNode):
        return True
    if depth >= _MAX_DEPTH:
        return None
    manager = get_components_manager()
    includes = cast("list[IncludeNode]", nodelist.get_nodes_by_type(IncludeNode))
    unknown = False
    for found in chain(
        (
            _component_renders_metadata(manager, name, template_path, memo, depth)
            for name in component_tags_slot.get().component_names(nodelist)
        ),
        (
            _include_renders_metadata(node, template_path, memo, depth)
            for node in includes
        ),
    ):
        if found:
            return True
        if found is None:
            unknown = True
    return None if unknown else False


def _include_renders_metadata(
    node: IncludeNode, template_path: Path, memo: _Memo, depth: int
) -> bool | None:
    """Load an include named by a constant and descend into its template.

    A name computed at render time or one the engine cannot load answers `None`.
    """
    expression = node.template
    name = expression.var
    if expression.filters or not isinstance(name, str):
        return None
    if name in memo:
        return memo[name]
    memo[name] = None
    try:
        included = Engine.get_default().get_template(name)
    except (TemplateDoesNotExist, TemplateSyntaxError):
        return None
    result = _renders_metadata(included.nodelist, template_path, memo, depth + 1)
    memo[name] = result
    return result


def _component_renders_metadata(
    manager: ComponentsManager, name: str, template_path: Path, memo: _Memo, depth: int
) -> bool | None:
    """Resolve one component the way the tag does and descend into its template."""
    info = manager.get_component(name, template_path)
    source = None if info is None else manager.template_loader.load_source(info)
    if source is None:
        return None
    if source.path in memo:
        return memo[source.path]
    memo[source.path] = None
    try:
        compiled = Template(source.text)
    except TemplateSyntaxError:
        return None
    result = _renders_metadata(compiled.nodelist, template_path, memo, depth + 1)
    memo[source.path] = result
    return result


@register(Tags.templates, NEXT)
def check_metadata_tag_rendered(*args, **kwargs) -> list[CheckMessage]:
    """Warn when a page declares metadata its composition never renders (`next.W085`).

    The composed template and every component and include it reaches are searched.
    """
    init_errors, pages = loaded_metadata_pages()
    warnings = list(init_errors)
    declared = {entry.page_path for entry in pages if entry.declared}
    memo: _Memo = {}
    for page_path, template in iter_composed_pages():
        if page_path not in declared or _renders_itself(page_path):
            continue
        template_path = Path(page_path_info(page_path).template_path).resolve()
        if _renders_metadata(template.nodelist, template_path, memo, 0) is not False:
            continue
        warnings.append(
            DjangoWarning(
                f"{page_path} declares metadata, but its composed template "
                "renders no {% metadata %}, so the title and the head tags never "
                "reach the page. Add {% metadata %} to the <head> of a layout.djx "
                "in its chain.",
                obj=str(page_path),
                id="next.W085",
            )
        )
    return warnings


__all__ = ["check_metadata_tag_rendered"]
