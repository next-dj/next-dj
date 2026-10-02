"""The system check that a page declaring metadata renders `{% metadata %}` at all."""

from __future__ import annotations

from collections.abc import Callable
from itertools import chain
from pathlib import Path
from typing import TYPE_CHECKING, Final, cast

from django.core.checks import CheckMessage, Tags, Warning as DjangoWarning, register
from django.template import Engine, Template, TemplateDoesNotExist, TemplateSyntaxError
from django.template.base import NodeList
from django.template.loader_tags import IncludeNode

from next.checks import NEXT, SEO
from next.components.nodes import ComponentTagNode
from next.components.sources import get_components_manager
from next.pages.checks.composed import iter_composed_pages
from next.pages.loaders import load_page_module
from next.pages.metadata.nodes import MetadataNode
from next.pages.paths import page_path_info

from .pages import loaded_metadata_pages


if TYPE_CHECKING:
    from next.components.manager import ComponentsManager


_MAX_DEPTH: Final = 8

type _Memo = dict[Path | str, bool | None]
type NodeTest = Callable[[NodeList], bool]


def page_template_path(page_path: Path) -> Path:
    """Return the template a component tag of the page resolves against."""
    return Path(page_path_info(page_path).template_path).resolve()


def renders_itself(page_path: Path) -> bool:
    """Whether the page answers through its own `render()` instead of a template."""
    module, _error = load_page_module(page_path)
    return module is not None and callable(getattr(module, "render", None))


def _component_names(nodelist: NodeList) -> list[str]:
    """Return the name of every `{% component %}` tag the nodes hold."""
    nodes = cast("list[ComponentTagNode]", nodelist.get_nodes_by_type(ComponentTagNode))
    return [node.name for node in nodes]


def _has_metadata_tag(nodelist: NodeList) -> bool:
    return bool(nodelist.get_nodes_by_type(MetadataNode))


class TemplateSearch:
    """Search a composition through the components and includes it reaches.

    What the search cannot resolve answers `None`, sparing a false alarm.
    """

    def __init__(self, test: NodeTest) -> None:
        """Search for the nodes `test` recognises, with an empty memo."""
        self.test = test
        self.memo: _Memo = {}

    def reaches(
        self, nodelist: NodeList, template_path: Path, depth: int = 0
    ) -> bool | None:
        """Tell whether the nodes or anything they reach pass the test."""
        if self.test(nodelist):
            return True
        if depth >= _MAX_DEPTH:
            return None
        manager = get_components_manager()
        includes = cast("list[IncludeNode]", nodelist.get_nodes_by_type(IncludeNode))
        unknown = False
        for found in chain(
            (
                self._component(manager, name, template_path, depth)
                for name in _component_names(nodelist)
            ),
            (self._include(node, template_path, depth) for node in includes),
        ):
            if found:
                return True
            if found is None:
                unknown = True
        return None if unknown else False

    def _include(
        self, node: IncludeNode, template_path: Path, depth: int
    ) -> bool | None:
        """Load an include named by a constant and descend into its template.

        A name computed at render time or one the engine cannot load answers `None`.
        """
        expression = node.template
        name = expression.var
        if expression.filters or not isinstance(name, str):
            return None
        if name in self.memo:
            return self.memo[name]
        self.memo[name] = None
        try:
            included = Engine.get_default().get_template(name)
        except (TemplateDoesNotExist, TemplateSyntaxError):
            return None
        result = self.reaches(included.nodelist, template_path, depth + 1)
        self.memo[name] = result
        return result

    def _component(
        self, manager: ComponentsManager, name: str, template_path: Path, depth: int
    ) -> bool | None:
        """Resolve one component the way the tag does and descend into its template."""
        info = manager.get_component(name, template_path)
        source = None if info is None else manager.template_loader.load_source(info)
        if source is None:
            return None
        if source.path in self.memo:
            return self.memo[source.path]
        self.memo[source.path] = None
        try:
            compiled = Template(source.text)
        except TemplateSyntaxError:
            return None
        result = self.reaches(compiled.nodelist, template_path, depth + 1)
        self.memo[source.path] = result
        return result


@register(Tags.templates, NEXT, SEO)
def check_metadata_tag_rendered(*args, **kwargs) -> list[CheckMessage]:
    """Warn when a page declares metadata its composition never renders (`next.W085`).

    The composed template and every component and include it reaches are searched.
    """
    init_errors, pages = loaded_metadata_pages()
    warnings = list(init_errors)
    declared = {entry.page_path for entry in pages if entry.declared}
    search = TemplateSearch(_has_metadata_tag)
    for page_path, template in iter_composed_pages():
        if page_path not in declared or renders_itself(page_path):
            continue
        if (
            search.reaches(template.nodelist, page_template_path(page_path))
            is not False
        ):
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


__all__ = [
    "TemplateSearch",
    "check_metadata_tag_rendered",
    "page_template_path",
    "renders_itself",
]
