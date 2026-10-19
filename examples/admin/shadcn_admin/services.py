from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import datetime

from django.contrib.admin.models import LogEntry
from django.contrib.contenttypes.models import ContentType
from django.db.models import Model

from shadcn_admin.permissions import Visitor


@dataclass(frozen=True, slots=True)
class NavLink:
    """One sidebar link an installed app adds under a section heading."""

    section: str
    label: str
    url: str


@dataclass(frozen=True, slots=True)
class PanelRow:
    """One line of a dashboard panel, a badge, its text and a muted note."""

    badge: str
    text: str
    note: str


@dataclass(frozen=True, slots=True)
class Panel:
    """A dashboard panel an installed app fills, rendered by the `dashboard_panel`."""

    title: str
    rows: tuple[PanelRow, ...]
    empty: str
    link_label: str = ""
    link_url: str = ""


type NavSource = Callable[[Visitor], Iterable[NavLink]]
type PanelSource = Callable[[Visitor], Iterable[Panel]]

_nav_sources: list[NavSource] = []
_panel_sources: list[PanelSource] = []


def register_nav(source: NavSource) -> NavSource:
    """Add `source` to the functions that fill the sidebar, like `admin.register`."""
    _nav_sources.append(source)
    return source


def register_panel(source: PanelSource) -> PanelSource:
    """Add `source` to the functions that fill the dashboard, like `admin.register`."""
    _panel_sources.append(source)
    return source


def admin_nav(user: Visitor) -> tuple[NavLink, ...]:
    """Return the sidebar links the installed apps add for `user`, in order."""
    return tuple(link for source in _nav_sources for link in source(user))


def dashboard_panels(user: Visitor) -> tuple[Panel, ...]:
    """Return the dashboard panels the installed apps fill for `user`, in order."""
    return tuple(panel for source in _panel_sources for panel in source(user))


_ACTION_LABELS = {1: "Added", 2: "Changed", 3: "Deleted"}


@dataclass(frozen=True, slots=True)
class HistoryEntry:
    """One `LogEntry` row of an object, flattened for the history page."""

    time: datetime
    user: str
    action: str
    message: str


def object_history(obj: Model) -> list[HistoryEntry]:
    """Return the `LogEntry` rows Django admin wrote for `obj`, newest first."""
    entries = (
        LogEntry.objects.filter(
            content_type=ContentType.objects.get_for_model(obj), object_id=obj.pk
        )
        .select_related("user")
        .order_by("-action_time")
    )
    return [
        HistoryEntry(
            time=entry.action_time,
            user=entry.user.get_username() if entry.user else "",
            action=_ACTION_LABELS.get(entry.action_flag, "?"),
            message=entry.get_change_message() or entry.change_message or "",
        )
        for entry in entries
    ]
