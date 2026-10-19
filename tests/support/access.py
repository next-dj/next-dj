from __future__ import annotations

from contextlib import contextmanager
from typing import TYPE_CHECKING, Final

from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.test import override_settings

from next.pages import page
from next.permissions.access import declarations
from next.testing import NextClient
from tests.support.pages import write_page


if TYPE_CHECKING:
    from collections.abc import Generator
    from pathlib import Path

    from django.test import Client


LOGIN_REQUIRED_MIDDLEWARE: Final = (
    "django.contrib.auth.middleware.LoginRequiredMiddleware"
)

PAGE_HEAD: Final = "from next.pages import page\n\n"

CONTEXT_CALLS: list[str] = []


def note_call(name: str) -> str:
    """Record that the context `name` ran and return the name as its value."""
    CONTEXT_CALLS.append(name)
    return name


def declare_login() -> None:
    """Declare a login from this helper module, which binds it to this file."""
    page.login_required()


def write_guard(directory: Path, source: str = "") -> Path:
    """Write a `page.py` with no body into `directory`, running `source` after `page`."""
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / "page.py"
    path.write_text(f"{PAGE_HEAD}{source}")
    return path


def page_source(source: str = "", template: str = "ok") -> str:
    """Return a `page.py` body importing `page`, running `source`, with a template."""
    return f"{PAGE_HEAD}{source}\ntemplate = {template!r}\n"


@contextmanager
def restored_declarations() -> Generator[None, None, None]:
    """Run a block over no access declarations, then declare the earlier ones again."""
    held = {
        path: (declarations.declared(path), declarations.inherited(path))
        for path in declarations.files()
    }
    declarations.reset()
    try:
        yield
    finally:
        declarations.reset()
        for path, (rule, inherited) in held.items():
            for requirement in rule.requirements:
                inherit = requirement in inherited.requirements
                declarations.declare(path, requirement, inherit=inherit)
            if rule.public:
                declarations.declare_public(path, inherit=inherited.public)


def login_required_middleware() -> override_settings:
    """Return an override that appends `LoginRequiredMiddleware` to `MIDDLEWARE`."""
    return override_settings(
        MIDDLEWARE=[*settings.MIDDLEWARE, LOGIN_REQUIRED_MIDDLEWARE]
    )


def login_required_setting() -> override_settings:
    """Return an override that turns `NEXT_FRAMEWORK["LOGIN_REQUIRED"]` on."""
    framework = getattr(settings, "NEXT_FRAMEWORK", {})
    return override_settings(NEXT_FRAMEWORK={**framework, "LOGIN_REQUIRED": True})


def sign_in(client: Client, username: str, *codenames: str) -> object:
    """Create a user holding the model permissions `codenames` and log it in."""
    user = get_user_model().objects.create_user(username)
    for codename in codenames:
        user.user_permissions.add(Permission.objects.get(codename=codename))
    client.force_login(user)
    return user


HOLDER_CODENAME: Final = "view_user"


def client_as(kind: str) -> NextClient:
    """Return a client for `kind`, anonymous, a visitor, a holder or a superuser.

    A holder holds `auth.view_user` and a visitor no permission at all.
    """
    client = NextClient()
    if kind == "superuser":
        user = get_user_model().objects.create_superuser("root", "root@example.com")
        client.force_login(user)
    elif kind == "holder":
        sign_in(client, kind, HOLDER_CODENAME)
    elif kind == "visitor":
        sign_in(client, kind)
    return client


COMPONENTS: Final = [
    {
        "BACKEND": "next.components.FileComponentsBackend",
        "DIRS": [],
        "COMPONENTS_DIR": "_components",
    }
]

_INDEX = """
from next.forms import action
from next.pages import page
from next.testing import NextClient

page.public()
template = '{% component "card" %}{% component "plain" %}{% component "bare" %}'


@action("guarded_ping", login_required=True, permission_required="auth.add_group")
def guarded_ping():
    return None
"""

_NOTES = """
from django.contrib.auth.models import Group

from next.deps import DObject
from next.forms import action
from next.pages import page

page.permission_required("auth.change_group", obj=DObject[Group])
template = "{{ group }}"


@action("save_note")
def save_note():
    return None
"""

_OPEN = """
from next.forms import action

template = "open"


@action("open_ping")
def open_ping():
    return None


@action("login_ping", login_required=True)
def login_ping():
    return None
"""

_CARD = """
from next.components import component

component.login_required()
"""


def write_access_project(root: Path) -> Path:
    """Write a public index with a guarded card, and a guarded, open and broken page."""
    write_page(root, "", _INDEX)
    write_guard(root / "notes", "page.login_required(inherit=True)\n")
    write_page(root, "notes/[int:group]", _NOTES)
    write_page(root, "open", _OPEN)
    write_page(root, "broken", page_source("raise RuntimeError('boom')\n"))
    card = root / "_components" / "card"
    card.mkdir(parents=True)
    (card / "component.py").write_text(_CARD)
    (card / "component.djx").write_text("<b>card</b>")
    for name, module in (("plain", None), ("bare", "x = 1\n")):
        folder = root / "_components" / name
        folder.mkdir()
        (folder / "component.djx").write_text(name)
        if module is not None:
            (folder / "component.py").write_text(module)
    return root
