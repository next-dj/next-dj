from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class WatchSourcesCase:
    """What the reloader reports for one finder run, spelled relative to the tree.

    `rooted` decides whether the tree is reported as a page root at all, which is what
    tells a path under no page tree from one the finder can name.
    """

    rooted: bool = True
    templates: tuple[str, ...] = ()
    layouts: tuple[str, ...] = ()
    components: tuple[str, ...] = ()


# The component folder is the one watch source a page tree does not carry on its own,
# so this row is what makes the finder walk its component branch at all.
TEMPLATE_AND_COMPONENT_SOURCES: WatchSourcesCase = WatchSourcesCase(
    templates=("about/template.djx",), components=("_components/widget/component.py",)
)


@dataclass(frozen=True, slots=True)
class WatchedBackendsCase:
    """One ``PAGE_BACKENDS`` shape the watcher reads its page trees from.

    ``entries`` names the entry shapes a test builds under its own ``tmp_path``,
    because a configured tree only exists once a test has a directory to make.
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
