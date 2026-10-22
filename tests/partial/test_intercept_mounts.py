import pytest

from next.testing import NextClient, envelope_of
from tests.support import installed_apps, write_app, write_page, write_urlconf
from tests.support.access import PAGE_HEAD
from tests.support.cases.intercepts import zone_body


pytestmark = pytest.mark.django_db


@pytest.fixture()
def mounted(tmp_path):
    write_app(tmp_path, "blog", {"pages": ()})
    pages = tmp_path / "blog" / "pages"
    write_page(pages, "feed/photo/[int:pk]", PAGE_HEAD, body="<p>target {{ pk }}</p>")
    write_page(pages, "feed/(.)photo/[int:pk]", PAGE_HEAD, body=zone_body("peek"))
    urlconf = write_urlconf(
        tmp_path,
        "mounts_urls",
        'path("a/", pages("blog", namespace="a"))',
        'path("b/", pages("blog", namespace="b"))',
    )
    with installed_apps(tmp_path, "blog", urlconf=urlconf, BASE_DIR=tmp_path):
        yield


@pytest.mark.usefixtures("mounted")
class TestInterceptMountedTwice:
    """A tree mounted at two prefixes serves its intercept under both."""

    @pytest.mark.parametrize("prefix", ["a", "b"])
    def test_a_zone_get_serves_the_intercept(self, prefix: str) -> None:
        response = NextClient().get_zones(f"/{prefix}/feed/photo/3/", "peek")

        assert response.status_code == 200
        assert envelope_of(response).zone_targets() == ["peek"]

    @pytest.mark.parametrize("prefix", ["a", "b"])
    def test_a_plain_get_serves_the_target(self, prefix: str) -> None:
        response = NextClient().get(f"/{prefix}/feed/photo/3/")

        assert response.content == b"<p>target 3</p>"
