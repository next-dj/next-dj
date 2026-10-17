from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from django.contrib.auth.models import Group
from django.core.exceptions import PermissionDenied
from django.urls import resolve

from next.deps.objects import SCOPE_DEPENDENCY
from tests.benchmarks.factories import StandInRows
from tests.support import PermissionHolder, bound_dependency, plain_get, routed
from tests.support.access import write_proxy


if TYPE_CHECKING:
    from collections.abc import Generator
    from pathlib import Path


_GROUP_PK = 7
_URL = f"/seg_0/{_GROUP_PK}/seg_2/"
_LEVELS = ("", "seg_0", "seg_0/[int:group]")
_ACCESS = (
    "proxy.login_required()\n",
    'proxy.permission_required("auth.view_user")\n',
    (
        "from django.contrib.auth.models import Group\n"
        "from next.deps import DObject\n\n"
        'proxy.permission_required("auth.change_user", obj=DObject[Group])\n'
    ),
)
_HELD = ("auth.view_user", "auth.change_user")
_ASKED = {
    "bare": [],
    "plain": [],
    "declared": [("auth.view_user",), ("auth.change_user", _GROUP_PK)],
}


@pytest.fixture()
def stand_in_scope(monkeypatch: pytest.MonkeyPatch) -> Generator[None, None, None]:
    """Answer every `Group` lookup from memory and bind the `scope` dependency."""
    rows = StandInRows(Group(pk=_GROUP_PK, name="bench"))
    monkeypatch.setattr(
        Group._default_manager, "visible_to", rows.visible_to, raising=False
    )
    with bound_dependency(SCOPE_DEPENDENCY, lambda: "tenant"):
        yield


def _write_chain(root: Path, shape: str) -> None:
    """Write a page under three `layout.djx` levels, each with a `proxy.py` or none.

    A `plain` chain writes empty proxies, a `declared` one adds an access line to each.
    """
    for index, trail in enumerate(_LEVELS):
        directory = root / trail
        directory.mkdir(parents=True, exist_ok=True)
        (directory / "layout.djx").write_text(
            f'<div class="l{index}">{{% template %}}</div>'
        )
    leaf = root / "seg_0" / "[int:group]" / "seg_2"
    leaf.mkdir()
    (leaf / "page.py").write_text("x = 1\n")
    (leaf / "template.djx").write_text("<main><h1>bench</h1></main>")
    if shape == "bare":
        return
    for trail, access in zip(_LEVELS, _ACCESS, strict=True):
        write_proxy(root / trail, access if shape == "declared" else "")


def _asked(asked: list[tuple[object, ...]]) -> list[tuple[object, ...]]:
    """Spell each `has_perm` call with the pk of its row, so the rows compare."""
    return [tuple(getattr(item, "pk", item) for item in call) for call in asked]


@pytest.mark.usefixtures("stand_in_scope")
class TestBenchProxyAccess:
    """A warm full render of a page under three `proxy.py` files, by what they hold."""

    @pytest.mark.parametrize("shape", ["bare", "plain", "declared"])
    @pytest.mark.benchmark(group="pages.proxy_access")
    def test_full_render(self, tmp_path: Path, shape: str, benchmark) -> None:
        root = tmp_path / "pages"
        root.mkdir()
        _write_chain(root, shape)
        request = plain_get(_URL)
        request.user = PermissionHolder(*_HELD)
        with routed(root):
            match = resolve(_URL)
            response = match.func(request, **match.kwargs)
            assert response.status_code == 200
            assert _asked(request.user.asked) == _ASKED[shape]
            benchmark(match.func, request, **match.kwargs)

    @pytest.mark.benchmark(group="pages.proxy_access")
    def test_declared_chain_refuses_a_missing_permission(
        self, tmp_path: Path, benchmark
    ) -> None:
        """The 403 of the `obj=` requirement, after its row loads."""
        root = tmp_path / "pages"
        root.mkdir()
        _write_chain(root, "declared")
        request = plain_get(_URL)
        request.user = PermissionHolder("auth.view_user")
        with routed(root):
            match = resolve(_URL)

            def refused() -> None:
                with pytest.raises(PermissionDenied):
                    match.func(request, **match.kwargs)

            refused()
            assert _asked(request.user.asked) == [
                ("auth.view_user",),
                ("auth.change_user", _GROUP_PK),
            ]
            benchmark(refused)
