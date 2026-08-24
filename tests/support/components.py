from pathlib import Path

from next.components import ComponentContextManager, ComponentInfo


def build_composite_component(
    root: Path, *, name: str = "card", template: str = "<div>{{ title }}</div>"
) -> tuple[ComponentContextManager, ComponentInfo, Path]:
    """Write a composite component under `root` and pair it with a fresh registry.

    The manager is per call, so a test registers context functions of its own.
    """
    root.mkdir(parents=True, exist_ok=True)
    module_path = (root / "component.py").resolve()
    module_path.write_text("# empty\n")
    template_path = root / "component.djx"
    template_path.write_text(template)
    info = ComponentInfo(
        name=name,
        scope_root=root,
        scope_relative="",
        template_path=template_path,
        module_path=module_path,
        is_simple=False,
    )
    return ComponentContextManager(), info, module_path
