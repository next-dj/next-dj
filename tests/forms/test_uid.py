from unittest.mock import MagicMock

import pytest

from next.forms import redirect_to_origin


class TestRedirectToOrigin:
    @pytest.mark.parametrize(
        "origin",
        ["/admin/library/book/", "/admin/library/book/?q=wiz"],
        ids=("path", "path_with_query"),
    )
    def test_valid_same_site_path(self, mock_http_request, origin) -> None:
        post = MagicMock()
        post.get.return_value = origin
        request = mock_http_request(method="POST", POST=post)
        response = redirect_to_origin(request)
        assert response.status_code == 302
        assert response.url == origin

    @pytest.mark.parametrize(
        "origin",
        [
            "https://attacker.example.com/x/",
            "//attacker.example.com/x/",
            "javascript:alert(1)",
            "ftp://x/y",
            "",
            "no-leading-slash",
        ],
        ids=("https", "protocol_relative", "javascript", "ftp", "empty", "relative"),
    )
    def test_rejects_open_redirect_attempts(self, mock_http_request, origin) -> None:
        post = MagicMock()
        post.get.return_value = origin
        request = mock_http_request(method="POST", POST=post)
        response = redirect_to_origin(request, fallback="/safe/")
        assert response.url == "/safe/"

    def test_missing_post_attribute_uses_fallback(self, mock_http_request) -> None:
        request = mock_http_request(method="GET")
        delattr(request, "POST") if hasattr(request, "POST") else None
        response = redirect_to_origin(request, fallback="/home/")
        assert response.url == "/home/"

    def test_non_string_origin_uses_fallback(self, mock_http_request) -> None:
        post = MagicMock()
        post.get.return_value = ["list", "value"]
        request = mock_http_request(method="POST", POST=post)
        response = redirect_to_origin(request, fallback="/x/")
        assert response.url == "/x/"
