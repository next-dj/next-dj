from dataclasses import dataclass

from django.dispatch import Signal

from shadcn_admin.permissions import Visitor
from shadcn_admin.signals import nav_requested, panels_requested


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


def _collected[T](signal: Signal, sender: type[T], user: Visitor) -> tuple[T, ...]:
    """Return every `sender` the receivers of `signal` answer for `user`, in order."""
    return tuple(
        item
        for _receiver, items in signal.send(sender=sender, user=user)
        for item in items
    )


def admin_nav(user: Visitor) -> tuple[NavLink, ...]:
    """Return the sidebar links the installed apps add for `user`."""
    return _collected(nav_requested, NavLink, user)


def dashboard_panels(user: Visitor) -> tuple[Panel, ...]:
    """Return the dashboard panels the installed apps fill for `user`."""
    return _collected(panels_requested, Panel, user)
