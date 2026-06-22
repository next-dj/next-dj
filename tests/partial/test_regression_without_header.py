import copy

import django
from django.conf import settings
from django.test import Client, override_settings

from next.conf.signals import settings_reloaded
from next.partial import is_partial_request
from next.testing import NextClient


PAGE_BODY = b"<p>test page</p>\n"


def _without_partial_backends() -> dict[str, object]:
    """Return the framework settings with the partial backends emptied."""
    framework = copy.deepcopy(dict(settings.NEXT_FRAMEWORK))
    framework["PARTIAL_BACKENDS"] = []
    return framework


def _invalid_form_post(client: NextClient):
    """Post an empty required field to the regression form, no partial switch."""
    return client.post_action("regression_form", {"name": ""}, origin="/")


class TestPageGetWithoutPartialSwitch:
    """A page GET without the partial switch keeps its byte-for-byte response."""

    def test_body_and_status_unchanged(self) -> None:
        response = Client().get("/")
        assert response.status_code == 200
        assert response.content == PAGE_BODY

    def test_request_is_not_partial(self) -> None:
        response = Client().get("/")
        assert is_partial_request(response.wsgi_request) is False

    def test_content_type_and_vary_unchanged(self) -> None:
        response = Client().get("/")
        assert response["Content-Type"] == "text/html; charset=utf-8"
        assert response["Vary"] == "Cookie"
        assert "X-Next-Request" not in response.get("Vary", "")

    def test_no_partial_response_headers_leak(self) -> None:
        response = Client().get("/")
        assert "X-Next-Version" not in response
        assert response["Content-Type"] != "application/vnd.next.patches+json"

    def test_identical_when_partial_backends_emptied(self) -> None:
        baseline = Client().get("/")
        with override_settings(NEXT_FRAMEWORK=_without_partial_backends()):
            settings_reloaded.send(sender=self.__class__)
            without = Client().get("/")
        settings_reloaded.send(sender=self.__class__)
        assert without.content == baseline.content
        assert without.status_code == baseline.status_code
        assert without["Content-Type"] == baseline["Content-Type"]


class TestInvalidFormPostWithoutPartialSwitch:
    """An invalid form POST without the switch keeps its full-page rerender."""

    def test_status_and_invalid_headers(self, next_client: NextClient) -> None:
        response = _invalid_form_post(next_client)
        assert response.status_code == 200
        assert response["X-Next-Form"] == "invalid"
        assert response["X-Next-Action"]

    def test_request_is_not_partial(self, next_client: NextClient) -> None:
        response = _invalid_form_post(next_client)
        assert is_partial_request(response.wsgi_request) is False

    def test_full_page_body_with_errors(self, next_client: NextClient) -> None:
        response = _invalid_form_post(next_client)
        body = response.content.decode()
        assert "test page" in body
        assert "This field is required." in body
        if django.VERSION >= (5, 0):
            assert 'aria-invalid="true"' in body

    def test_content_type_is_html_not_envelope(self, next_client: NextClient) -> None:
        response = _invalid_form_post(next_client)
        assert response["Content-Type"] == "text/html; charset=utf-8"
        assert response["Content-Type"] != "application/vnd.next.patches+json"

    def test_bytes_identical_when_partial_backends_emptied(
        self, next_client: NextClient
    ) -> None:
        baseline = _invalid_form_post(next_client)
        with override_settings(NEXT_FRAMEWORK=_without_partial_backends()):
            settings_reloaded.send(sender=self.__class__)
            without = _invalid_form_post(next_client)
        settings_reloaded.send(sender=self.__class__)
        assert without.content == baseline.content
        assert without.status_code == baseline.status_code
        assert without["X-Next-Form"] == baseline["X-Next-Form"]
        assert without["X-Next-Action"] == baseline["X-Next-Action"]
