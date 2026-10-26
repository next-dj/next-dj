from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from django.core.exceptions import PermissionDenied
from django.urls import resolve

from next.pages.access import forget_page_access, refusal
from tests.benchmarks.factories import STAND_IN_PK
from tests.support import PermissionHolder, QuietHolder, plain_get, routed, write_page
from tests.support.access import PAGE_HEAD, write_guard


if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path


_URL = f"/seg_0/{STAND_IN_PK}/seg_2/"
_LEVELS = ("", "seg_0", "seg_0/[int:group]")
_ACCESS = (
    "page.login_required(inherit=True)\n",
    'page.permission_required("auth.view_user", inherit=True)\n',
    (
        "from django.contrib.auth.models import Group\n"
        "from next.deps import DObject\n\n"
        "page.permission_required(\n"
        '    "auth.change_user", obj=DObject[Group], inherit=True\n'
        ")\n"
    ),
)
_HELD = ("auth.view_user", "auth.change_user")
_ASKED = {
    "bare": [],
    "plain": [],
    "declared": [("auth.view_user",), ("auth.change_user", STAND_IN_PK)],
}


def _write_chain(root: Path, shape: str) -> None:
    """Write a page under three `layout.djx` levels, each with a guarding `page.py`.

    A `plain` chain declares nothing in them, a `declared` one an inherited line each.
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
        write_guard(root / trail, access if shape == "declared" else "")


def _asked(asked: list[tuple[object, ...]]) -> list[tuple[object, ...]]:
    """Spell each `has_perm` call with the pk of its row, so the rows compare."""
    return [tuple(getattr(item, "pk", item) for item in call) for call in asked]


def _refused(view: Callable[..., object], request: object, kwargs: dict) -> bool:
    """Return whether `view` refuses `request` with a 403, as a timed call."""
    try:
        view(request, **kwargs)
    except PermissionDenied:
        return True
    return False


@pytest.mark.usefixtures("stand_in_scope")
class TestBenchPageAccess:
    """A full render of a page under three `page.py` files, by what they hold."""

    @pytest.mark.walltime()
    @pytest.mark.parametrize("shape", ["bare", "plain", "declared"])
    @pytest.mark.benchmark(group="pages.page_access")
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
            request.user = QuietHolder(*_HELD)
            benchmark(match.func, request, **match.kwargs)

    @pytest.mark.walltime()
    @pytest.mark.parametrize("shape", ["plain", "declared"])
    @pytest.mark.benchmark(group="pages.page_access")
    def test_full_render_after_the_rules_drop(
        self, tmp_path: Path, shape: str, benchmark
    ) -> None:
        """The first render after a reload, which reads the `page.py` chain again."""
        root = tmp_path / "pages"
        root.mkdir()
        _write_chain(root, shape)
        request = plain_get(_URL)
        request.user = QuietHolder(*_HELD)
        with routed(root):
            match = resolve(_URL)
            assert match.func(request, **match.kwargs).status_code == 200

            def forget() -> tuple[tuple[object, ...], dict[str, object]]:
                forget_page_access()
                return (request,), match.kwargs

            benchmark.pedantic(match.func, setup=forget, rounds=200, iterations=1)

    @pytest.mark.benchmark(group="pages.page_access")
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
            assert _refused(match.func, request, match.kwargs) is True
            assert _asked(request.user.asked) == [
                ("auth.view_user",),
                ("auth.change_user", STAND_IN_PK),
            ]
            request.user = QuietHolder("auth.view_user")
            assert benchmark(_refused, match.func, request, match.kwargs) is True


_PHOTO_URL = "/photo/3/"
_INTERCEPT_RULE = 'page.permission_required("auth.view_user")\n'
_TARGET_RULE = 'page.permission_required("auth.add_user")\n'


def _refused_file(file_path: Path, request: object) -> bool:
    """Return whether the page at `file_path` refuses `request` with a 403."""
    try:
        refusal(file_path, request, {"pk": 3}, {})
    except PermissionDenied:
        return True
    return False


def _write_photo_site(root: Path) -> tuple[Path, Path]:
    """Write a guarded target and the guarded intercept over it, returned in order."""
    target = write_page(
        root, "photo/[int:pk]", f"{PAGE_HEAD}{_TARGET_RULE}", body="<p>x</p>"
    )
    intercept = write_page(
        root, "(.)photo/[int:pk]", f"{PAGE_HEAD}{_INTERCEPT_RULE}", body="<p>y</p>"
    )
    return target, intercept


class TestBenchInterceptRefusal:
    """The refusal an intercept page owes a request, against its target's."""

    @pytest.mark.benchmark(group="pages.page_access")
    def test_intercept_refusal_allowed(self, tmp_path: Path, benchmark) -> None:
        _target, intercept = _write_photo_site(tmp_path)
        request = plain_get(_PHOTO_URL)
        request.user = PermissionHolder("auth.view_user", "auth.add_user")
        with routed(tmp_path):
            assert refusal(intercept, request, {"pk": 3}, {}) is None
            assert _asked(request.user.asked) == [("auth.view_user",)]
            request.user = QuietHolder("auth.view_user", "auth.add_user")
            benchmark(refusal, intercept, request, {"pk": 3}, {})

    @pytest.mark.benchmark(group="pages.page_access")
    def test_target_refusal_allowed(self, tmp_path: Path, benchmark) -> None:
        target, _intercept = _write_photo_site(tmp_path)
        request = plain_get(_PHOTO_URL)
        request.user = PermissionHolder("auth.view_user", "auth.add_user")
        with routed(tmp_path):
            assert refusal(target, request, {"pk": 3}, {}) is None
            assert _asked(request.user.asked) == [("auth.add_user",)]
            request.user = QuietHolder("auth.view_user", "auth.add_user")
            benchmark(refusal, target, request, {"pk": 3}, {})

    @pytest.mark.benchmark(group="pages.page_access")
    def test_intercept_refusal_denied(self, tmp_path: Path, benchmark) -> None:
        _target, intercept = _write_photo_site(tmp_path)
        request = plain_get(_PHOTO_URL)
        request.user = PermissionHolder("auth.add_user")
        with routed(tmp_path):
            assert _refused_file(intercept, request) is True
            request.user = QuietHolder("auth.add_user")
            assert benchmark(_refused_file, intercept, request) is True
