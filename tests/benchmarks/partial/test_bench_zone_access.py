from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from django.urls import resolve

from tests.benchmarks.factories import build_layout_page
from tests.support import PermissionHolder, partial_meta, plain_get, routed
from tests.support.access import write_proxy


if TYPE_CHECKING:
    from pathlib import Path


_URL = "/seg_0/seg_1/"
_PERM = "auth.view_user"
_ZONE = '{% zone "z" %}<p>SECRET</p>{% endzone %}'
_OPEN = f"<main><h1>head</h1>{_ZONE}</main>"
_BLOCK = (
    f'<main><h1>head</h1>{{% has_perm "{_PERM}" %}}{_ZONE}{{% endhas_perm %}}</main>'
)
_INSIDE = (
    '<main><h1>head</h1>{% zone "z" %}'
    f'{{% has_perm "{_PERM}" %}}<p>SECRET</p>{{% endhas_perm %}}{{% endzone %}}</main>'
)
_GUARDED_LAYOUT = f'proxy.login_required()\nproxy.permission_required("{_PERM}")\n'
_SHAPES = {
    "open": (_OPEN, False),
    "guarded-page": (_OPEN, True),
    "has-perm-inside": (_INSIDE, False),
    "has-perm-block": (_BLOCK, False),
}
_ASKED = {"open": 0, "guarded-page": 1, "has-perm-inside": 1}


def _write_page(root: Path, shape: str) -> None:
    """Write a zoned page under two `layout.djx` levels, the guard `shape` names."""
    template, guarded = _SHAPES[shape]
    build_layout_page(root, layouts=2, template=template)
    if guarded:
        write_proxy(root, _GUARDED_LAYOUT)


class TestBenchZoneAccess:
    """A warm zone GET of a holder, by where the check over the zone sits."""

    @pytest.mark.parametrize("shape", list(_ASKED))
    @pytest.mark.benchmark(group="partial.zone_access")
    def test_zone_get(self, tmp_path: Path, shape: str, benchmark) -> None:
        root = tmp_path / "pages"
        root.mkdir()
        _write_page(root, shape)
        request = plain_get(_URL)
        request.META.update(partial_meta(zones="z"))
        request.user = PermissionHolder(_PERM)
        with routed(root):
            match = resolve(_URL)
            response = match.func(request, **match.kwargs)
            assert response.status_code == 200
            assert b"SECRET" in response.content
            assert len(request.user.asked) == _ASKED[shape]
            benchmark(match.func, request, **match.kwargs)

    @pytest.mark.benchmark(group="partial.zone_access")
    def test_zone_get_refused_by_the_block(self, tmp_path: Path, benchmark) -> None:
        """A zone under a page block, refused with a 400 before any check is asked."""
        root = tmp_path / "pages"
        root.mkdir()
        _write_page(root, "has-perm-block")
        request = plain_get(_URL)
        request.META.update(partial_meta(zones="z"))
        request.user = PermissionHolder(_PERM)
        with routed(root):
            match = resolve(_URL)
            response = match.func(request, **match.kwargs)
            assert response.status_code == 400
            assert b"SECRET" not in response.content
            assert request.user.asked == []
            benchmark(match.func, request, **match.kwargs)
