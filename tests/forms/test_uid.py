from unittest.mock import MagicMock

import pytest

from next.forms import redirect_to_origin
from next.forms.uid import _resolved_base_dir, _resolved_base_dirs


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


class TestResolvedBaseDir:
    def test_first_call_populates_cache(self, tmp_path) -> None:
        resolved = _resolved_base_dir(tmp_path)
        assert resolved == tmp_path.resolve()
        assert _resolved_base_dirs[str(tmp_path)] == resolved

    def test_second_call_returns_cached_value(self, tmp_path) -> None:
        first = _resolved_base_dir(tmp_path)
        second = _resolved_base_dir(tmp_path)
        assert second is first
