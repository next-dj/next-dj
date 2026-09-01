from pathlib import Path

from next.components import ComponentContextManager, ComponentInfo


def component_info(
    directory: Path,
    *,
    name: str = "widget",
    module: Path | None = None,
    template: str | None = None,
    is_simple: bool = False,
) -> ComponentInfo:
    """Return the `ComponentInfo` a component living in `directory` scans as.

    A `template` writes the `component.djx`, which a test holding the folder
    off disk leaves out.
    """
    template_path = directory / "component.djx"
    if template is not None:
        template_path.write_text(template)
    return ComponentInfo(
        name=name,
        scope_root=directory.parent,
        scope_relative="",
        template_path=template_path,
        module_path=module,
        is_simple=is_simple,
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
