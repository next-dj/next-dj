import pytest
from django.core.signals import setting_changed

from tests.support.leaks import leaked, snapshot
from tests.support.state import Rule, Unit, framework_state, scan


# Process state on an older idiom, each with why it may stay. The list only shrinks.
KNOWN: dict[tuple[str, str], str] = {
    ("next/apps/autoreload.py", "_state"): "the install state of the reloader swap",
    ("next/caches.py", "_SOURCES"): "the registry of sources itself",
    ("next/caches.py", "PageCache._instances"): "the caches a page count rebounds",
    ("next/checks/common.py", "_RUN_MEMOS"): "the memos one check run drops",
    ("next/deps/loader.py", "_row_loaders"): "one loader per model class",
    ("next/pages/graph/variables.py", "_registry"): "accessors registered at import",
    ("next/pages/loaders.py", "_FAILED_PATHS"): "page loads, stamped by mtime",
    ("next/pages/loaders.py", "_stamps"): "page loads, stamped by mtime",
    ("next/pages/loaders.py", "_generation"): "the counter page memos compare",
    ("next/pages/metadata/fold.py", "_STRATEGIES"): "one entry per metadata class",
    ("next/realtime/tunnel.py", "_LIVE"): "the tunnels running now",
    ("next/seo/discovery.py", "_roots_memo"): "held with the route version it read",
    ("next/seo/views.py", "_cached_sitemap"): "a cached view, held per cache setting",
    ("next/seo/views.py", "_cached_robots"): "a cached view, held per cache setting",
    ("next/server/watcher.py", "_registered_extra_watch_specs"): "watch registry",
    ("next/static/inject.py", "_NO_PARTS"): "a shared empty value nothing writes",
    ("next/static/inject.py", "_TOKENS"): "held with the asset version it read",
    ("next/defer/assembly/jobs.py", "_closing"): "the generators closing now",
    ("next/testing/loaders.py", "_loaded_dirs"): "test helper, reset by its caller",
    ("next/transactions/savepoints.py", "_local"): "per-thread savepoint sessions",
    ("next/urls/backends.py", "_installed_apps"): "held with the app list it read",
    ("next/urls/manager.py", "routed_pages"): "the page count each router reports",
    ("next/urls/manager.py", "urlpatterns"): "the lazy URL list, reset on reload",
    ("next/urls/urlconf.py", "_memo"): "held with the resolver it walked",
}

_ADVICE = (
    "New process state builds on next.caches and names its sources, so a move drops "
    "it. See docs/content/internals/overview.rst."
)


def report(units: list[Unit], known: dict[tuple[str, str], str]) -> list[str]:
    """Name every new unit and every known exception that no longer occurs."""
    seen = {unit.key for unit in units}
    new = [unit.describe() for unit in units if unit.key not in known]
    gone = [
        f"{key} no longer occurs, so delete it from KNOWN in tests/test_state.py."
        for key in sorted(known.keys() - seen)
    ]
    return [*new, *([_ADVICE] if new else []), *gone]


class TestFrameworkState:
    """Every unit of process state in `next/` is a primitive or a known exception."""

    def test_no_state_escapes_the_primitives(self) -> None:
        lines = report(framework_state(), KNOWN)
        assert not lines, "\n".join(lines)


def _rules(source: str) -> list[tuple[str, Rule]]:
    return [(unit.name, unit.rule) for unit in scan("next/x.py", source)]


class TestScan:
    """The scan sees each idiom of process state and passes each primitive."""

    @pytest.mark.parametrize(
        "decorator", ["@functools.cache", "@lru_cache(maxsize=8)", "@cache"]
    )
    def test_a_bare_functools_cache_is_named(self, decorator) -> None:
        source = f"{decorator}\ndef build():\n    return 1\n"
        assert _rules(source) == [("build", Rule.FUNCTOOLS)]

    @pytest.mark.parametrize(
        "decorator", ["@memo(SETTINGS)", "@pure_cache()", "@pure_cache(64)"]
    )
    def test_a_primitive_decorator_passes(self, decorator) -> None:
        assert _rules(f"{decorator}\ndef build():\n    return 1\n") == []

    def test_a_cache_names_its_sources(self) -> None:
        source = "a = BoundedCache()\nb = PageCache(depends=())\nc = LruCache(8)\n"
        assert _rules(source) == [("a", Rule.UNDECLARED), ("c", Rule.UNDECLARED)]

    def test_a_container_counts_once_the_module_writes_it(self) -> None:
        source = (
            "TABLE = {'a': 1}\nseen = set()\nheld: dict[str, int] = {}\n"
            "class Owner:\n    items = []\n"
            "def note(key):\n    seen.add(key)\n    held[key] = 1\n"
            "    Owner.items.append(key)\n"
        )
        assert _rules(source) == [
            ("seen", Rule.CONTAINER),
            ("held", Rule.CONTAINER),
            ("Owner.items", Rule.CONTAINER),
        ]

    def test_a_weak_keyed_memo_lives_with_its_key(self) -> None:
        source = "held = WeakKeyDictionary()\ndef note(key):\n    held[key] = 1\n"
        assert _rules(source) == []

    def test_a_reset_attached_to_a_source_declares_what_it_reaches(self) -> None:
        source = (
            "held = {}\nlater = []\n"
            "def note(key):\n    held[key] = 1\n    later.append(key)\n"
            "def _drop():\n    held.clear()\n"
            "def forget():\n    _drop()\n"
            "SETTINGS.attach(forget)\n"
        )
        assert _rules(source) == [("later", Rule.CONTAINER)]

    def test_an_attached_method_declares_its_object(self) -> None:
        source = (
            "class _Held:\n    def __init__(self):\n        self.value = None\n"
            "    def forget(self):\n        self.value = None\n"
            "_held = _Held()\nFORK.attach(_held.forget)\n"
        )
        assert _rules(source) == []

    def test_a_private_holder_the_module_writes_is_named(self) -> None:
        source = (
            "class _Held:\n    def __init__(self):\n        self.value = None\n"
            "@dataclass(frozen=True)\nclass _Fixed:\n    value: int = 0\n"
            "class Public:\n    def set(self):\n        self.value = 1\n"
            "_held = _Held()\n_fixed = _Fixed()\nshared = Public()\n"
            "def put():\n    _held.value = 1\n"
        )
        assert _rules(source) == [("_held", Rule.HOLDER)]

    def test_a_holder_that_writes_itself_is_named(self) -> None:
        source = (
            "class _Held:\n    def set(self, value):\n        self.value = value\n"
            "_held = _Held()\n"
        )
        assert _rules(source) == [("_held", Rule.HOLDER)]

    def test_a_bare_vars_view_and_a_global_are_named(self) -> None:
        source = (
            "_view = vars(owner)\n_kept = instance_dict(owner)\n"
            "def bump():\n    global count\n    count = 1\n"
        )
        assert _rules(source) == [("_view", Rule.VARS), ("count", Rule.GLOBAL)]

    def test_state_inside_if_and_try_counts(self) -> None:
        source = (
            "if flag:\n    a = []\nelse:\n    b = []\n"
            "try:\n    c = []\nexcept ImportError:\n    d = []\nelse:\n    e = []\n"
            "finally:\n    f = []\n"
            "def fill():\n    for item in (a, b, c, d, e, f):\n        item.append(1)\n"
            "    a.append(1)\n    b.append(1)\n    c.append(1)\n    d.append(1)\n"
            "    e.append(1)\n    f.append(1)\n"
        )
        assert [name for name, _rule in _rules(source)] == list("abcdef")


class TestReport:
    """A failure names the file, the line and what to do, and stale exceptions."""

    def test_a_new_unit_names_file_line_and_advice(self) -> None:
        unit = Unit("next/x.py", 3, "_held", Rule.CONTAINER)
        lines = report([unit], {})
        assert lines[0].startswith("next/x.py:3 `_held` is a module-level container")
        assert lines[1] == _ADVICE

    def test_a_vanished_exception_is_reported(self) -> None:
        key = ("next/x.py", "_held")
        assert report([], {key: "why"}) == [
            f"{key} no longer occurs, so delete it from KNOWN in tests/test_state.py."
        ]


class TestLeakGuard:
    """The leak guard names a test that unwires a source from its signal."""

    def test_a_disconnected_settings_receiver_is_named(self) -> None:
        uid = "next.conf.settings.reload_on_setting_changed"
        before = snapshot()
        # Restored as a list, since connecting again would move it behind the others.
        held = list(setting_changed.receivers)
        setting_changed.disconnect(dispatch_uid=uid)
        try:
            found = leaked(before, snapshot())
        finally:
            setting_changed.receivers = held
            setting_changed.sender_receivers_cache.clear()
        assert found == [f"the {uid} receiver of setting_changed"]
        assert leaked(before, snapshot()) == []
