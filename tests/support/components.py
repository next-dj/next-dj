from collections.abc import Iterable, Mapping
from pathlib import Path
from unittest.mock import patch

from django.http import HttpRequest

from next.components import ComponentContextManager, ComponentInfo, render_component
from next.components.info import BoundaryFiles


CALLS: list[str] = []
"""The context callables a written `component.py` ran, in the order they ran."""


def components_config(root: Path) -> dict[str, object]:
    """Build a `FileComponentsBackend` config rooted at `root`."""
    return {"DIRS": [str(root)], "COMPONENTS_DIR": "_components"}


def write_colocated_component_assets(sources: Iterable[Path]) -> None:
    """Write a `component.css` beside each component source the caller names.

    A page-tree fixture builds only template and layout assets.
    """
    for source in sources:
        folder = source.parent
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "component.css").write_text("")


def _written(target: Path, text: str | None) -> Path | None:
    """Write `text` to `target` and return it, `None` when there is no text."""
    if text is None:
        return None
    target.write_text(text)
    return target


def component_info(
    directory: Path,
    *,
    name: str = "widget",
    module: Path | None = None,
    template: str | None = None,
    is_simple: bool = False,
    loading: str | None = None,
    error: str | None = None,
    files: BoundaryFiles | None = None,
) -> ComponentInfo:
    """Return the `ComponentInfo` a component living in `directory` scans as.

    A `template` writes the `component.djx` a test with no folder on disk leaves out.
    A `loading` or `error` text writes that file and attaches it, `files` attaches ready ones.
    """
    template_path = directory / "component.djx"
    if template is not None:
        template_path.write_text(template)
    if files is None and (loading is not None or error is not None):
        files = BoundaryFiles(
            _written(directory / "loading.djx", loading),
            _written(directory / "error.djx", error),
        )
    return ComponentInfo(
        name=name,
        scope_root=directory.parent,
        scope_relative="",
        template_path=template_path,
        module_path=module,
        is_simple=is_simple,
        files=files,
    )


def build_composite_component(
    root: Path, *, name: str = "card", template: str = "<div>{{ title }}</div>"
) -> tuple[ComponentContextManager, ComponentInfo, Path]:
    """Write a composite component under `root` and pair it with a fresh registry.

    The manager is per call, so a test registers context functions of its own.
    """
    root.mkdir(parents=True, exist_ok=True)
    module_path = (root / "component.py").resolve()
    module_path.write_text("# empty\n")
    info = component_info(root, name=name, module=module_path, template=template)
    return ComponentContextManager(), info, module_path


def render_with(
    manager: ComponentContextManager,
    info: ComponentInfo,
    context_data: Mapping[str, object],
    request: HttpRequest | None = None,
) -> str:
    """Render `info` the way a page renders it, `manager` holding its context.

    The module loads under the same manager, as its entry records what it resolves.
    """
    with (
        patch("next.components.renderers.component", manager),
        patch("next.components.loading.component", manager),
    ):
        return render_component(info, context_data, request)


def write_component(
    root: Path,
    name: str,
    *,
    module: str | None = None,
    body: str = "<p>{{ n }} {{ waited }}</p>",
    loading: str | None = "<p>loading</p>",
    error: str | None = None,
    css: bool = False,
) -> Path:
    """Write the files of component `name` under the `_components` folder of `root`."""
    folder = root / "_components" / name
    folder.mkdir(parents=True)
    (folder / "component.djx").write_text(body)
    if css:
        (folder / "component.css").write_text(f".{name} {{}}")
    for file, text in (
        ("component.py", module),
        ("loading.djx", loading),
        ("error.djx", error),
    ):
        if text is not None:
            (folder / file).write_text(text)
    return folder
