from collections.abc import Iterable
from dataclasses import dataclass

import pytest

from tests.support.imports import (
    Import,
    Kind,
    framework_imports,
    module_names,
    parse_imports,
    statements,
)


# Bottom to top. A layer is a tuple of tiers, and a tier never imports a tier above it
# at runtime. Areas of one tier may import each other, so foundation stays one tier.
LAYERS: tuple[tuple[str, tuple[frozenset[str], ...]], ...] = (
    (
        "foundation",
        (
            frozenset(
                {
                    "backends",
                    "caches",
                    "checks",
                    "conf",
                    "diagnostics",
                    "discovery",
                    "errors",
                    "identity",
                    "intercept",
                    "interpolation",
                    "introspect",
                    "lifecycle",
                    "manifest",
                    "marks",
                    "paths",
                    "requests",
                    "ports",
                    "seeding",
                    "site",
                    "stamps",
                    "utils",
                    "wire",
                }
            ),
        ),
    ),
    (
        "kernel",
        (
            frozenset({"bridge", "compression", "csrf", "permissions", "transactions"}),
            frozenset({"deps"}),
            frozenset({"boundaries"}),
            frozenset({"components"}),
            frozenset({"pages"}),
            frozenset({"static"}),
            frozenset({"forms"}),
            frozenset({"urls"}),
        ),
    ),
    ("protocol", (frozenset({"partial"}),)),
    (
        "features",
        (frozenset({"consent", "realtime", "router", "scripts", "seo", "defer"}),),
    ),
    (
        "composition",
        (
            frozenset(
                {
                    "apps",
                    "management",
                    "middleware",
                    "next",
                    "server",
                    "signals",
                    "templatetags",
                    "views",
                }
            ),
        ),
    ),
    ("testing", (frozenset({"testing"}),)),
)

# Feature pairs that depend on each other by design, importer first.
FEATURE_PAIRS: frozenset[tuple[str, str]] = frozenset({("scripts", "consent")})

# Imports that break a contract today, as contract, importer and imported module. The
# test fails once one disappears, so the list only ever shrinks.
KNOWN: frozenset[tuple[str, str, str]] = frozenset()

CORE: frozenset[str] = frozenset().union(*dict(LAYERS)["kernel"])
FEATURES: frozenset[str] = frozenset().union(
    *dict(LAYERS)["protocol"], *dict(LAYERS)["features"]
)

_RANK: dict[str, tuple[int, int]] = {
    area: (layer, tier)
    for layer, (_name, tiers) in enumerate(LAYERS)
    for tier, members in enumerate(tiers)
    for area in members
}
_NAMES: dict[int, str] = {layer: name for layer, (name, _tiers) in enumerate(LAYERS)}
_FEATURES_LAYER = next(i for i, (name, _) in enumerate(LAYERS) if name == "features")

_ADVICE = (
    "Move the shared code down to a layer both areas may import, or invert the "
    "dependency through a narrow Protocol slot in next/ports.py bound in ready()."
)


@dataclass(frozen=True, slots=True)
class Violation:
    """One import that breaks a named contract."""

    contract: str
    edge: Import
    reason: str

    @property
    def key(self) -> tuple[str, str, str]:
        return (self.contract, self.edge.source, self.edge.target)

    def describe(self) -> str:
        return (
            f"{self.edge.where} imports {self.edge.target} ({self.edge.kind}), "
            f"breaking the {self.contract} contract: {self.reason}"
        )


def area_of(module: str) -> str:
    """Name the area of a dotted module, the second segment or `next` for the root."""
    parts = module.split(".")
    return parts[1] if len(parts) > 1 else parts[0]


def is_checks(module: str) -> bool:
    """Tell whether a module belongs to the system checks of its area."""
    return "checks" in module.split(".")[1:]


def _where(area: str) -> str:
    layer, _tier = _RANK[area]
    return f"{_NAMES[layer]} area `{area}`"


def layer_violations(edges: Iterable[Import]) -> list[Violation]:
    """Answer every runtime import of code that reaches a higher layer or tier."""
    found = []
    for edge in edges:
        if edge.kind is not Kind.RUNTIME or is_checks(edge.source):
            continue
        source, target = area_of(edge.source), area_of(edge.target)
        if _RANK[source] >= _RANK[target]:
            continue
        contract = "kernel" if _RANK[source][0] == _RANK[target][0] else "layers"
        reason = f"{_where(source)} sits below {_where(target)}."
        found.append(Violation(contract, edge, reason))
    return found


def core_violations(edges: Iterable[Import]) -> list[Violation]:
    """Answer every import, typing included, of a feature area by a kernel area."""
    return [
        Violation(
            "core",
            edge,
            f"{_where(area_of(edge.source))} never knows "
            f"{_where(area_of(edge.target))}, not even for typing.",
        )
        for edge in edges
        if area_of(edge.source) in CORE and area_of(edge.target) in FEATURES
    ]


def independence_violations(edges: Iterable[Import]) -> list[Violation]:
    """Answer every runtime import of one feature by another outside the pairs."""
    features = dict(LAYERS)["features"][0]
    found = []
    for edge in edges:
        source, target = area_of(edge.source), area_of(edge.target)
        if (
            edge.kind is Kind.RUNTIME
            and source != target
            and {source, target} <= features
            and (source, target) not in FEATURE_PAIRS
        ):
            reason = f"features stay apart and `{source}` may not import `{target}`."
            found.append(Violation("independence", edge, reason))
    return found


def checks_violations(edges: Iterable[Import]) -> list[Violation]:
    """Answer every runtime import by a checks module of composition or testing code.

    System checks inspect their neighbours, so a checks module may import any checks
    module and any code up to the feature layer, or up to its own layer when higher.
    """
    found = []
    for edge in edges:
        if edge.kind is not Kind.RUNTIME or not is_checks(edge.source):
            continue
        if is_checks(edge.target):
            continue
        ceiling = max(_RANK[area_of(edge.source)][0], _FEATURES_LAYER)
        target = area_of(edge.target)
        if _RANK[target][0] > ceiling:
            reason = f"a checks module looks no higher than features, not {target}."
            found.append(Violation("checks", edge, reason))
    return found


def report(
    violations: Iterable[Violation], known: frozenset[tuple[str, str, str]]
) -> tuple[list[str], list[str]]:
    """Split violations into new ones and known exceptions that no longer occur."""
    violations = list(violations)
    seen = {violation.key for violation in violations}
    new = [item.describe() for item in violations if item.key not in known]
    gone = [
        f"{key} no longer occurs, so delete it from KNOWN in tests/test_layers.py."
        for key in sorted(known - seen)
    ]
    return new, gone


def _assert_clean(violations: list[Violation], contract: str) -> None:
    known = frozenset(key for key in KNOWN if key[0] == contract)
    new, gone = report(violations, known)
    lines = [*new, *([_ADVICE] if new else []), *gone]
    assert not lines, "\n".join(lines)


def _edge(source: str, target: str, kind: Kind = Kind.RUNTIME) -> Import:
    file = source.replace(".", "/") + ".py"
    return Import(source, target, file, 7, kind)


@pytest.fixture(scope="module")
def edges() -> tuple[Import, ...]:
    return framework_imports()


class TestLayerMap:
    """Every area of the package sits in exactly one layer."""

    def test_every_area_is_placed(self) -> None:
        areas = {area_of(name) for name in module_names()}
        unplaced = sorted(areas - set(_RANK))
        assert unplaced == [], f"place these areas in LAYERS: {unplaced}"

    def test_no_layer_names_a_missing_area(self) -> None:
        areas = {area_of(name) for name in module_names()}
        assert sorted(set(_RANK) - areas) == []

    def test_no_area_sits_in_two_tiers(self) -> None:
        placed = [area for _name, tiers in LAYERS for tier in tiers for area in tier]
        assert len(placed) == len(set(placed))

    def test_every_feature_pair_names_two_features(self) -> None:
        features = dict(LAYERS)["features"][0]
        assert all({a, b} <= features for a, b in FEATURE_PAIRS)

    def test_every_known_exception_names_a_contract(self) -> None:
        contracts = {"layers", "kernel", "core", "independence", "checks"}
        assert {key[0] for key in KNOWN} <= contracts


class TestContracts:
    """The import graph of `next/` keeps the declared layers."""

    def test_lower_layers_never_import_higher_ones(self, edges) -> None:
        found = [item for item in layer_violations(edges) if item.contract == "layers"]
        _assert_clean(found, "layers")

    def test_kernel_tiers_never_import_higher_ones(self, edges) -> None:
        found = [item for item in layer_violations(edges) if item.contract == "kernel"]
        _assert_clean(found, "kernel")

    def test_kernel_never_knows_a_feature(self, edges) -> None:
        _assert_clean(core_violations(edges), "core")

    def test_features_stay_independent(self, edges) -> None:
        _assert_clean(independence_violations(edges), "independence")

    def test_checks_look_no_higher_than_features(self, edges) -> None:
        _assert_clean(checks_violations(edges), "checks")

    def test_the_page_view_names_no_feature(self, edges) -> None:
        """Features reach the page view as lifecycle participants, never by import."""
        features = dict(LAYERS)["features"][0]
        found = [
            edge.where
            for edge in edges
            if edge.source == "next.views.pages" and area_of(edge.target) in features
        ]
        assert found == []

    def test_the_static_area_reads_features_only_through_its_providers(self) -> None:
        """A feature reaches a page head and payload as a provider, never by a slot.

        The layer pass alone stays a slot, read through a fallback that keeps the HTML.
        """
        allowed = {
            "PageScripts",
            "ScriptRender",
            "ScriptShare",
            "StaticAssets",
            "page_scripts",
            "stash_layer_loading",
            "static_assets_slot",
        }
        found = [
            f"{module}:{line} imports {name}"
            for module, name, line, _kind in statements()
            if area_of(module) == "static"
            and name.startswith("next.ports.")
            and name.removeprefix("next.ports.") not in allowed
        ]
        assert found == []

    def test_every_import_runs_at_module_level(self) -> None:
        nested = [
            f"{module} imports {name} inside a function at line {line}, move it up."
            for module, name, line, kind in statements()
            if kind is Kind.DEFERRED
        ]
        assert nested == []


class TestParse:
    """The collector tells runtime, typing and deferred imports apart."""

    def test_kinds_follow_the_statement_position(self) -> None:
        source = (
            "import json\n"
            "from typing import TYPE_CHECKING\n"
            "if TYPE_CHECKING:\n"
            "    from next.pages import page\n"
            "else:\n"
            "    from next.deps import Depends\n"
            "async def load():\n"
            "    import next.forms\n"
        )
        assert parse_imports(source, "next.views.plan") == [
            ("json", 1, Kind.RUNTIME),
            ("typing.TYPE_CHECKING", 2, Kind.RUNTIME),
            ("next.pages.page", 4, Kind.TYPING),
            ("next.deps.Depends", 6, Kind.RUNTIME),
            ("next.forms", 8, Kind.DEFERRED),
        ]

    @pytest.mark.parametrize(
        ("module", "package", "source", "expected"),
        [
            ("next.views.pages", False, "from . import plan", "next.views.plan"),
            ("next.views.pages", False, "from ..pages import page", "next.pages.page"),
            (
                "next.views",
                True,
                "from .plan import PagePlan",
                "next.views.plan.PagePlan",
            ),
        ],
    )
    def test_relative_imports_resolve_from_the_module(
        self, module, package, source, expected
    ) -> None:
        names = parse_imports(source, module, package=package)
        assert [name for name, _line, _kind in names] == [expected]


class TestReport:
    """A failure names the import, the contract it breaks and the stale exceptions."""

    def test_a_new_violation_names_file_line_and_areas(self) -> None:
        edge = _edge("next.deps.loader", "next.views.pages")
        new, gone = report(layer_violations([edge]), frozenset())
        expected = (
            "next/deps/loader.py:7 imports next.views.pages (runtime), breaking the "
            "layers contract: kernel area `deps` sits below composition area `views`."
        )
        assert new == [expected]
        assert gone == []

    def test_a_vanished_exception_is_reported(self) -> None:
        key = ("layers", "next.urls.backends", "next.views")
        new, gone = report([], frozenset({key}))
        assert new == []
        assert gone == [
            f"{key} no longer occurs, so delete it from KNOWN in tests/test_layers.py."
        ]

    def test_a_typing_import_passes_the_layers_but_not_the_core(self) -> None:
        edge = _edge("next.pages.jobs", "next.router.state", Kind.TYPING)
        assert layer_violations([edge]) == []
        assert [item.contract for item in core_violations([edge])] == ["core"]

    def test_a_feature_pair_passes_independence(self) -> None:
        allowed = _edge("next.scripts.consent", "next.consent.state")
        reverse = _edge("next.consent.state", "next.scripts.consent")
        assert [item.edge for item in independence_violations([allowed, reverse])] == [
            reverse
        ]

    def test_a_checks_module_may_look_sideways_but_not_into_composition(self) -> None:
        sideways = _edge("next.deps.checks", "next.forms.registry")
        upward = _edge("next.deps.checks", "next.views.pages")
        assert layer_violations([sideways, upward]) == []
        assert [item.edge for item in checks_violations([sideways, upward])] == [upward]
