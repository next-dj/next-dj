import pytest
from django.test import override_settings

from next.seo.checks import check_seo_settings
from tests.support import check_ids


DEFAULT = {"BACKEND": "next.seo.PageTreeSitemapBackend", "OPTIONS": {}}


def _check(scope: object) -> list:
    with override_settings(NEXT_FRAMEWORK={"SEO": scope}):
        return check_seo_settings()


class TestSeoSettings:
    """The `SEO` scope names known keys and usable sitemap backends."""

    def test_the_default_list_passes(self) -> None:
        assert _check({"SITEMAP_BACKENDS": [DEFAULT]}) == []
        assert _check({}) == []

    def test_no_scope_or_a_wrong_scope_type_is_left_to_the_settings_check(self) -> None:
        with override_settings(NEXT_FRAMEWORK={}):
            assert check_seo_settings() == []
        assert _check(["x"]) == []
        with override_settings(NEXT_FRAMEWORK="junk"):
            assert check_seo_settings() == []

    def test_an_unknown_key_is_e035(self) -> None:
        messages = _check({"SITEMAPS": []})
        assert check_ids(messages) == ["next.E035"]
        assert "NEXT_FRAMEWORK['SEO'] has unknown keys 'SITEMAPS'" in messages[0].msg

    def test_a_backend_list_of_the_wrong_type_is_e120(self) -> None:
        messages = _check({"SITEMAP_BACKENDS": {"BACKEND": "x"}})
        assert check_ids(messages) == ["next.E120"]
        assert "expected a list of backend entries" in messages[0].msg

    @pytest.mark.parametrize(
        ("entry", "phrase"),
        [
            ("next.seo.PageTreeSitemapBackend", "expected a mapping with BACKEND"),
            ({"BACKEND": "nope.Nope"}, "names no usable sitemap backend"),
            ({"BACKEND": "next.seo.SitemapBackend"}, "names no usable sitemap backend"),
            ({"BACKEND": "next.urls.FileRouterBackend"}, "names no usable"),
            ({"OPTIONS": {}}, "names no usable sitemap backend"),
            ({**DEFAULT, "OPTIONS": []}, "['OPTIONS'] is [], expected a mapping"),
        ],
        ids=[
            "string",
            "unimportable",
            "abstract",
            "wrong-family",
            "no-backend",
            "options-list",
        ],
    )
    def test_an_unusable_entry_is_e120(self, entry, phrase) -> None:
        messages = _check({"SITEMAP_BACKENDS": [DEFAULT, entry]})
        assert check_ids(messages) == ["next.E120"]
        assert "NEXT_FRAMEWORK['SEO']['SITEMAP_BACKENDS'][1]" in messages[0].msg
        assert phrase in messages[0].msg
