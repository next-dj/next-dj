import logging
from pathlib import Path
from unittest.mock import patch

import pytest
from django.test import Client, override_settings

from next.pages.responses import cookie_varies
from next.scripts.manager import ScriptsManager, TreeScripts, scripts_manager
from next.scripts.registry import ScriptsRegistry
from next.static import StaticCollector
from next.static.errors import StaticAssetNotFoundError
from next.testing import NextClient, envelope_of
from next.utils import PageRoot
from tests.scripts.trees import NO_HEAD_TOKEN, get, names, payload, write_tree
from tests.support import routed, touch_later


NEW_SCRIPTS = "from next.scripts import Script\nscripts = (Script('new', init='1'),)\n"


MARKETING = "1:marketing:1700000000"
CATEGORIES = {"CATEGORIES": ["marketing"]}
SHARED = 'template = "<p>x</p>"\ncache = 60\n'


class TestServerRender:
    """A page no shared cache holds renders the allowed head scripts on the server."""

    def test_an_undecided_visitor_gets_the_necessary_head_scripts(
        self, tmp_path: Path
    ) -> None:
        response = get(write_tree(tmp_path / "pages"))
        html = response.content.decode()
        head = html.split("</head>")[0]
        assert head.index('data-next-script="early"') < head.index(
            'data-next-script="base"'
        )
        assert (
            '<script src="https://cdn.example/base.js" async '
            'data-next-script="base"></script>'
        ) in head
        assert "px.example" not in head
        data = payload(response)
        assert names(data["$scripts"]) == ["pixel", "chat", "ads"]
        assert all("timeout" not in entry for entry in data["$scripts"])
        assert data["$consent"]["decided"] is False
        assert data["$consent"]["granted"] == ["necessary"]
        assert set(data["$consent"]) == {"categories", "cookie", "decided", "granted"}
        assert all("order" not in entry for entry in data["$scripts"])
        assert data["$chunks"] == {"scripts": "/static/next/next.scripts.min.js"}
        assert cookie_varies(response.wsgi_request)

    def test_a_granted_category_renders_its_head_script(self, tmp_path: Path) -> None:
        response = get(
            write_tree(tmp_path / "pages"), cookie=MARKETING, CONSENT=CATEGORIES
        )
        head = response.content.decode().split("</head>")[0]
        assert (
            '<script data-next-script="pixel">window.px=[]</script>\n'
            '<script src="https://px.example/p.js" async '
            'data-next-script="pixel"></script>'
        ) in head
        data = payload(response)
        assert names(data["$scripts"]) == ["chat"]
        assert data["$consent"]["decided"] is True
        assert data["$consent"]["granted"] == ["necessary", "marketing"]

    def test_only_necessary_scripts_never_vary(self, tmp_path: Path) -> None:
        scripts = (
            "from next.scripts import Script\nscripts = (Script('a', init='1'),)\n"
        )
        response = get(write_tree(tmp_path / "pages", scripts=scripts))
        assert not cookie_varies(response.wsgi_request)
        assert "$scripts" not in payload(response)

    def test_true_renders_on_the_server_and_takes_a_shared_page_private(
        self, tmp_path: Path
    ) -> None:
        root = write_tree(tmp_path / "pages", page=SHARED)
        response = get(
            root, cookie=MARKETING, CONSENT={**CATEGORIES, "SERVER_RENDER": True}
        )
        assert 'data-next-script="pixel"' in response.content.decode()
        assert "Cookie" in response["Vary"]
        assert response["Cache-Control"] == "private, max-age=60"


class TestClientRender:
    """A shared page, or `SERVER_RENDER=False`, never follows the consent cookie."""

    def test_a_shared_page_sends_every_gated_script_through_the_manifest(
        self, tmp_path: Path
    ) -> None:
        response = get(write_tree(tmp_path / "pages", page=SHARED), cookie=MARKETING)
        assert response["Cache-Control"] == "public, max-age=60"
        assert "Cookie" not in response["Vary"]
        head = response.content.decode().split("</head>")[0]
        assert 'data-next-script="base"' in head
        assert "px.example" not in head
        data = payload(response)
        assert names(data["$scripts"]) == ["pixel", "chat", "ads"]
        assert data["$consent"]["decided"] is False

    def test_false_renders_consent_on_the_client(self, tmp_path: Path) -> None:
        response = get(
            write_tree(tmp_path / "pages"),
            cookie=MARKETING,
            CONSENT={"SERVER_RENDER": False},
        )
        assert "px.example" not in response.content.decode().split("</head>")[0]
        assert not cookie_varies(response.wsgi_request)


class TestSelection:
    """`auto=False` scripts render only where `{% script %}` names them."""

    def test_the_tag_selects_a_manual_script(self, tmp_path: Path) -> None:
        root = write_tree(
            tmp_path / "pages", page="template = '{% script \"optional\" %}<p>x</p>'\n"
        )
        html = get(root).content.decode()
        assert 'data-next-script="optional"' in html.split("</head>")[0]

    def test_without_the_tag_it_stays_out(self, tmp_path: Path) -> None:
        html = get(write_tree(tmp_path / "pages")).content.decode()
        assert "opt.example" not in html

    def test_an_unknown_name_is_logged_once(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        root = write_tree(
            tmp_path / "pages", page="template = '{% script \"nope\" %}x'\n"
        )
        with routed(root), caplog.at_level(logging.WARNING, "next.scripts.manager"):
            Client().get("/")
            Client().get("/")
        assert caplog.text.count("'nope'") == 1


class TestPlacement:
    """Head scripts go to `{% collect_head %}`, else right before `</head>`."""

    def test_without_the_token_they_close_the_head(self, tmp_path: Path) -> None:
        root = write_tree(tmp_path / "pages", layout=NO_HEAD_TOKEN)
        html = get(root).content.decode()
        head = html.split("</head>")[0]
        assert head.index("<title>") < head.index('data-next-script="early"')
        assert head.index('data-next-script="base"') < head.index('rel="preload"')

    def test_a_tree_without_scripts_adds_nothing(self, tmp_path: Path) -> None:
        response = get(write_tree(tmp_path / "pages", scripts=None))
        html = response.content.decode()
        assert "data-next-script" not in html
        data = payload(response)
        assert not {"$scripts", "$consent"} & set(data)
        assert data["$chunks"] == {"scripts": "/static/next/next.scripts.min.js"}

    def test_a_configured_consent_reaches_a_page_without_scripts(
        self, tmp_path: Path
    ) -> None:
        response = get(write_tree(tmp_path / "pages", scripts=None), CONSENT={})
        data = payload(response)
        assert data["$consent"]["cookie"]["name"] == "next_consent"
        assert "$scripts" not in data
        assert "$chunks" in data


class TestNonce:
    """Every tag the framework writes carries the nonce of the render."""

    def test_each_tag_carries_it(self, tmp_path: Path) -> None:
        root = write_tree(
            tmp_path / "pages",
            page=(
                "template = '{% #use_script %}window.x=1{% /use_script %}"
                '{% use_style "https://cdn.example/a.css" %}<p>x</p>\'\n'
            ),
        )
        with patch("next.static.nonce.request_nonce", return_value="abc123"):
            response = get(root)
        html = response.content.decode()
        tags = [
            tag for tag in html.split("<")[1:] if tag.startswith(("script", "link"))
        ]
        assert tags
        assert all('nonce="abc123"' in tag for tag in tags)
        assert all(
            entry["nonce"] == "abc123" for entry in payload(response)["$scripts"]
        )

    def test_a_nonce_switched_off_carries_none(self, tmp_path: Path) -> None:
        with patch("next.static.nonce.request_nonce", return_value="abc123"):
            response = get(write_tree(tmp_path / "pages"), CSP_NONCE=False)
        assert "nonce=" not in response.content.decode()


def _trees(*roots: Path) -> list[tuple[PageRoot, frozenset[str]]]:
    return [(PageRoot(root, root.name), frozenset()) for root in roots]


class TestDiscovery:
    """Every tree holds its own scripts, read again once a watched source moves."""

    def test_each_tree_renders_its_own_scripts(self, tmp_path: Path) -> None:
        first = write_tree(tmp_path / "a" / "pages")
        second = write_tree(
            tmp_path / "b" / "pages",
            scripts="from next.scripts import Script\n"
            "scripts = (Script('other', init='1'),)\n",
            trail="other",
        )
        html = get(first, "/other/", extra_roots=(second,)).content.decode()
        assert 'data-next-script="other"' in html
        assert 'data-next-script="base"' not in html

    def test_a_watched_process_reads_an_edit(self, tmp_path: Path) -> None:
        root = write_tree(tmp_path / "pages")
        with routed(root), override_settings(DEBUG=True):
            before = Client().get("/").content.decode()
            path = root / "scripts.py"
            touch_later(path, NEW_SCRIPTS)
            after = Client().get("/").content.decode()
            again = Client().get("/").content.decode()
        assert 'data-next-script="new"' in again
        assert 'data-next-script="base"' in before
        assert 'data-next-script="new"' in after
        assert 'data-next-script="base"' not in after

    def test_a_page_outside_every_tree_has_no_scripts(self, tmp_path: Path) -> None:
        manager = ScriptsManager(ScriptsRegistry())
        with patch(
            "next.scripts.manager.routed_page_trees",
            return_value=_trees(tmp_path / "pages"),
        ):
            assert manager.tree(tmp_path / "elsewhere" / "page.py") == TreeScripts()
            assert manager.tree(None) == TreeScripts()
            assert manager.root_of(tmp_path / "elsewhere" / "page.py") is None
        assert manager.registry.roots() == (tmp_path / "pages",)

    def test_a_watched_listing_reads_an_edit_of_every_tree(
        self, tmp_path: Path
    ) -> None:
        root = write_tree(tmp_path / "pages")
        manager = ScriptsManager(ScriptsRegistry())
        with (
            patch("next.scripts.manager.routed_page_trees", return_value=_trees(root)),
            override_settings(DEBUG=True),
        ):
            before = manager.sources()
            path = root / "scripts.py"
            touch_later(path, NEW_SCRIPTS)
            after = manager.sources()
        assert "new" not in [script.name for script in before[0].scripts]
        assert [script.name for script in after[0].scripts] == ["new"]

    def test_no_page_path_reads_no_tree(self, tmp_path: Path) -> None:
        manager = ScriptsManager(ScriptsRegistry())
        with patch(
            "next.scripts.manager.routed_page_trees",
            return_value=_trees(tmp_path / "pages"),
        ):
            assert manager.tree(None) == TreeScripts()
        assert manager.registry.roots() == ()

    def test_a_watched_process_stats_only_the_rendered_tree(
        self, tmp_path: Path
    ) -> None:
        first, second = tmp_path / "a", tmp_path / "b"
        manager = ScriptsManager(ScriptsRegistry())
        with (
            patch(
                "next.scripts.manager.routed_page_trees",
                return_value=_trees(first, second),
            ),
            patch("next.scripts.manager.source_stale", return_value=False) as stale,
            override_settings(DEBUG=True),
        ):
            manager.tree(second / "page.py")
        assert [call.args[1] for call in stale.call_args_list] == [second]

    def test_the_innermost_tree_owns_a_nested_page(self, tmp_path: Path) -> None:
        outer = tmp_path / "pages"
        inner = outer / "nested"
        manager = ScriptsManager(ScriptsRegistry())
        with patch(
            "next.scripts.manager.routed_page_trees", return_value=_trees(outer, inner)
        ):
            manager.sources()
        assert manager.root_of(inner / "page.py") == inner
        assert manager.root_of(inner / "page.py") == inner


class TestSources:
    """A script whose file the storage cannot answer costs its tag, not the page."""

    def test_a_missing_file_drops_the_src(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        scripts = (
            "from next.scripts import Script\n"
            "scripts = (Script('a', src='x/a.js'), Script('b', src='x/b.js', "
            "init='window.b=1'))\n"
        )
        root = write_tree(tmp_path / "pages", scripts=scripts)
        with (
            patch(
                "next.static.backends.StaticFilesBackend.resolve_url",
                side_effect=StaticAssetNotFoundError("x"),
            ),
            caplog.at_level(logging.WARNING, "next.scripts.manager"),
        ):
            html = get(root).content.decode()
        assert 'data-next-script="a"' not in html
        assert '<script data-next-script="b">window.b=1</script>' in html
        assert "'a' names a missing file" in caplog.text

    def test_a_staticfiles_name_resolves_through_the_storage(
        self, tmp_path: Path
    ) -> None:
        scripts = (
            "from next.scripts import Script\nscripts = (Script('a', src='x/a.js'),)\n"
        )
        html = get(write_tree(tmp_path / "pages", scripts=scripts)).content.decode()
        assert '<script src="/static/x/a.js" async data-next-script="a">' in html


CONSENTED_ZONE = (
    'template = \'{% zone "media" %}'
    '{% #consented "marketing" %}<video>{% /consented %}'
    "{% endzone %}'\n"
)


class TestConsentedRuntime:
    """Markup the runtime reveals always reaches a page able to load the chunk."""

    @pytest.mark.parametrize(
        ("cache", "framework"),
        [("cache = 60\n", {}), ("", {"CONSENT": {"SERVER_RENDER": False}})],
        ids=["shared", "client-mode"],
    )
    def test_a_client_rendered_block_carries_consent_and_the_chunk(
        self, tmp_path: Path, cache: str, framework: dict[str, object]
    ) -> None:
        page = CONSENTED_ZONE + cache
        root = write_tree(tmp_path / "pages", scripts=None, page=page)
        response = get(root, **framework)
        assert "<!--/next-consented-->" in response.content.decode()
        data = payload(response)
        assert data["$consent"]["decided"] is False
        assert data["$chunks"]["scripts"].endswith("next.scripts.min.js")

    def test_a_zone_get_ships_the_template_and_its_marker(self, tmp_path: Path) -> None:
        page = CONSENTED_ZONE + "cache = 60\n"
        root = write_tree(tmp_path / "pages", scripts=None, page=page)
        with routed(root):
            response = NextClient().get_zones("/", "media")
        (morph,) = envelope_of(response).ops
        assert (
            '<template data-next-consented="marketing"><video></template>'
            "<!--/next-consented-->"
        ) in morph["html"]


class TestRenderWithoutPage:
    """A render with no page holds only what its template noted."""

    def test_nothing_noted_adds_nothing(self) -> None:
        assert scripts_manager.render(
            StaticCollector(), page_path=None, request=None, nonce=None
        ) == ("", {})
