from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class WatchSourcesCase:
    """What the reloader reports for one finder run, spelled relative to the tree.

    `rooted` decides whether the tree is reported as a page root at all.
    """

    rooted: bool = True
    templates: tuple[str, ...] = ()
    layouts: tuple[str, ...] = ()
    components: tuple[str, ...] = ()


# The component folder is the one watch source a page tree lacks, so this row walks it.
TEMPLATE_AND_COMPONENT_SOURCES: WatchSourcesCase = WatchSourcesCase(
    templates=("about/template.djx",), components=("_components/widget/component.py",)
)


@dataclass(frozen=True, slots=True)
class WatchedBackendsCase:
    """One ``PAGE_BACKENDS`` shape the watcher reads its page trees from.

    ``entries`` names shapes built under the test's own `tmp_path`.
    """

    id: str
    entries: tuple[str, ...]


WATCHED_BACKENDS_CASES: tuple[WatchedBackendsCase, ...] = (
    WatchedBackendsCase("no_entry", ()),
    WatchedBackendsCase("not_a_dict", ("not_a_dict",)),
    WatchedBackendsCase("unimportable", ("unimportable",)),
    WatchedBackendsCase("existing_root", ("existing",)),
    WatchedBackendsCase("missing_root", ("missing",)),
    WatchedBackendsCase("app_trees", ("app_dirs",)),
    WatchedBackendsCase("skip_name_entry", ("skipping",)),
    WatchedBackendsCase("existing_and_missing", ("existing", "missing")),
    WatchedBackendsCase("unimportable_and_extra_root", ("unimportable", "extra_root")),
)
