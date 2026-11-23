from pathlib import Path
from unittest import mock

import pytest

from next.suspense.manager import SuspenseManager
from next.suspense.markers import Capacity
from next.testing import NextClient, envelope_of
from tests.support import routed, write_page
from tests.support.access import PAGE_HEAD


pytestmark = pytest.mark.django_db

_SHEET = '{% zone "sheet" %}<form>sheet</form>{% endzone %}'
_REFUSES = PAGE_HEAD + "page.router(prefetch=False)\n"
_RENDERS = "def render(request, **kwargs):\n    return '<p>dynamic</p>'\n"


def _prefetch(root: Path, url: str = "/sheet/", depth: str = "full") -> object:
    with routed(root):
        return NextClient().get_zones(url, "sheet", headers={"X-Next-Prefetch": depth})


class TestLayerPrefetch:
    """A layer prefetch answers what the layer would, unless its page opts out."""

    @pytest.mark.parametrize("depth", ["1", "full"])
    def test_a_prefetch_answers_the_full_zone_an_open_gets(
        self, root: Path, depth: str
    ) -> None:
        write_page(root, "sheet", PAGE_HEAD, body=_SHEET)
        with routed(root):
            opened = NextClient().get_zones("/sheet/", "sheet")
        prefetched = _prefetch(root, depth=depth)
        assert prefetched.status_code == 200
        assert envelope_of(prefetched).ops == envelope_of(opened).ops
        assert "X-Next-Prefetch" in prefetched["Vary"]

    @pytest.mark.parametrize(
        "source", [_REFUSES, _REFUSES + _RENDERS], ids=["static", "resolving"]
    )
    def test_a_page_refusing_prefetch_refuses_its_zone_too(
        self, root: Path, source: str
    ) -> None:
        write_page(root, "sheet", source, body=_SHEET)
        response = _prefetch(root)
        assert response.status_code == 204
        assert response["Cache-Control"] == "private, no-store"

    def test_an_intercept_refusing_prefetch_refuses_the_zone_it_serves(
        self, root: Path
    ) -> None:
        write_page(root, "photo", PAGE_HEAD, body="<p>photo</p>")
        write_page(root, "(.)photo", _REFUSES, body=_SHEET)
        assert _prefetch(root, "/photo/").status_code == 204

    def test_a_refusing_page_still_answers_the_open(self, root: Path) -> None:
        write_page(root, "sheet", _REFUSES, body=_SHEET)
        with routed(root):
            response = NextClient().get_zones("/sheet/", "sheet")
        assert response.status_code == 200

    def test_a_lagging_pool_sheds_a_layer_prefetch(self, root: Path) -> None:
        write_page(root, "sheet", PAGE_HEAD, body=_SHEET)
        lag = Capacity(4, 4, 9, 2.2)
        with mock.patch.object(SuspenseManager, "capacity", return_value=lag):
            response = _prefetch(root)
        assert response.status_code == 429
        assert response["Retry-After"] == "3"
