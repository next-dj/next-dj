"""Signal receivers that count form-action dispatches into the shared cache.

Hooked on `action_dispatched` so every `@forms.action` handler adds one to
a per-action counter. The admin "Stats" page reads the counters to show
how often each form has been submitted this process.
"""

from __future__ import annotations

import threading

from django.core.cache import cache
from django.dispatch import receiver

from next.forms.signals import action_dispatched


ACTION_COUNT_PREFIX = "shortener:action:"
ACTION_INDEX_KEY = "shortener:action:__index__"

_index_lock = threading.Lock()


def _key(action_name: str) -> str:
    return f"{ACTION_COUNT_PREFIX}{action_name}"


def _remember(action_name: str) -> None:
    with _index_lock:
        known: set[str] = set(cache.get(ACTION_INDEX_KEY) or ())
        if action_name in known:
            return
        known.add(action_name)
        cache.set(ACTION_INDEX_KEY, known)


@receiver(action_dispatched)
def _on_action_dispatched(
    sender: object,  # noqa: ARG001 — signal receivers take `sender` by contract
    action_name: str,
    **_kwargs: object,
) -> None:
    """Bump the counter for `action_name` and track the name for readout."""
    key = _key(action_name)
    cache.add(key, 0)
    cache.incr(key)
    _remember(action_name)


def action_counts() -> dict[str, int]:
    """Return `{action_name: count}` for every dispatched action."""
    with _index_lock:
        known: set[str] = set(cache.get(ACTION_INDEX_KEY) or ())
    if not known:
        return {}
    snapshot = cache.get_many([_key(name) for name in known])
    return {k.removeprefix(ACTION_COUNT_PREFIX): int(v) for k, v in snapshot.items()}
