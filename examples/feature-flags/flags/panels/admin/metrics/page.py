from flags.metrics import render_counts
from flags.receivers import access_denied_count, feature_guard_count

from next.pages import context


@context("render_counts")
def page_render_counts() -> dict[str, int]:
    return render_counts()


@context("feature_guard_count")
def page_feature_guard_count() -> int:
    return feature_guard_count()


@context("access_denied_count")
def page_access_denied_count() -> int:
    return access_denied_count()
