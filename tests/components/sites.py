from collections.abc import Callable
from contextlib import AbstractContextManager
from pathlib import Path
from unittest.mock import patch

from django.http import HttpRequest
from django.template import Context, Engine, Template
from django.template.base import UNKNOWN_SOURCE

from next.components import ComponentInfo, components_manager
from next.components.loading import ModuleLoader
from next.defer import stable_key
from next.seeding import COLLECTOR_KEY
from next.static import StaticCollector
from next.static.assets import StaticAsset
from next.suspense.assembly import finish
from next.suspense.components import deferring
from next.suspense.render import Delta
from next.suspense.session import BoundarySession
from tests.support import component_info
from tests.support.boundaries import run_sequentially


BOOM = "boom"
HEAD = "from next.components import component\n"
LOGIN = HEAD + "component.login_required()\n"
OBJECT = (
    HEAD
    + 'component.permission_required("notes.read", obj="account")\n'
    + '@component.context("account")\ndef account():\n    return "acme"\n'
)
TRIP = HEAD + 'component.defer("load", props=("x",))\n'
PROPS = HEAD + 'component.props("x")\n'
FIRST_KEY = stable_key(UNKNOWN_SOURCE, 0)


def boom() -> str:
    raise ValueError(BOOM)


def stamp(request: HttpRequest) -> dict[str, str]:
    return {"stamp": f"fresh:{request.path}"}


def template_engine(
    templates: dict[str, str], *, cached: bool = False, permissions: bool = False
) -> Engine:
    """Return an engine over `templates` with the call tag built in.

    `stamp` is a context processor of it, `cache` a library, `permissions` adds `has_perm`.
    """
    loader: tuple[str, object] = ("django.template.loaders.locmem.Loader", templates)
    if cached:
        loader = ("django.template.loaders.cached.Loader", [loader])
    builtins = ["next.templatetags.components"]
    if permissions:
        builtins.append("next.templatetags.permissions")
    return Engine(
        loaders=[loader],
        builtins=builtins,
        libraries={"cache": "django.templatetags.cache"},
        context_processors=["tests.components.sites.stamp"],
    )


class Member:
    is_authenticated = True
    is_active = True

    def __init__(self, allowed: str = "acme") -> None:
        """Hold the context object the user is permitted on."""
        self.allowed = allowed

    def has_perm(self, perm: str, obj: object = None) -> bool:
        return obj == self.allowed


class Admin:
    is_authenticated = True
    is_active = True

    def has_perm(self, perm: str, obj: object = None) -> bool:
        return True


def info_in(
    root: Path,
    name: str,
    body: str,
    error: str | None = None,
    *,
    loading: str | None = None,
    module: str | Path | None = None,
) -> ComponentInfo:
    """Write the component `name` under `root` and return how it scans.

    A text `module` is written to its `component.py` and loaded, a path is used as is.
    """
    folder = root / name
    folder.mkdir(parents=True)
    module_path = None
    if isinstance(module, str):
        module_path = folder / "component.py"
        module_path.write_text(module)
        ModuleLoader().load(module_path)
    elif module is not None:
        module_path = module
    return component_info(
        folder,
        name=name,
        module=module_path,
        template=body,
        loading=loading,
        error=error,
    )


def resolving(infos: dict[str, ComponentInfo]) -> AbstractContextManager[object]:
    """Resolve a component by its name from `infos`, no scan behind it."""
    return patch.object(
        components_manager,
        "get_component",
        side_effect=lambda name, _path: infos.get(name),
    )


class ComponentSite:
    """Components resolved by name, rendered from template strings, jobs included."""

    def __init__(self, root: Path) -> None:
        """Start with no component under `root`."""
        self.root = root
        self.infos: dict[str, ComponentInfo] = {}
        self.collector = StaticCollector()

    def add(
        self,
        name: str,
        body: str,
        error: str | None = None,
        *,
        loading: str | None = None,
        module: str | Path | None = None,
    ) -> ComponentInfo:
        info = info_in(self.root, name, body, error, loading=loading, module=module)
        self.infos[name] = info
        return info

    def collect(self, info: ComponentInfo, collector: StaticCollector | None) -> None:
        if collector is not None:
            collector.add(StaticAsset(url=f"/{info.name}.css", kind="css"))

    def asset(self, url: str) -> Callable[[], str]:
        def add() -> str:
            self.collector.add(StaticAsset(url=url, kind="css"))
            return ""

        return add

    def context(self, **values: object) -> dict[str, object]:
        return {
            "current_template_path": str(self.root / "page.djx"),
            COLLECTOR_KEY: self.collector,
            "boom": boom,
            **values,
        }

    def resolving(self) -> AbstractContextManager[object]:
        return resolving(self.infos)

    def render(self, source: str, **values: object) -> str:
        return Template(source).render(Context(self.context(**values)))

    def deferred(
        self, source: str, **values: object
    ) -> tuple[str, BoundarySession[Delta]]:
        with deferring() as held:
            shell = self.render(source, **values)
        assert held.session is not None
        return shell, held.session

    def finished(self, source: str, **values: object) -> str:
        shell, session = self.deferred(source, **values)
        return finish(shell, session, self.collector, run_sequentially)

    def styles(self) -> list[str]:
        return [asset.url for asset in self.collector.assets_in_slot("styles")]
