from __future__ import annotations

from dataclasses import dataclass

from tests.support.helpers import file_router_config_entry


@dataclass(frozen=True, slots=True)
class PageBackendCase:
    """One `PAGE_BACKENDS` entry, pinned to the check ids its mistake earns."""

    id: str
    entry: dict[str, object]
    expected: tuple[str, ...]


def _without(key: str) -> dict[str, object]:
    entry = file_router_config_entry()
    del entry[key]
    return entry


PAGE_BACKEND_CASES: tuple[PageBackendCase, ...] = (
    PageBackendCase("no_backend", _without("BACKEND"), ("next.E003",)),
    PageBackendCase(
        "unimportable_backend",
        {**file_router_config_entry(), "BACKEND": "tests.nonexistent.Router"},
        ("next.E004",),
    ),
    PageBackendCase(
        "not_a_router",
        {**file_router_config_entry(), "BACKEND": "pathlib.Path"},
        ("next.E004",),
    ),
    PageBackendCase("no_dirs", _without("DIRS"), ()),
    PageBackendCase("no_pages_dir", _without("PAGES_DIR"), ("next.E024",)),
    PageBackendCase("no_app_dirs", _without("APP_DIRS"), ("next.E025",)),
    PageBackendCase(
        "app_dirs_not_bool",
        {**file_router_config_entry(), "APP_DIRS": "yes"},
        ("next.E005",),
    ),
    PageBackendCase(
        "options_not_dict",
        {**file_router_config_entry(), "OPTIONS": []},
        ("next.E094",),
    ),
    PageBackendCase(
        "processors_not_list",
        file_router_config_entry(options={"context_processors": "x"}),
        ("next.E095",),
    ),
    PageBackendCase(
        "processors_not_strings",
        file_router_config_entry(options={"context_processors": [1]}),
        ("next.E096",),
    ),
    PageBackendCase(
        "unknown_option",
        file_router_config_entry(options={"loaders": []}),
        ("next.E097",),
    ),
)
