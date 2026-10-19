from collections.abc import Generator
from dataclasses import dataclass
from pathlib import Path

import pytest
from django.test import override_settings

from next.components import ComponentInfo, ComponentModuleImportError
from next.components.renderers import _granted
from next.permissions import UnknownAccessObjectError, add_perm
from tests.components.calls import CALLS
from tests.pages.checks.metadata.trees import (
    COMPONENTS,
    component_template,
    layout_root,
    templated_page,
)
from tests.support import routed
from tests.support.access import client_as


_READ = "notes.read_account"

_HEADER = """
from next.components import component
from tests.components.calls import CALLS

"""

_CONTEXTS = """

@component.context("account")
def account():
    CALLS.append("account")
    return "acme"


@component.context("title")
def title():
    CALLS.append("title")
    return "T"
"""

_SPREAD = """

@component.context
def spread():
    CALLS.append("spread")
    return {"account": "acme"}
"""

_LOGIN = "component.login_required()\n"
_MODEL_PERM = 'component.permission_required("auth.view_user")\n'
_OBJECT_PERM = f'component.permission_required("{_READ}", obj="account")\n'
_TEMPLATE = "<b>{{ account }}{{ title }}</b>"


@dataclass(frozen=True, slots=True)
class _Declared:
    """A component declaration, the tag that renders it and what one user sees."""

    id: str
    module: str
    kind: str
    shown: bool
    calls: tuple[str, ...]
    tag: str = '{% component "card" %}'
    allowed: str = "acme"


_DECLARED = (
    _Declared("login-anonymous", _LOGIN + _CONTEXTS, "anonymous", False, ()),
    _Declared(
        "login-visitor", _LOGIN + _CONTEXTS, "visitor", True, ("account", "title")
    ),
    _Declared("model-perm-refused", _MODEL_PERM + _CONTEXTS, "visitor", False, ()),
    _Declared(
        "model-perm-superuser",
        _MODEL_PERM + _CONTEXTS,
        "superuser",
        True,
        ("account", "title"),
    ),
    _Declared(
        "object-from-context",
        _OBJECT_PERM + _CONTEXTS,
        "visitor",
        True,
        ("account", "title"),
    ),
    _Declared(
        "object-from-context-refused",
        _OBJECT_PERM + _CONTEXTS,
        "visitor",
        False,
        ("account",),
        allowed="other",
    ),
    _Declared(
        "object-from-prop",
        _OBJECT_PERM,
        "visitor",
        True,
        (),
        tag='{% component "card" account="acme" %}',
    ),
    _Declared(
        "object-from-prop-refused",
        _OBJECT_PERM,
        "visitor",
        False,
        (),
        tag='{% component "card" account="beta" %}',
    ),
    _Declared("object-anonymous", _OBJECT_PERM + _CONTEXTS, "anonymous", False, ()),
    _Declared(
        "object-from-keyless-context",
        _OBJECT_PERM + _SPREAD,
        "visitor",
        True,
        ("spread",),
    ),
    _Declared(
        "object-from-keyless-context-refused",
        _OBJECT_PERM + _SPREAD,
        "visitor",
        False,
        ("spread",),
        allowed="other",
    ),
)


@pytest.fixture(autouse=True)
def _calls() -> Generator[None, None, None]:
    CALLS.clear()
    yield
    CALLS.clear()


def _tree(tmp_path: Path, module: str, tag: str, template: str | None = _TEMPLATE):
    pages = layout_root(tmp_path)
    folder = pages / "_components" / "card"
    if template is None:
        folder.mkdir(parents=True)
    else:
        component_template(pages, "card", template)
    (folder / "component.py").write_text(_HEADER + module)
    templated_page(pages / "home", "x = 1\n", f"[{tag}]")
    return pages


def _render(pages: Path, kind: str) -> str:
    with routed(pages, COMPONENT_BACKENDS=COMPONENTS):
        response = client_as(kind).get("/home/")
    assert response.status_code == 200
    return response.content.decode()


@pytest.mark.django_db()
@pytest.mark.usefixtures("object_permission_backend")
class TestComponentDeclarations:
    """A refused component renders nothing and runs no context past its object."""

    @pytest.mark.parametrize("case", _DECLARED, ids=lambda case: case.id)
    def test_render(self, tmp_path: Path, isolated_permissions, case) -> None:
        add_perm(_READ, lambda _user, obj: obj == case.allowed)
        pages = _tree(tmp_path, case.module, case.tag)
        body = _render(pages, case.kind)
        assert ("<b>" in body) is case.shown
        assert ("[]" in body) is not case.shown
        assert tuple(CALLS) == case.calls

    def test_an_object_of_none_refuses_without_asking(
        self, tmp_path: Path, isolated_permissions
    ) -> None:
        asked: list[object] = []
        add_perm(_READ, lambda _user, obj: asked.append(obj) is None)
        pages = _tree(tmp_path, _OBJECT_PERM, '{% component "card" account=None %}')
        body = _render(pages, "visitor")
        assert "[]" in body
        assert asked == []

    def test_an_unknown_object_name_is_a_wiring_error(
        self, tmp_path: Path, isolated_permissions
    ) -> None:
        add_perm(_READ, lambda _user, _obj: True)
        pages = _tree(tmp_path, _OBJECT_PERM, '{% component "card" %}')
        with pytest.raises(UnknownAccessObjectError):
            _render(pages, "visitor")

    def test_a_keyless_context_without_the_name_is_a_wiring_error(
        self, tmp_path: Path, isolated_permissions
    ) -> None:
        add_perm(_READ, lambda _user, _obj: True)
        module = _OBJECT_PERM + _SPREAD.replace('"account"', '"other"')
        pages = _tree(tmp_path, module, '{% component "card" %}')
        with pytest.raises(UnknownAccessObjectError):
            _render(pages, "visitor")

    def test_a_row_object_bound_to_a_component_is_a_wiring_error(
        self, tmp_path: Path, isolated_permissions
    ) -> None:
        add_perm(_READ, lambda _user, _obj: True)
        module = (
            "from django.contrib.auth.models import Group\n\n"
            "from next.deps import DObject\n"
            "from next.pages import page\n\n"
            f'page.permission_required("{_READ}", obj=DObject[Group])\n'
        )
        pages = _tree(tmp_path, module, '{% component "card" %}')
        with pytest.raises(TypeError, match="a component has no URL"):
            _render(pages, "visitor")


_RENDERED = '\n\ndef render():\n    return "RENDERED"\n'
_BOOM = 'raise RuntimeError("boom")\n'


@pytest.mark.django_db()
class TestOtherRenderPaths:
    """A `render()` answers the declaration, and a failed import renders nothing."""

    @pytest.mark.parametrize(
        ("kind", "shown"), [("anonymous", False), ("visitor", True)]
    )
    def test_render_function(self, tmp_path: Path, kind: str, shown) -> None:
        pages = _tree(tmp_path, _LOGIN + _RENDERED, '{% component "card" %}', None)
        assert ("RENDERED" in _render(pages, kind)) is shown

    @pytest.mark.usefixtures("object_permission_backend")
    @pytest.mark.parametrize(("account", "shown"), [("acme", True), ("beta", False)])
    def test_render_function_checks_its_object(
        self, tmp_path: Path, isolated_permissions, account: str, *, shown: bool
    ) -> None:
        asked: list[object] = []
        add_perm(_READ, lambda _user, obj: asked.append(obj) is None and obj == "acme")
        tag = f'{{% component "card" account="{account}" %}}'
        pages = _tree(tmp_path, _OBJECT_PERM + _RENDERED, tag, None)
        assert ("RENDERED" in _render(pages, "visitor")) is shown
        assert asked == [account]

    @pytest.mark.parametrize("kind", ["anonymous", "visitor"])
    @pytest.mark.parametrize(
        "module",
        [
            pytest.param(_LOGIN + _BOOM, id="fails-after-declaring"),
            pytest.param(_BOOM + _LOGIN, id="fails-before-declaring"),
        ],
    )
    def test_a_failed_import_renders_nothing(
        self, tmp_path: Path, module: str, kind: str
    ) -> None:
        pages = _tree(tmp_path, module, '{% component "card" %}', "<b>card</b>")
        assert "<b>card</b>" not in _render(pages, kind)

    def test_failing_loudly_raises_the_import_error(self, tmp_path: Path) -> None:
        pages = _tree(tmp_path, _BOOM, '{% component "card" %}', "<b>card</b>")
        with (
            override_settings(DEBUG=True),
            pytest.raises(ComponentModuleImportError, match=r"component\.py") as raised,
        ):
            _render(pages, "visitor")
        assert isinstance(raised.value.__cause__, RuntimeError)


def test_a_component_without_a_module_is_granted(tmp_path: Path) -> None:
    info = ComponentInfo(
        name="card",
        scope_root=tmp_path,
        scope_relative="",
        template_path=tmp_path / "card.djx",
        module_path=None,
        is_simple=True,
    )
    assert _granted(info, {}, None) is True
