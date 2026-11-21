import sys
from collections.abc import Iterator
from pathlib import Path
from unittest import mock

import pytest
from django.http import HttpRequest
from django.test import override_settings
from django.test.utils import override_script_prefix

from next.pages.loaders import forget_layout_texts, load_python_module_memo
from next.pages.navigation import router_declarations
from next.router import checks as router_checks
from next.router.checks import (
    check_exclude_prefixes,
    check_inline_scripts,
    check_layout_page_reads,
    check_layout_slots,
    check_no_store_pages,
    check_root_layout_paths,
    check_router_declarations,
    check_router_seams,
    check_router_settings,
    check_speculation,
)
from next.testing import override_next_settings
from tests.support import patch_checks_router_manager, write_page


_ROOT_LAYOUT = (
    "<html><head>{% metadata %}</head><body>"
    '<nav class="{{ active_tab }}">{{ brand }} {{ user }} {{ csrf_token }}</nav>'
    '{% zone "flash" %}{{ notice }}{% endzone %}{% template %}</body></html>'
)
_SECTION_LAYOUT = (
    "<section>{{ section }}{% #template %}{{ hint }}{% /template %}</section>"
)
_HOME = """
from next.pages import context


@context("brand", inherit_context=True)
def brand() -> str:
    return "b"


@context("active_tab")
def active_tab() -> str:
    return "home"

template = "home"
"""
_SECTION = """
from next.pages import context


@context("section", inherit_context=True)
def section() -> str:
    return "ops"


@context("active_tab")
def active_tab() -> str:
    return "ops"


@context("notice")
def notice() -> str:
    return "n"

template = "ops"
"""
_ITEM = """
from next.pages import context


@context("hint")
def hint() -> str:
    return "h"


@context("title")
def title() -> str:
    return "t"

template = "item"
"""
_WRAPPING = """
from next.pages import context


@context("active_tab")
def active_tab() -> str:
    return "none"
"""


def path_processor(request: HttpRequest) -> dict[str, object]:
    return {"site_path": request.path, "other": 1}


def plain_processor(_request: HttpRequest) -> dict[str, object]:
    return {"brand": "x"}


_TAGLINE = "Ship it"


def mixed_processor(request: HttpRequest) -> dict[str, object]:
    return {"current_path": request.path, "tagline": _TAGLINE, "who": request.user}


def local_processor(request: HttpRequest) -> dict[str, object]:
    here = request.get_full_path()
    crumbs = here.split("/")
    label: str
    label = _TAGLINE.upper()
    size = (width := len(_TAGLINE))
    return {
        "crumbs": crumbs,
        "label": label,
        "width": width + size,
        "count": len(sys.path),
    }


def opaque_processor(request: HttpRequest) -> dict[str, object]:
    first, *_rest = request.path.split("/")
    return {
        "first": first,
        "nav": _nav(request),
        "host": request.get_host(),
        "match": request.resolver_match,
        "level": len(_TAGLINE),
        **_spread(),
    }


def varargs_processor(*args: HttpRequest) -> dict[str, object]:
    return {"where": args[0].path, "made": _made().path, "fixed": 1}


def bare_processor() -> dict[str, object]:
    return {"fixed": _TAGLINE}


def _nav(request: HttpRequest) -> str:
    return request.path


def _spread() -> dict[str, object]:
    return {}


def _made() -> HttpRequest:
    return HttpRequest()


@pytest.fixture()
def router_on() -> Iterator[None]:
    with override_next_settings(ROUTER={"ENABLED": True}):
        yield


def _write(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    return path


class TestSettings:
    """`next.E236` names every key and value outside the `ROUTER` shape."""

    def test_a_clean_scope(self) -> None:
        with override_next_settings(ROUTER={"ENABLED": False}):
            assert check_router_settings() == []

    def test_a_bool_current_is_legal(self) -> None:
        with override_next_settings(ROUTER={"CURRENT": False}):
            assert check_router_settings() == []

    def test_no_scope(self) -> None:
        with override_next_settings(ROUTER="on"):
            assert check_router_settings() == []
            assert check_router_seams() == []

    def test_misfits(self) -> None:
        with override_next_settings(ROUTER={"BOGUS": 1, "SCOPE": "some"}):
            messages = check_router_settings()
        assert [message.id for message in messages] == ["next.E236", "next.E236"]
        assert "unknown keys 'BOGUS'" in messages[0].msg
        assert "['SCOPE'] must be 'all' or 'opt-in', got 'some'" in messages[1].msg


class TestSeams:
    """`next.E237` names a seam path that names no usable class."""

    @pytest.mark.parametrize(
        ("scope", "problem"),
        [
            ({"CUT": "missing.module.Cut"}, "does not import"),
            ({"CUT": "next.router.navigation.CLEAR"}, "names no class"),
            ({"DIGEST": "next.router.VerifiedCut"}, "is no subclass of ChromeDigest"),
            ({"CUT": "next.router.NavigationCut"}, "is an abstract class"),
            (
                {"CURRENT": {"BACKEND": "next.router.ChromeDigest"}},
                "is no subclass of CurrentLinks",
            ),
        ],
    )
    def test_an_unusable_path(self, scope: dict[str, object], problem: str) -> None:
        with override_next_settings(ROUTER=scope):
            (message,) = check_router_seams()
        assert message.id == "next.E237"
        assert problem in message.msg

    def test_usable_and_unset_paths(self) -> None:
        scope = {"CUT": "next.router.VerifiedCut", "DIGEST": "", "CURRENT": {}}
        with override_next_settings(ROUTER=scope):
            assert check_router_seams() == []


@pytest.mark.usefixtures("router_on")
class TestSpeculation:
    """`next.E242` names a `SPECULATION` value that emits no rules."""

    @pytest.mark.parametrize(
        "value", [None, 3, "moderate", {"eagerness": "eager"}], ids=repr
    )
    def test_nothing_to_report(self, value: object) -> None:
        with override_next_settings(ROUTER={"SPECULATION": value}):
            assert check_speculation() == []

    def test_no_scope(self) -> None:
        with override_next_settings(ROUTER="on"):
            assert check_speculation() == []

    @pytest.mark.parametrize(
        ("value", "problem"),
        [
            ("prerender", "names the eagerness 'prerender'"),
            ({"eagerness": "eager", "prerender": True}, "asks for prerender"),
            ({"eagerness": "eager", "source": "list"}, "unknown keys 'source'"),
            ({}, "names the eagerness None"),
        ],
        ids=["string", "prerender", "unknown", "empty"],
    )
    def test_a_value_without_rules(self, value: object, problem: str) -> None:
        with override_next_settings(ROUTER={"SPECULATION": value}):
            (message,) = check_speculation()
        assert message.id == "next.E242"
        assert problem in message.msg
        assert "NEXT_FRAMEWORK['ROUTER']['SPECULATION']" in message.msg


@pytest.mark.usefixtures("router_on")
class TestLayoutSlots:
    """`next.E239` names a placeholder the slot comments cannot wrap."""

    def test_a_title_placeholder(self, tmp_path: Path) -> None:
        bad = _write(tmp_path / "layout.djx", "<title>{% template %}</title>")
        good = _write(tmp_path / "a" / "layout.djx", "<main>{% template %}</main>")
        gone = tmp_path / "gone" / "layout.djx"
        with mock.patch.object(
            router_checks, "_layouts", return_value=[bad, good, gone]
        ):
            (message,) = check_layout_slots()
        assert message.id == "next.E239"
        assert "inside a <title>" in message.msg

    def test_off_checks_nothing(self) -> None:
        with override_next_settings(ROUTER={"ENABLED": False}):
            assert check_layout_slots() == []
            assert check_root_layout_paths() == []
            assert check_inline_scripts() == []
            assert check_no_store_pages() == []

    def test_the_layouts_of_the_trees(self) -> None:
        assert isinstance(router_checks._layouts(), list)


@pytest.mark.usefixtures("router_on")
class TestRootLayoutPaths:
    """`next.W176` names a root chrome that reads the path outside its zones."""

    def test_path_reads(self, tmp_path: Path) -> None:
        root = _write(
            tmp_path / "layout.djx",
            "{% comment %}{{ request.path }}{% endcomment %}"
            '{% zone "crumbs" %}{{ request.path }}{% endzone %}'
            "<footer>{{ site_path }} {{ request.get_full_path }}</footer>"
            "{% if brand %}{{ brand }}{% endif %}{% template %}",
        )
        inner = _write(
            tmp_path / "a" / "layout.djx", "{{ request.path }}{% template %}"
        )
        processors = [path_processor, plain_processor, len]
        with (
            mock.patch.object(router_checks, "_layouts", return_value=[root, inner]),
            mock.patch.object(
                router_checks, "context_processors", return_value=processors
            ),
        ):
            (message,) = check_root_layout_paths()
        assert message.id == "next.W176"
        assert "reads site_path, request.get_full_path outside" in message.msg

    def test_a_clean_root(self, tmp_path: Path) -> None:
        root = _write(tmp_path / "layout.djx", "<main>{% template %}</main>")
        with (
            mock.patch.object(router_checks, "_layouts", return_value=[root]),
            mock.patch.object(router_checks, "context_processors", return_value=[]),
        ):
            assert check_root_layout_paths() == []

    def test_an_unreadable_processor_returns_no_key(self) -> None:
        with mock.patch.object(router_checks.inspect, "getsource", side_effect=OSError):
            assert router_checks._path_dependent_keys(path_processor) == frozenset()

    def test_an_unparsable_processor_returns_no_key(self) -> None:
        with mock.patch.object(
            router_checks.inspect, "getsource", return_value="def ("
        ):
            assert router_checks._path_dependent_keys(path_processor) == frozenset()

    @pytest.mark.parametrize(
        ("processor", "keys"),
        [
            (mixed_processor, {"current_path"}),
            (local_processor, {"crumbs"}),
            (opaque_processor, {"first", "nav", "host", "match"}),
            (varargs_processor, {"where", "made"}),
            (bare_processor, set()),
        ],
        ids=["mixed", "locals", "opaque", "varargs", "no_parameter"],
    )
    def test_only_a_key_whose_value_reads_the_path_counts(
        self, processor, keys: set[str]
    ) -> None:
        assert router_checks._path_dependent_keys(processor) == keys

    def test_a_source_without_a_function_counts_every_path_attribute(self) -> None:
        source = "return {'here': request.path, 'there': os.path.sep, 'fixed': 1}\n"
        with mock.patch.object(router_checks.inspect, "getsource", return_value=source):
            assert router_checks._path_dependent_keys(path_processor) == {
                "here",
                "there",
            }

    @pytest.mark.parametrize(
        ("body", "warned"),
        [("{{ tagline }} {{ who }}", False), ("{{ current_path }}", True)],
        ids=["constant", "path"],
    )
    def test_a_constant_of_a_path_processor_is_quiet(
        self, tmp_path: Path, body: str, *, warned: bool
    ) -> None:
        root = _write(tmp_path / "layout.djx", f"<nav>{body}</nav>{{% template %}}")
        with (
            mock.patch.object(router_checks, "_layouts", return_value=[root]),
            mock.patch.object(
                router_checks, "context_processors", return_value=[mixed_processor]
            ),
        ):
            messages = check_root_layout_paths()
        assert [message.id for message in messages] == (["next.W176"] if warned else [])


@pytest.mark.usefixtures("router_on")
class TestLayoutPageReads:
    """`next.W183` names a layout chrome reading a key the pages below it set."""

    def _tree(self, root: Path, home: str = _HOME) -> None:
        (root / "layout.djx").write_text(_ROOT_LAYOUT)
        write_page(root, "", home)
        write_page(root, "ops", _SECTION)
        (root / "ops" / "layout.djx").write_text(_SECTION_LAYOUT)
        write_page(root, "ops/item", _ITEM)
        (root / "ops" / "item" / "layout.djx").write_text("<p>{{ title }}</p>")
        forget_layout_texts()

    def test_a_key_the_pages_set_differently_is_named(self, tmp_path: Path) -> None:
        self._tree(tmp_path)

        with patch_checks_router_manager(pages_directory=tmp_path):
            (message,) = check_layout_page_reads()

        assert message.id == "next.W183"
        assert message.obj == str(tmp_path / "layout.djx")
        assert "reads 'active_tab' outside a zone" in message.msg
        assert f"as {tmp_path / 'page.py'} does for 'active_tab'" in message.msg
        assert "{% zone %}" in message.hint

    def test_shared_keys_zones_and_the_slot_are_quiet(self, tmp_path: Path) -> None:
        self._tree(tmp_path)
        (tmp_path / "layout.djx").write_text(
            _ROOT_LAYOUT.replace("{{ active_tab }}", "")
        )
        forget_layout_texts()

        with patch_checks_router_manager(pages_directory=tmp_path):
            assert check_layout_page_reads() == []

    def test_a_page_py_without_a_body_is_not_read(self, tmp_path: Path) -> None:
        self._tree(tmp_path, home=_WRAPPING)
        (tmp_path / "ops" / "page.py").write_text(
            _SECTION.replace('"active_tab"', '"other"')
        )

        with patch_checks_router_manager(pages_directory=tmp_path):
            assert check_layout_page_reads() == []

    def test_a_layout_off_the_page_path_shares_no_callable(self) -> None:
        assert (
            router_checks._distance(Path("/a/layout.djx"), Path("/b/page.py")) is None
        )

    def test_off_checks_nothing(self) -> None:
        with override_next_settings(ROUTER={"ENABLED": False}):
            assert check_layout_page_reads() == []


@pytest.mark.usefixtures("router_on")
class TestInlineScripts:
    """`next.W177` names a template with an inline script without a nonce."""

    def test_scripts(self, tmp_path: Path) -> None:
        bare = _write(tmp_path / "template.djx", "<script>go()</script>")
        fine = _write(
            tmp_path / "a" / "template.djx",
            '<script nonce="{{ n }}">x</script><script src="/a.js"></script>'
            '<script type="application/json">{}</script>',
        )
        module = _write(
            tmp_path / "b" / "template.djx", '<script type="module">y</script>'
        )
        with mock.patch.object(
            router_checks, "_templates", return_value=[bare, fine, module]
        ):
            messages = check_inline_scripts()
        assert [message.obj for message in messages] == [str(bare), str(module)]
        assert {message.id for message in messages} == {"next.W177"}
        assert "only when its nonce is the response nonce" in str(messages[0].hint)
        assert 'nonce="{{ csp_nonce }}"' in str(messages[0].hint)

    def test_the_templates_of_the_trees(self) -> None:
        assert isinstance(router_checks._templates(), list)


@pytest.mark.usefixtures("router_on")
class TestNoStorePages:
    """`next.W178` names a page whose cache forbids every store."""

    def test_no_store(self, tmp_path: Path) -> None:
        stored = _write(tmp_path / "a" / "page.py", "cache = False\n")
        cached = _write(tmp_path / "b" / "page.py", "cache = 60\n")
        plain = _write(tmp_path / "c" / "page.py", "x = 1\n")
        with mock.patch.object(
            router_checks, "_page_modules", return_value=iter([stored, cached, plain])
        ):
            (message,) = check_no_store_pages()
        assert message.id == "next.W178"
        assert str(stored) in message.msg

    def test_the_pages_of_the_trees(self, tmp_path: Path) -> None:
        _write(tmp_path / "a" / "page.py", "x = 1\n")
        with mock.patch.object(
            router_checks, "get_pages_directories_for_watch", return_value=[tmp_path]
        ):
            assert list(router_checks._page_modules()) == [tmp_path / "a" / "page.py"]


class TestDeclarations:
    """`next.E238` names a `page.router()` call that applies nothing."""

    @pytest.fixture(autouse=True)
    def _fresh(self) -> Iterator[None]:
        router_declarations.reset()
        yield
        router_declarations.reset()

    def test_a_bad_call_and_a_misplaced_one(self, tmp_path: Path) -> None:
        load_python_module_memo(
            _write(
                tmp_path / "a" / "page.py",
                "from next.pages import page\npage.router(prefetch='no')\n",
            )
        )
        load_python_module_memo(
            _write(
                tmp_path / "helpers.py", "from next.pages import page\npage.router()\n"
            )
        )
        messages = check_router_declarations()
        assert [message.id for message in messages] == ["next.E238", "next.E238"]
        assert "prefetch='no' is no bool" in messages[0].msg
        assert "outside a page.py" in messages[1].msg


def _unmatched(*prefixes: str, enabled: bool = True) -> list[str]:
    with override_next_settings(ROUTER={"ENABLED": enabled, "EXCLUDE": prefixes}):
        messages = check_exclude_prefixes()
    assert {message.id for message in messages} <= {"next.W184"}
    return [message.msg.split("'")[5] for message in messages]


class TestExcludePrefixes:
    """`next.W184` names an `EXCLUDE` prefix that starts no path of the URLconf."""

    @pytest.fixture(autouse=True)
    def _urls(self) -> Iterator[None]:
        with override_settings(
            ROOT_URLCONF="tests.router.urls_exclude",
            LANGUAGES=[("en", "English"), ("de", "German")],
            MEDIA_URL="/uploads/",
        ):
            yield

    @pytest.mark.parametrize(
        "prefix",
        [
            "/legacy/",
            "/legacy",
            "/legacy/reports/7/",
            "/about",
            "/old-site/post/",
            "/feed",
            "/col",
            "/color/",
            "/raw/any/depth/",
            "/shop/",
            "/de/shop/",
            "/static/admin/",
            "/uploads/",
        ],
    )
    def test_a_prefix_some_route_serves_is_silent(self, prefix: str) -> None:
        assert _unmatched(prefix) == []

    @pytest.mark.parametrize(
        "prefix",
        ["/legcy/", "/legacy/nope/", "/about/team/", "/feed.json", "/fr/shop/"],
    )
    def test_a_prefix_no_route_serves_warns(self, prefix: str) -> None:
        assert _unmatched(prefix) == [prefix]

    def test_the_message(self) -> None:
        with override_next_settings(ROUTER={"ENABLED": True, "EXCLUDE": ["/legcy/"]}):
            (message,) = check_exclude_prefixes()
        assert message.msg == (
            "NEXT_FRAMEWORK['ROUTER']['EXCLUDE'] holds '/legcy/', which starts no "
            "path the URLconf serves, so it keeps nothing out of the client router."
        )
        assert "Silence next.W184" in str(message.hint)

    def test_a_disabled_router_is_silent(self) -> None:
        assert _unmatched("/legcy/", enabled=False) == []

    def test_no_prefixes_is_silent(self) -> None:
        assert _unmatched() == []

    def test_the_script_prefix_is_read_off_the_prefix(self) -> None:
        with override_script_prefix("/app/"):
            assert _unmatched("/app/legacy/", "/app/legcy/", "/elsewhere/") == [
                "/app/legcy/"
            ]


class TestRegexHead:
    """The literal start of a pattern regex stops where the regex stops being literal."""

    @pytest.mark.parametrize(
        ("regex", "head", "rest"),
        [
            pytest.param(r"^a\.b/\Z", "a.b/", r"\Z", id="escaped"),
            pytest.param(r"^ab?c/$", "a", "b?c/$", id="quantified"),
            pytest.param(r"^a\-?b/$", "a", r"\-?b/$", id="quantified-escape"),
            pytest.param(r"^a/(?P<x>\d+)/$", "a/", r"(?P<x>\d+)/$", id="group"),
            pytest.param(r"^a/\d+/$", "a/", r"\d+/$", id="class-escape"),
            pytest.param(r"^a/$|^b/$", "", r"^a/$|^b/$", id="alternation"),
        ],
    )
    def test_the_head_and_the_rest(self, regex: str, head: str, rest: str) -> None:
        assert router_checks._regex_head(regex) == (head, rest)
