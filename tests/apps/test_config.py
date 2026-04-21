from __future__ import annotations

from django.utils import autoreload
from django.utils.autoreload import autoreload_started

from next.pages.watch import get_pages_directories_for_watch
from next.server import NextStatReloader


class TestNextFrameworkConfig:
    """Tests for NextFrameworkConfig.ready() behavior."""

    def test_ready_patches_stat_reloader(self) -> None:
        """After app load, django.utils.autoreload.StatReloader is NextStatReloader."""
        assert autoreload.StatReloader is NextStatReloader

    def test_autoreload_started_watches_each_pages_directory(
        self, mock_autoreload_sender
    ) -> None:
        """Sending autoreload_started runs watch_dir once per pages dir for page.py globs."""
        autoreload_started.send(sender=mock_autoreload_sender)
        pages_dirs = get_pages_directories_for_watch()
        next_watch_calls = [
            (p, g) for p, g in mock_autoreload_sender.watch_calls if g == "**/page.py"
        ]
        assert len(next_watch_calls) == len(pages_dirs)
        for path in pages_dirs:
            assert any(
                p == path and g == "**/page.py"
                for p, g in mock_autoreload_sender.watch_calls
            )

    def test_autoreload_started_never_registers_djx_globs(
        self, mock_autoreload_sender
    ) -> None:
        """``watch_dir`` patterns from next must not match ``.djx`` (lazy templates)."""
        autoreload_started.send(sender=mock_autoreload_sender)
        for _path, glob in mock_autoreload_sender.watch_calls:
            assert ".djx" not in glob, f"unexpected djx glob: {glob!r}"


class TestAutoreloadInstallIdempotent:
    """`install()` is safe to call repeatedly and logs unknown overrides."""

    def test_second_install_is_noop(self) -> None:
        from next.apps import autoreload as next_autoreload

        before = autoreload.StatReloader
        next_autoreload.install()
        assert autoreload.StatReloader is before

    def test_install_warns_on_incompatible_override(self, caplog) -> None:
        from django.utils.autoreload import StatReloader as DjangoStatReloader

        from next.apps import autoreload as next_autoreload

        original = autoreload.StatReloader

        class Unrelated:
            pass

        try:
            autoreload.StatReloader = Unrelated  # type: ignore[misc]
            with caplog.at_level("WARNING", logger="next.apps.autoreload"):
                next_autoreload.install()
            assert autoreload.StatReloader is Unrelated
            assert any(
                "not a StatReloader subclass" in rec.message for rec in caplog.records
            )
        finally:
            autoreload.StatReloader = original  # type: ignore[misc]
            assert issubclass(autoreload.StatReloader, DjangoStatReloader)

    def test_uninstall_restores_original_and_disconnects(self) -> None:
        """`uninstall()` puts back the previous `StatReloader` and detaches the signal."""
        # Grab the true Django `StatReloader` class from `NextStatReloader.__bases__`
        # because the module attribute has already been monkey-patched by `ready()`.
        real_django_stat_reloader = NextStatReloader.__bases__[0]

        from next.apps import autoreload as next_autoreload

        class Placeholder(real_django_stat_reloader):  # type: ignore[misc,valid-type]
            pass

        original = autoreload.StatReloader
        autoreload.StatReloader = Placeholder  # type: ignore[misc]
        next_autoreload._ORIGINAL_STAT_RELOADER = None
        next_autoreload._WATCHER_CONNECTED = False
        try:
            next_autoreload.install()
            assert autoreload.StatReloader is NextStatReloader
            assert next_autoreload._ORIGINAL_STAT_RELOADER is Placeholder
            next_autoreload.uninstall()
            assert autoreload.StatReloader is Placeholder
            # Calling uninstall() a second time is a no-op (both guards fall through).
            next_autoreload.uninstall()
        finally:
            autoreload.StatReloader = original  # type: ignore[misc]
            next_autoreload._ORIGINAL_STAT_RELOADER = None
            next_autoreload._WATCHER_CONNECTED = False
            next_autoreload.install()


class TestStaticfilesInstall:
    """``next.apps.staticfiles.install`` wires the static files finder."""

    def test_next_static_files_finder_in_finders(self) -> None:
        """``NextStaticFilesFinder`` is present in ``STATICFILES_FINDERS`` after ready()."""
        from django.conf import settings

        finders = getattr(settings, "STATICFILES_FINDERS", [])
        assert "next.static.NextStaticFilesFinder" in finders


class TestTemplatesInstall:
    """``next.apps.templates.install`` adds template tag builtins."""

    def test_template_builtins_include_next_tags(self) -> None:
        """next.templatetags modules are present in ``TEMPLATES[0].OPTIONS.builtins``."""
        from django.conf import settings

        builtins = settings.TEMPLATES[0].get("OPTIONS", {}).get("builtins", [])
        assert "next.templatetags.components" in builtins
        assert "next.templatetags.forms" in builtins
        assert "next.templatetags.next_static" in builtins


class TestComponentsInstall:
    """``next.apps.components.install`` loads component backends on startup."""

    def test_components_manager_backends_loaded(self) -> None:
        """``components_manager._backends`` is populated after ``ready()``."""
        from next.components import components_manager

        assert components_manager._backends is not None
