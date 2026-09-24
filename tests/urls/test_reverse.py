import pytest
from django.test import override_settings
from django.urls import NoReverseMatch

from next.testing import override_next_settings
from next.urls import page_reverse, page_reverse_lazy, with_query


NAMESPACED_URLCONF = "tests.urls.urls_namespaced"
CUSTOM_NAMESPACE_URLCONF = "tests.urls.urls_custom_namespace"


@pytest.mark.usefixtures("page_tree")
class TestPageReverse:
    """`page_reverse` answers the path a directory template routes to."""

    @pytest.mark.parametrize(
        ("path_template", "kwargs", "expected"),
        [
            ("", {}, "/"),
            ("login", {}, "/login/"),
            ("items/[int:id]", {"id": 3}, "/items/3/"),
            (
                "admin/[str:app_label]/[str:model_name]/[int:pk]/change",
                {"app_label": "library", "model_name": "book", "pk": 1},
                "/admin/library/book/1/change/",
            ),
        ],
        ids=("root", "simple_segment", "one_parameter", "bracket_template"),
    )
    def test_the_template_reverses_to_its_path(
        self, path_template, kwargs, expected
    ) -> None:
        with override_settings(ROOT_URLCONF=NAMESPACED_URLCONF):
            assert page_reverse(path_template, **kwargs) == expected

    def test_a_custom_namespace_reaches_the_pages_mounted_under_it(self) -> None:
        """The namespace is the mount point, so the prefix lands in the answer."""
        with override_settings(ROOT_URLCONF=CUSTOM_NAMESPACE_URLCONF):
            assert page_reverse("login", namespace="dashboard") == "/dash/login/"

    def test_the_default_namespace_follows_the_application_the_mount_names(
        self,
    ) -> None:
        """The mount carries `next` as its application namespace, so `next:` finds it."""
        with override_settings(ROOT_URLCONF=CUSTOM_NAMESPACE_URLCONF):
            assert page_reverse("login") == "/dash/login/"

    def test_a_template_no_page_routes_raises_no_reverse_match(self) -> None:
        with (
            override_settings(ROOT_URLCONF=NAMESPACED_URLCONF),
            pytest.raises(NoReverseMatch),
        ):
            page_reverse("[str:missing]", missing="x")

    def test_a_wrong_parameter_type_raises_no_reverse_match(self) -> None:
        """`[int:id]` refuses a non-numeric value, so the miss comes from the converter."""
        with (
            override_settings(ROOT_URLCONF=NAMESPACED_URLCONF),
            pytest.raises(NoReverseMatch),
        ):
            page_reverse("items/[int:id]", id="three")

    def test_the_url_name_template_setting_reaches_the_reverse(self) -> None:
        """A project renaming its page routes keeps reversing them through the helper."""
        with (
            override_next_settings(URL_NAME_TEMPLATE="next-{name}"),
            override_settings(ROOT_URLCONF=NAMESPACED_URLCONF),
        ):
            assert page_reverse("login") == "/login/"


class TestWithQuery:
    @pytest.mark.parametrize(
        ("base", "overrides", "expected"),
        [
            ("/admin/library/book/", {"q": "wiz"}, "/admin/library/book/?q=wiz"),
            ("/x/?q=old&p=1", {"q": "new"}, "/x/?p=1&q=new"),
            ("/x/?q=old&p=1", {"q": None}, "/x/?p=1"),
            ("/x/", {"tag": ["a", "b"]}, "/x/?tag=a&tag=b"),
            ("/x/?tag=a&tag=b", {"tag": []}, "/x/"),
            ("/x/?tag=a&tag=b", {"tag": ()}, "/x/"),
            ("/x/", {"page": 2}, "/x/?page=2"),
            ("/x/y/?a=1#top", {"a": "2"}, "/x/y/?a=2#top"),
            ("/x/", {"q": "a b&c"}, "/x/?q=a+b%26c"),
        ],
        ids=(
            "append",
            "override",
            "drop_none",
            "list_value",
            "drop_empty_list",
            "drop_empty_tuple",
            "coerce_int",
            "preserve_fragment",
            "urlencode_special",
        ),
    )
    def test_query_string_composition(self, base, overrides, expected) -> None:
        assert with_query(base, **overrides) == expected


class TestPageReverseLazy:
    """The lazy variant reverses at read time, not where the value is written."""

    def test_the_value_reverses_against_the_urlconf_installed_when_it_is_read(
        self, page_tree
    ) -> None:
        value = page_reverse_lazy("login")

        with override_settings(ROOT_URLCONF=NAMESPACED_URLCONF):
            assert str(value) == "/login/"

    def test_building_the_value_reverses_nothing(self) -> None:
        """A name no urlconf carries costs nothing until something reads the value."""
        value = page_reverse_lazy("nowhere-at-all")

        with pytest.raises(NoReverseMatch):
            str(value)

    def test_url_parameters_travel_to_the_deferred_reverse(self, page_tree) -> None:
        value = page_reverse_lazy("items/[int:id]", id=7)

        with override_settings(ROOT_URLCONF=NAMESPACED_URLCONF):
            assert str(value) == "/items/7/"
