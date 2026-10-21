from collections.abc import Iterable

from django.db import transaction

from flags.models import Flag


def all_flags() -> list[Flag]:
    """Return every flag in name order."""
    return list(Flag.objects.all())


def flags_in_state(*, enabled: bool) -> list[Flag]:
    """Return the flags that are on, or the ones that are off."""
    return list(Flag.objects.filter(enabled=enabled))


def flags_named(names: Iterable[str]) -> dict[str, Flag]:
    """Return the flags among `names` that exist, keyed by name."""
    return {flag.name: flag for flag in Flag.objects.filter(name__in=list(names))}


def flag_names() -> list[str]:
    """Return the name of every flag in name order."""
    return list(Flag.objects.values_list("name", flat=True))


def set_enabled_flags(*, enabled_names: Iterable[str]) -> None:
    """Switch on exactly the flags in `enabled_names`, saving only the rows that move.

    Each row saves on its own, so the `post_save` receiver drops its cache entry.
    """
    wanted = set(enabled_names)
    with transaction.atomic():
        for flag in Flag.objects.select_for_update():
            should_be_on = flag.name in wanted
            if flag.enabled != should_be_on:
                flag.enabled = should_be_on
                flag.save(update_fields=["enabled", "updated_at"])
