from collections.abc import Generator, Iterator
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import MagicMock, patch

from next.components import ComponentInfo, FileComponentsBackend
from next.partial import checks


@contextmanager
def _composed_pages(*pages: tuple[Path, str]) -> Iterator[None]:
    """Point the page-scanning checks at real on-disk page directories.

    Each entry is a `page.py` path and the `template.djx` body next to it.
    The global page instance compiles the body through its layout loader, so
    a zone tag in the body is discovered exactly as it is in production.
    """
    routes: list[tuple[str, Path]] = []
    root = pages[0][0].parent if pages else Path()
    for page_file, body in pages:
        page_file.write_text("x = 1")
        (page_file.parent / "template.djx").write_text(body)
        routes.append((f"/{page_file.parent.name}/", page_file))

    manager = MagicMock()
    router = MagicMock()
    manager._backends = [router]
    router._scan_pages_directory.return_value = routes
    with (
        patch("next.partial.checks.get_router_manager", return_value=(manager, [])),
        patch("next.checks.common.get_pages_directory", return_value=root),
    ):
        yield


def _page_dir(tmp_path: Path, name: str) -> Path:
    directory = tmp_path / name
    directory.mkdir()
    return directory / "page.py"


class TestDuplicateZoneCheck:
    """`next.E060` fires when two zones in one page share a name."""

    def test_duplicate_name_in_one_page(self, tmp_path: Path) -> None:
        page_file = _page_dir(tmp_path, "dup")
        body = (
            '{% zone "side" %}<p>{{ a }}</p>{% endzone %}'
            '{% zone "side" %}<p>{{ b }}</p>{% endzone %}'
        )
        with _composed_pages((page_file, body)):
            ids = [m.id for m in checks.check_duplicate_zone_names()]
        assert ids == [checks.E_DUPLICATE_ZONE]

    def test_distinct_names_are_silent(self, tmp_path: Path) -> None:
        page_file = _page_dir(tmp_path, "ok")
        body = (
            '{% zone "left" %}<p>{{ a }}</p>{% endzone %}'
            '{% zone "right" %}<p>{{ b }}</p>{% endzone %}'
        )
        with _composed_pages((page_file, body)):
            assert checks.check_duplicate_zone_names() == []


class TestZoneSlugCheck:
    """`next.E061` fires when a zone name is not an ASCII slug."""

    def test_non_ascii_name(self, tmp_path: Path) -> None:
        page_file = _page_dir(tmp_path, "slug")
        with _composed_pages(
            (page_file, '{% zone "зона" %}<p>{{ a }}</p>{% endzone %}')
        ):
            ids = [m.id for m in checks.check_zone_name_is_slug()]
        assert ids == [checks.E_NON_ASCII_ZONE]

    def test_ascii_slug_is_silent(self, tmp_path: Path) -> None:
        page_file = _page_dir(tmp_path, "slug_ok")
        with _composed_pages(
            (page_file, '{% zone "side-bar_2" %}<p>{{ a }}</p>{% endzone %}')
        ):
            assert checks.check_zone_name_is_slug() == []


class TestZoneInLoopCheck:
    """`next.E062` fires when a zone sits inside a `{% for %}`."""

    def test_zone_in_for(self, tmp_path: Path) -> None:
        page_file = _page_dir(tmp_path, "loop")
        body = (
            "{% for item in items %}"
            '{% zone "row" %}<p>{{ item }}</p>{% endzone %}'
            "{% endfor %}"
        )
        with _composed_pages((page_file, body)):
            ids = [m.id for m in checks.check_zone_not_in_loop()]
        assert ids == [checks.E_ZONE_IN_FOR]

    def test_zone_outside_loop_is_silent(self, tmp_path: Path) -> None:
        page_file = _page_dir(tmp_path, "no_loop")
        body = '{% zone "row" %}<p>{{ a }}</p>{% endzone %}'
        with _composed_pages((page_file, body)):
            assert checks.check_zone_not_in_loop() == []


class TestZoneInIfCheck:
    """`next.E063` fires when a zone sits inside an `{% if %}`, either branch."""

    def test_zone_in_if_branch(self, tmp_path: Path) -> None:
        page_file = _page_dir(tmp_path, "if_then")
        body = '{% if cond %}{% zone "guard" %}<p>{{ a }}</p>{% endzone %}{% endif %}'
        with _composed_pages((page_file, body)):
            ids = [m.id for m in checks.check_zone_not_in_if()]
        assert ids == [checks.E_ZONE_IN_IF]

    def test_zone_in_else_branch(self, tmp_path: Path) -> None:
        page_file = _page_dir(tmp_path, "if_else")
        body = (
            "{% if cond %}plain"
            '{% else %}{% zone "guard" %}<p>{{ a }}</p>{% endzone %}'
            "{% endif %}"
        )
        with _composed_pages((page_file, body)):
            ids = [m.id for m in checks.check_zone_not_in_if()]
        assert ids == [checks.E_ZONE_IN_IF]

    def test_zone_outside_if_is_silent(self, tmp_path: Path) -> None:
        page_file = _page_dir(tmp_path, "no_if")
        body = '{% zone "guard" %}{% if cond %}<p>{{ a }}</p>{% endif %}{% endzone %}'
        with _composed_pages((page_file, body)):
            assert checks.check_zone_not_in_if() == []


class TestLazyPlaceholderCheck:
    """`next.E064` fires when a lazy zone has no `{% placeholder %}`."""

    def test_lazy_without_placeholder(self, tmp_path: Path) -> None:
        page_file = _page_dir(tmp_path, "lazy_bad")
        body = '{% zone "z" lazy="load" %}<p>{{ a }}</p>{% endzone %}'
        with _composed_pages((page_file, body)):
            ids = [m.id for m in checks.check_lazy_zone_has_placeholder()]
        assert ids == [checks.E_LAZY_WITHOUT_PLACEHOLDER]

    def test_lazy_with_placeholder_is_silent(self, tmp_path: Path) -> None:
        page_file = _page_dir(tmp_path, "lazy_ok")
        body = (
            '{% zone "z" lazy="load" %}<p>{{ a }}</p>'
            "{% placeholder %}<div>loading</div>{% endzone %}"
        )
        with _composed_pages((page_file, body)):
            assert checks.check_lazy_zone_has_placeholder() == []


class TestWithOverZoneCheck:
    """`next.W067` warns when a `{% with %}` wraps a zone directly."""

    def test_with_directly_over_zone(self, tmp_path: Path) -> None:
        page_file = _page_dir(tmp_path, "with_bad")
        body = (
            '{% with greeting="hi" %}'
            '{% zone "z" %}<p>{{ greeting }}</p>{% endzone %}'
            "{% endwith %}"
        )
        with _composed_pages((page_file, body)):
            messages = checks.check_with_directly_over_zone()
        assert [m.id for m in messages] == [checks.W_WITH_OVER_ZONE]

    def test_zone_without_with_is_silent(self, tmp_path: Path) -> None:
        page_file = _page_dir(tmp_path, "with_ok")
        body = '{% zone "z" %}{% with greeting="hi" %}{{ greeting }}{% endwith %}{% endzone %}'
        with _composed_pages((page_file, body)):
            assert checks.check_with_directly_over_zone() == []


@contextmanager
def _component(template_path: Path | None) -> Generator[None, None, None]:
    """Point `next.E065` at a fake components manager with one component."""
    backend = FileComponentsBackend({"DIRS": [], "COMPONENTS_DIR": "_components"})
    if template_path is not None:
        backend._registry.register(
            ComponentInfo(
                name="card",
                scope_root=template_path.parent,
                scope_relative="",
                template_path=template_path,
                module_path=None,
                is_simple=True,
            )
        )
    backend._loaded = True
    manager = MagicMock()
    manager._reload_config = lambda: None
    manager._backends = [backend]

    settings_ns = MagicMock()
    settings_ns.COMPONENT_BACKENDS = [
        {"BACKEND": "next.components.FileComponentsBackend"}
    ]
    with (
        patch("next.partial.checks.next_framework_settings", settings_ns),
        patch("next.partial.checks.ComponentsManager", return_value=manager),
    ):
        yield


class TestZoneInComponentCheck:
    """`next.E065` fires when a component template declares a zone."""

    def test_zone_in_component_template(self, tmp_path: Path) -> None:
        component = tmp_path / "card.djx"
        component.write_text('<div>{% zone "inner" %}x{% endzone %}</div>')
        with _component(component):
            ids = [m.id for m in checks.check_no_zone_in_component()]
        assert ids == [checks.E_ZONE_IN_COMPONENT]

    def test_component_without_zone_is_silent(self, tmp_path: Path) -> None:
        component = tmp_path / "card.djx"
        component.write_text("<div>plain card</div>")
        with _component(component):
            assert checks.check_no_zone_in_component() == []

    def test_no_component_backends_is_silent(self) -> None:
        settings_ns = MagicMock()
        settings_ns.COMPONENT_BACKENDS = []
        with patch("next.partial.checks.next_framework_settings", settings_ns):
            assert checks.check_no_zone_in_component() == []


class TestChecksSilentOnValidComposite:
    """A page with well-formed zones triggers none of the zone checks."""

    def test_all_page_checks_clear(self, tmp_path: Path) -> None:
        page_file = _page_dir(tmp_path, "valid")
        body = (
            '{% zone "header" %}<h1>{{ title }}</h1>{% endzone %}'
            '{% zone "feed" lazy="revealed" %}<ul>{{ items }}</ul>'
            "{% placeholder %}<p>loading</p>{% endzone %}"
        )
        with _composed_pages((page_file, body)):
            assert checks.check_duplicate_zone_names() == []
            assert checks.check_zone_name_is_slug() == []
            assert checks.check_zone_not_in_loop() == []
            assert checks.check_zone_not_in_if() == []
            assert checks.check_lazy_zone_has_placeholder() == []
            assert checks.check_with_directly_over_zone() == []
