"""Registries of the object permissions by name and of the access declared per file."""

import threading
from collections.abc import Iterable, Iterator
from contextlib import contextmanager
from dataclasses import replace
from pathlib import Path

from .errors import PermissionAlreadyRegisteredError
from .markers import OPEN, AccessRule, Predicate, Requirement, Test, predicate as leaf
from .signals import permission_registered


_NAME_PARTS = 2


def _check_name(name: str) -> None:
    """Refuse a name Django cannot address, which is `app_label.codename`."""
    parts = name.split(".")
    if len(parts) != _NAME_PARTS or not all(parts):
        msg = (
            f"The permission {name!r} is not named 'app_label.codename', the form "
            "user.has_perm() looks permissions up by."
        )
        raise ValueError(msg)


class PermissionRegistry:
    """Hold one predicate per permission name, in the order the names arrived."""

    def __init__(self) -> None:
        """Start empty."""
        self._names: list[str] = []
        self._index: dict[str, Predicate] = {}
        self._version = 0

    @property
    def version(self) -> int:
        """Monotonic counter bumped on every change, a token for derived memos."""
        return self._version

    def add(self, name: str, predicate: Predicate | Test) -> Predicate:
        """Bind `predicate` to the permission `name` and return it as a `Predicate`.

        An equal predicate under the same name is a repeated import and changes nothing.
        """
        _check_name(name)
        rule = predicate if isinstance(predicate, Predicate) else leaf(predicate)
        held = self._index.get(name)
        if held is not None:
            if held == rule:
                return held
            raise PermissionAlreadyRegisteredError(name)
        self._names.append(name)
        self._index[name] = rule
        self._version += 1
        permission_registered.send(sender=PermissionRegistry, name=name, predicate=rule)
        return rule

    def get(self, name: str) -> Predicate | None:
        """Return the predicate of the permission `name`, if it is registered."""
        return self._index.get(name)

    def __contains__(self, name: object) -> bool:
        """Return whether the permission `name` is registered."""
        return name in self._index

    def __len__(self) -> int:
        """Return how many permissions are registered."""
        return len(self._names)

    def items(self) -> Iterator[tuple[str, Predicate]]:
        """Yield each registered name with its predicate, in registration order."""
        for name in self._names:
            yield name, self._index[name]

    def reset(self) -> None:
        """Drop every permission."""
        self._names.clear()
        self._index.clear()
        self._version += 1


def _required(
    held: AccessRule, file_path: Path, requirement: Requirement
) -> AccessRule:
    """Return `held` with `requirement` added, the same rule when it holds it."""
    if requirement in held.requirements:
        return held
    requirements = (*held.requirements, requirement)
    return replace(held, requirements=requirements, sources=(file_path,))


def _public(held: AccessRule, file_path: Path) -> AccessRule:
    """Return `held` marked public, the same rule when it already is."""
    if held.public:
        return held
    return replace(held, public=True, sources=(file_path,))


class _Collecting(threading.local):
    """The declarations each execution under way in this thread gathered so far."""

    def __init__(self) -> None:
        """Start with no execution under way."""
        self.rules: dict[Path, AccessRule] = {}


class DeclarationRegistry:
    """Hold the access each file declares about itself, in the order it declared it.

    Each change stamps its file, so a memo over a few files outlives a change elsewhere.
    """

    def __init__(self) -> None:
        """Start empty."""
        self._rules: dict[Path, AccessRule] = {}
        self._stamps: dict[Path, int] = {}
        self._version = 0
        self._collecting = _Collecting()

    @property
    def version(self) -> int:
        """Monotonic counter bumped on every change, a token for derived memos."""
        return self._version

    def _store(self, file_path: Path, rule: AccessRule) -> None:
        """Replace the declarations of `file_path` and stamp the change."""
        self._rules[file_path] = rule
        self._version += 1
        self._stamps[file_path] = self._version

    def _change(self, file_path: Path, rule: AccessRule) -> None:
        """Hold `rule` for `file_path`, gathered apart while it is executing."""
        collected = self._collecting.rules
        if file_path in collected:
            collected[file_path] = rule
        elif rule is not self._rules.get(file_path, OPEN):
            self._store(file_path, rule)

    def _held(self, file_path: Path) -> AccessRule:
        """Return what `file_path` holds now, its execution under way first."""
        collected = self._collecting.rules.get(file_path)
        if collected is not None:
            return collected
        return self._rules.get(file_path, OPEN)

    def declare(self, file_path: Path, requirement: Requirement) -> None:
        """Add `requirement` to `file_path`, a repeated one changing nothing."""
        held = self._held(file_path)
        self._change(file_path, _required(held, file_path, requirement))

    def declare_public(self, file_path: Path) -> None:
        """Mark `file_path` public, which its own requirements still bind."""
        self._change(file_path, _public(self._held(file_path), file_path))

    @contextmanager
    def collecting(self, file_path: Path) -> Iterator[None]:
        """Gather what one execution of `file_path` declares and swap it in at the end.

        A reader during the execution sees the earlier declarations rather than none.
        """
        collected = self._collecting.rules
        collected[file_path] = OPEN
        try:
            yield
        finally:
            rule = collected.pop(file_path)
            if rule != self._rules.get(file_path, OPEN):
                if rule == OPEN:
                    self.forget(file_path)
                else:
                    self._store(file_path, rule)

    def forget(self, file_path: Path) -> None:
        """Drop what `file_path` declared, for a file gone or no longer loaded."""
        if self._rules.pop(file_path, None) is not None:
            self._version += 1
            self._stamps[file_path] = self._version

    def declared(self, file_path: Path) -> AccessRule:
        """Return what `file_path` declared, an open rule when it declared nothing."""
        return self._rules.get(file_path, OPEN)

    def files(self) -> tuple[Path, ...]:
        """Return every file holding a declaration, in the order they first declared."""
        return tuple(self._rules)

    def stamps(self, paths: Iterable[Path]) -> tuple[int | None, ...]:
        """Return the version of the last change to each path, `None` for none."""
        return tuple(map(self._stamps.get, paths))

    def reset(self) -> None:
        """Drop every declaration."""
        for file_path in self._rules:
            self._stamps[file_path] = self._version + 1
        self._rules.clear()
        self._version += 1


__all__ = ["DeclarationRegistry", "PermissionRegistry"]
