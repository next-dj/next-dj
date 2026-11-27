from collections.abc import Callable, Generator
from unittest.mock import patch

import pytest
from django.http import HttpRequest
from django.test import RequestFactory

from tests.components.sites import ComponentSite


@pytest.fixture()
def site(site: ComponentSite) -> Generator[ComponentSite, None, None]:
    """Give each component the `/<name>.css` asset a real scan finds beside it."""
    with patch("next.static.ports.collect_component_assets", site.collect):
        yield site


@pytest.fixture()
def request_for(rf: RequestFactory) -> Callable[[object], HttpRequest]:
    """Return a builder of the GET request a given user sent."""

    def build(user: object) -> HttpRequest:
        request = rf.get("/")
        request.user = user
        return request

    return build
