from unittest.mock import patch

import pytest
from django.urls import NoReverseMatch

from next.urls import page_reverse, with_query


class TestPageReverse:
    @pytest.mark.parametrize(
        ("path_template", "kwargs", "expected_name", "expected_reverse_kwargs"),
        [
            ("", {}, "next:page_", None),
            ("login", {}, "next:page_login", None),
            (
                "[str:app_label]/[str:model_name]/[int:pk]/change",
                {"app_label": "library", "model_name": "book", "pk": 1},
                "next:page_str_app_label_str_model_name_int_pk_change",
                {"app_label": "library", "model_name": "book", "pk": 1},
            ),
        ],
        ids=("root", "simple_segment", "bracket_template"),
    )
    def test_translates_template_to_url_name(
        self, path_template, kwargs, expected_name, expected_reverse_kwargs
    ) -> None:
        with patch("next.urls.reverse.reverse", return_value="/x/") as mock_reverse:
            page_reverse(path_template, **kwargs)
            mock_reverse.assert_called_once_with(
                expected_name, kwargs=expected_reverse_kwargs
            )

    def test_custom_namespace(self) -> None:
        with patch("next.urls.reverse.reverse", return_value="/x/") as mock_reverse:
            page_reverse("foo", namespace="admin")
            mock_reverse.assert_called_once_with("admin:page_foo", kwargs=None)

    def test_unknown_template_propagates_no_reverse_match(self) -> None:
        with (
            patch(
                "next.urls.reverse.reverse",
                side_effect=NoReverseMatch("nope"),
            ),
            pytest.raises(NoReverseMatch),
        ):
            page_reverse("[str:missing]", missing="x")


class TestWithQuery:
    @pytest.mark.parametrize(
        ("base", "overrides", "expected"),
        [
            ("/admin/library/book/", {"q": "wiz"}, "/admin/library/book/?q=wiz"),
            ("/x/?q=old&p=1", {"q": "new"}, "/x/?p=1&q=new"),
            ("/x/?q=old&p=1", {"q": None}, "/x/?p=1"),
            ("/x/", {"tag": ["a", "b"]}, "/x/?tag=a&tag=b"),
            ("/x/", {"page": 2}, "/x/?page=2"),
            ("/x/y/?a=1#top", {"a": "2"}, "/x/y/?a=2#top"),
            ("/x/", {"q": "a b&c"}, "/x/?q=a+b%26c"),
        ],
        ids=(
            "append",
            "override",
            "drop_none",
            "list_value",
            "coerce_int",
            "preserve_fragment",
            "urlencode_special",
        ),
    )
    def test_query_string_composition(self, base, overrides, expected) -> None:
        assert with_query(base, **overrides) == expected
