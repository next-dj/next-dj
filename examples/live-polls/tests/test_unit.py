import json
from pathlib import Path

import pytest
from django.http import Http404
from polls.backends import ViteManifestBackend
from polls.models import Poll
from polls.providers import DPoll

from next.static import default_kinds
from next.testing import resolve_call


pytestmark = pytest.mark.django_db


@pytest.fixture()
def vite_root(tmp_path: Path) -> Path:
    """Stand in for VITE_ROOT during URL resolution tests."""
    return tmp_path


@pytest.fixture()
def asset_path(vite_root: Path) -> Path:
    """Synthesise a co-located Vue file path inside `vite_root`."""
    asset = vite_root / "polls" / "screens" / "polls" / "_widgets" / "poll_chart"
    asset.mkdir(parents=True, exist_ok=True)
    file = asset / "component.vue"
    file.write_text("<template></template>")
    return file


def _backend(options: dict[str, str]) -> ViteManifestBackend:
    """Build a `ViteManifestBackend` from a flat OPTIONS dict."""
    return ViteManifestBackend(
        {"BACKEND": "polls.backends.ViteManifestBackend", "OPTIONS": options}
    )


class TestDevOriginRouting:
    """When DEV_ORIGIN is set, .vue assets resolve to the Vite dev server."""

    @pytest.mark.parametrize(
        ("root_strategy", "expected_url_suffix"),
        [
            ("matching", "polls/screens/polls/_widgets/poll_chart/component.vue"),
            ("unrelated", "component.vue"),
        ],
        ids=["under_vite_root", "outside_vite_root"],
    )
    def test_dev_url_keys_off_root_relationship(
        self,
        root_strategy: str,
        expected_url_suffix: str,
        vite_root: Path,
        asset_path: Path,
        tmp_path: Path,
    ) -> None:
        """The dev URL is `{origin}/{relative}` under VITE_ROOT, `{origin}/{name}` outside."""
        configured_root = vite_root
        if root_strategy == "unrelated":
            configured_root = tmp_path / "other"
            configured_root.mkdir()
        backend = _backend(
            {
                "DEV_ORIGIN": "http://localhost:5173",
                "VITE_ROOT": str(configured_root),
                "MANIFEST_PATH": "",
            }
        )
        url = backend.register_file(asset_path, "component", "vue")
        assert url == f"http://localhost:5173/{expected_url_suffix}"

    def test_non_vue_kind_delegates_to_super(
        self, vite_root: Path, asset_path: Path
    ) -> None:
        backend = _backend(
            {
                "DEV_ORIGIN": "http://localhost:5173",
                "VITE_ROOT": str(vite_root),
                "MANIFEST_PATH": "",
            }
        )
        url = backend.register_file(asset_path, "component", "css")
        assert url is not None


class TestManifestRouting:
    """Without DEV_ORIGIN, the manifest maps source files to hashed bundles."""

    @pytest.mark.parametrize(
        ("root_strategy", "manifest_key", "built_file"),
        [
            (
                "matching",
                "polls/screens/polls/_widgets/poll_chart/component.vue",
                "assets/component-abc123.js",
            ),
            ("unrelated", "component.vue", "assets/component-xyz.js"),
        ],
        ids=["key_is_relative_path", "key_falls_back_to_filename"],
    )
    def test_manifest_hit_returns_static_url(
        self,
        root_strategy: str,
        manifest_key: str,
        built_file: str,
        vite_root: Path,
        asset_path: Path,
        tmp_path: Path,
    ) -> None:
        """Manifest lookup uses the relative path under VITE_ROOT or the filename outside."""
        configured_root = vite_root
        if root_strategy == "unrelated":
            configured_root = tmp_path / "other"
            configured_root.mkdir()
        manifest_path = tmp_path / "manifest.json"
        manifest_path.write_text(json.dumps({manifest_key: {"file": built_file}}))
        backend = _backend(
            {
                "VITE_ROOT": str(configured_root),
                "MANIFEST_PATH": str(manifest_path),
            }
        )
        url = backend.register_file(asset_path, "component", "vue")
        assert f"polls/dist/{built_file}" in url


def _stale_manifest_options(vite_root: Path, tmp_path: Path) -> dict[str, str]:
    """Build options where the manifest exists but does not list the asset."""
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text("{}")
    return {"VITE_ROOT": str(vite_root), "MANIFEST_PATH": str(manifest_path)}


def _missing_manifest_options(vite_root: Path, tmp_path: Path) -> dict[str, str]:
    """Build options where `MANIFEST_PATH` points at a path that does not exist."""
    return {
        "VITE_ROOT": str(vite_root),
        "MANIFEST_PATH": str(tmp_path / "missing.json"),
    }


def _no_options(_vite_root: Path, _tmp_path: Path) -> dict[str, str]:
    """Build the empty-options dict, simulating no DEV_ORIGIN and no MANIFEST_PATH."""
    return {}


class TestRefusalWithoutBuildOrDevServer:
    """Three failure modes raise `RuntimeError` with actionable messages.

    Vue single-file components cannot be parsed by the browser as
    plain modules. The kanban example falls back to staticfiles when
    no manifest exists because raw `.jsx` is at least loadable as
    plain JavaScript. A raw `.vue` file is unrenderable, so the
    backend raises an actionable error rather than misleading the
    user with a 200 that the browser cannot execute.
    """

    @pytest.mark.parametrize(
        ("options_factory", "expected_match"),
        [
            (_stale_manifest_options, "manifest does not contain"),
            (_missing_manifest_options, "manifest is missing"),
            (_no_options, "no Vite manifest and no"),
        ],
        ids=["stale_manifest", "missing_manifest_file", "no_options_at_all"],
    )
    def test_register_file_raises_actionable_runtime_error(
        self,
        options_factory,
        expected_match: str,
        asset_path: Path,
        vite_root: Path,
        tmp_path: Path,
    ) -> None:
        """Each refusal scenario points the operator at the right next step."""
        backend = _backend(options_factory(vite_root, tmp_path))
        with pytest.raises(RuntimeError, match=expected_match):
            backend.register_file(asset_path, "component", "vue")


@pytest.fixture()
def poll(db) -> Poll:
    """Return the seeded demo poll, deduped against the data migration."""
    del db
    poll, _ = Poll.objects.get_or_create(question="Tabs or spaces?")
    return poll


def consume(active: DPoll[Poll]) -> Poll | None:
    """Test fixture function whose `active` parameter the resolver fills."""
    return active


class TestDPollResolution:
    """`DPoll[Poll]` reads the poll from URL kwargs or POST body."""

    def test_resolves_from_url_kwargs(self, poll: Poll) -> None:
        """`DPoll[Poll]` matches when `id` is in URL kwargs."""
        kwargs = resolve_call(consume, url_kwargs={"id": poll.pk})
        assert kwargs["active"] == poll

    def test_falls_back_to_post_poll_id(self, rf, poll: Poll) -> None:
        """`DPoll[Poll]` reads `request.POST['poll_id']` when URL kwargs are empty."""
        request = rf.post("/", data={"poll_id": str(poll.pk)})
        kwargs = resolve_call(consume, request=request)
        assert kwargs["active"] == poll

    def test_returns_none_when_no_source(self) -> None:
        """`DPoll[Poll]` resolves to `None` when no URL kwarg or POST field is provided."""
        kwargs = resolve_call(consume)
        assert kwargs["active"] is None

    def test_raises_404_when_poll_missing(self) -> None:
        """An unknown id raises `Http404` so the dispatcher returns the right page."""
        with pytest.raises(Http404):
            resolve_call(consume, url_kwargs={"id": 99999})


class TestVueKindRegistration:
    """`apps.ready()` wires the `.vue` extension to the scripts slot."""

    def test_vue_kind_registered_for_module_renderer(self) -> None:
        """`default_kinds.register("vue", ...)` survives app loading."""
        assert "vue" in default_kinds
        assert default_kinds.extension("vue") == ".vue"
        assert default_kinds.slot("vue") == "scripts"
        assert default_kinds.renderer("vue") == "render_module_tag"

    def test_vue_extension_round_trips_through_registry(self) -> None:
        """The reverse lookup also resolves so discovery picks up `.vue` files."""
        assert default_kinds.kind_for_extension(".vue") == "vue"
