from pathlib import Path
from unittest.mock import MagicMock

import pytest

from next.forms import redirect_to_origin, validated_next_form_page_path
from next.forms.uid import _resolved_base_dir, _resolved_base_dirs, page_path_token


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


class TestPagePathToken:
    def test_inside_base_dir_is_relative(self, settings, tmp_path) -> None:
        settings.BASE_DIR = tmp_path
        page = tmp_path / "app" / "page.py"
        assert page_path_token(str(page)) == str(Path("app") / "page.py")

    def test_outside_base_dir_falls_back_to_raw(self, settings, tmp_path) -> None:
        settings.BASE_DIR = tmp_path / "site"
        outside = tmp_path / "elsewhere" / "page.py"
        assert page_path_token(str(outside)) == str(outside)

    def test_without_base_dir_falls_back_to_raw(self, settings) -> None:
        settings.BASE_DIR = None
        assert page_path_token("/x/page.py") == "/x/page.py"


class TestPostedPagePathToken:
    @staticmethod
    def _post_request(mock_http_request, value: str) -> MagicMock:
        post = MagicMock()
        post.get.return_value = value
        return mock_http_request(method="POST", POST=post)

    def test_relative_token_resolves_against_base_dir(
        self, settings, tmp_path, mock_http_request
    ) -> None:
        settings.BASE_DIR = tmp_path
        page = tmp_path / "app" / "page.py"
        page.parent.mkdir(parents=True)
        page.write_text("")
        request = self._post_request(mock_http_request, str(Path("app") / "page.py"))
        assert validated_next_form_page_path(request) == page.resolve()

    def test_emitted_token_round_trips(
        self, settings, tmp_path, mock_http_request
    ) -> None:
        settings.BASE_DIR = tmp_path
        page = tmp_path / "app" / "page.py"
        page.parent.mkdir(parents=True)
        page.write_text("")
        request = self._post_request(mock_http_request, page_path_token(str(page)))
        assert validated_next_form_page_path(request) == page.resolve()

    def test_relative_token_without_base_dir_is_rejected(
        self, settings, mock_http_request
    ) -> None:
        settings.BASE_DIR = None
        request = self._post_request(mock_http_request, "app/page.py")
        assert validated_next_form_page_path(request) is None

    def test_relative_token_cannot_escape_base_dir(
        self, settings, tmp_path, mock_http_request
    ) -> None:
        settings.BASE_DIR = tmp_path / "site"
        (tmp_path / "site").mkdir()
        outside = tmp_path / "page.py"
        outside.write_text("")
        request = self._post_request(mock_http_request, "../page.py")
        assert validated_next_form_page_path(request) is None


class TestResolvedBaseDir:
    def test_first_call_populates_cache(self, tmp_path) -> None:
        resolved = _resolved_base_dir(tmp_path)
        assert resolved == tmp_path.resolve()
        assert _resolved_base_dirs[str(tmp_path)] == resolved

    def test_second_call_returns_cached_value(self, tmp_path) -> None:
        first = _resolved_base_dir(tmp_path)
        second = _resolved_base_dir(tmp_path)
        assert second is first
