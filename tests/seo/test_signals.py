from django.test import override_settings

from next.seo import PageTreeSitemapBackend
from next.seo.manager import seo_manager
from next.seo.signals import sitemap_backend_loaded
from next.testing import capture_signals


class TestSitemapBackendLoadedSignal:
    """``sitemap_backend_loaded`` fires once per backend the manager loads."""

    def test_a_load_sends_the_class_the_config_and_the_instance(self) -> None:
        entry = {"BACKEND": "next.seo.PageTreeSitemapBackend", "OPTIONS": {"a": 1}}
        with (
            override_settings(NEXT_FRAMEWORK={"SEO": {"SITEMAP_BACKENDS": [entry]}}),
            capture_signals(sitemap_backend_loaded) as recorder,
        ):
            [backend] = seo_manager.backends
        event = recorder.first_for(sitemap_backend_loaded)
        assert event.sender is PageTreeSitemapBackend
        assert event.kwargs == {"config": entry, "instance": backend}
        assert backend.options == {"a": 1}
