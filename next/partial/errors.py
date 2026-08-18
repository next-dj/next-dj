"""Exceptions the patch builder raises when a caller breaks a wire contract."""

from typing import TYPE_CHECKING


if TYPE_CHECKING:
    from pathlib import Path


class UnknownPatchOpError(LookupError):
    """Raised when the builder is asked to emit an unregistered verb.

    The runtime guard pairs with the `next.E066` check, so an unknown
    verb fails fast in `op()` rather than reaching the client.
    """

    def __init__(self, name: str) -> None:
        """Store the unknown verb name and build a readable message."""
        self.name = name
        super().__init__(
            f'Patch op "{name}" is not registered. Register it with '
            "register_patch_op() before emitting it."
        )


class ReservedPatchKeyError(ValueError):
    """Raised when a custom op payload names a structural wire key.

    The `op`, `target`, and `html` keys carry the patch structure, so a
    payload that names one of them is refused rather than overwriting it.
    """

    def __init__(self, op: str, reserved: frozenset[str]) -> None:
        """Store the offending verb and the reserved keys it collided with."""
        self.op = op
        self.keys = reserved
        names = ", ".join(sorted(reserved))
        super().__init__(
            f'Patch op "{op}" payload names the reserved wire key(s) {names}. '
            "Use a different payload key, op/target/html are structural."
        )


class BuiltinPatchOpError(ValueError):
    """Raised when the generic `op()` channel names a built-in verb.

    A built-in verb owns typed wire keys, so it must travel through its
    typed builder method rather than the raw `op()` payload channel.
    """

    def __init__(self, name: str) -> None:
        """Store the built-in verb name and build a readable message."""
        self.name = name
        super().__init__(
            f'Patch op "{name}" is built in, emit it through its typed '
            "builder method rather than the generic op() channel."
        )


class ReservedEventNameError(ValueError):
    """Raised when `event()` names a framework-owned client-bus event.

    The `ready` and `context-updated` events and the `partial:` and
    `next:` prefixes belong to the runtime lifecycle, so an app event under
    one of those names is refused rather than forging a framework signal.
    """

    def __init__(self, name: str) -> None:
        """Store the reserved name and build a readable message."""
        self.name = name
        super().__init__(
            f'Event name "{name}" is reserved by the framework client bus. '
            "Use your own application event name instead."
        )


class ForeignPageNotAuthorizedError(PermissionError):
    """Raised when an OOB morph names a foreign page that denies the request.

    The zone of a foreign page renders only after that page's own body
    resolution authorizes the request, so the denial is surfaced rather
    than swallowed into an empty morph.
    """

    def __init__(self, page_path: "Path", status_code: int) -> None:
        """Store the page path and the short-circuit status code."""
        self.page_path = page_path
        self.status_code = status_code
        super().__init__(
            f"Page {page_path} did not authorize an out-of-band zone morph, "
            f"its body resolution short-circuited with status {status_code}. "
            "The zone of a foreign page is rendered only when that page would "
            "have served the request."
        )


class DynamicForeignPageError(ValueError):
    """Raised when an OOB morph names a foreign page with a `render()` body.

    A `render()` string body never reaches the composed-template cache, so
    it has no compiled source to render a standalone zone against. The OOB
    view branch refuses the same shape with a 400, so the builder refuses
    it here rather than morphing the page's stale static template.
    """

    def __init__(self, page_path: "Path") -> None:
        """Store the page path and build a readable message."""
        self.page_path = page_path
        super().__init__(
            f"Page {page_path} resolves a dynamic render() body, which has no "
            "zone to morph out of band. A foreign zone morph needs a page "
            "whose body is a static template."
        )


class UnknownContextNameError(LookupError):
    """Raised when `context()` names a value that is not a serialize provider.

    Only the names of registered `serialize=True` context providers may
    travel in a context patch, so an arbitrary mapping is rejected at the
    builder rather than serialized blind.
    """

    def __init__(self, name: str, available: tuple[str, ...] = ()) -> None:
        """Store the rejected name and the available serialize provider names."""
        self.name = name
        self.available = available
        message = (
            f'Context name "{name}" is not a registered serialize=True '
            "provider on the origin page. Mark its @context provider "
            "serialize=True or drop it from the patch."
        )
        if available:
            names = ", ".join(repr(provider) for provider in available)
            message = f"{message} Available serialize providers: {names}."
        super().__init__(message)


class ReservedContextKeyError(ValueError):
    """Raised when `context()` names a key the init payload owns.

    A full render keeps a reserved key for the framework, so a context
    patch that names one would leave the client store disagreeing with the
    page it patches. The explicit naming is a caller bug refused at the
    builder rather than merged on the client.
    """

    def __init__(self, reserved: frozenset[str]) -> None:
        """Store the reserved keys the call collided with."""
        self.keys = reserved
        names = ", ".join(sorted(reserved))
        super().__init__(
            f"Context patch names the reserved init-payload key(s) {names}. "
            "The framework owns those keys on every render, rename the "
            "serialize provider instead."
        )


class UnknownDedupeError(ValueError):
    """Raised when a merge op names a dedupe strategy the client cannot apply.

    The client keys a merge row by `data-next-key` then `id`, so only
    `key` and `id` mean anything on the wire, an unknown value is refused
    at the builder rather than dropped to a silent no-dedup downstream.
    """

    def __init__(self, dedupe: str) -> None:
        """Store the rejected dedupe value and build a readable message."""
        self.dedupe = dedupe
        super().__init__(
            f'Dedupe strategy "{dedupe}" is not supported, use "key" or "id".'
        )


class CrossSiteHrefError(ValueError):
    """Raised when a builder href sink names a cross-site URL.

    The `push_url`, `layer_open(href=)`, and internal `redirect` sinks
    author an in-app navigation, so a cross-site href is a caller bug
    refused at the builder rather than masked as a fallback to the origin
    path. A server-authored external destination travels through
    `redirect(external=True)` instead.
    """

    def __init__(self, href: str) -> None:
        """Store the rejected href and build a readable message."""
        self.href = href
        super().__init__(
            f'href "{href}" is not same-site, for a server-authored external '
            "destination use redirect(external=True)."
        )


class LayerHrefWithoutZoneError(ValueError):
    """Raised when a layer seeds an href but names no zone to load it into.

    The client fetch path needs a zone to know which fragment of the href
    to pull, so an href without one would silently open an empty modal,
    which is a caller bug refused at the builder.
    """

    def __init__(self, href: str) -> None:
        """Store the rejected href and build a readable message."""
        self.href = href
        super().__init__(
            f'href "{href}" needs a zone to load into, name the page zone that '
            'receives the content, for example layer_open(href=..., zone="record").'
        )


__all__ = [
    "BuiltinPatchOpError",
    "CrossSiteHrefError",
    "DynamicForeignPageError",
    "ForeignPageNotAuthorizedError",
    "LayerHrefWithoutZoneError",
    "ReservedContextKeyError",
    "ReservedEventNameError",
    "ReservedPatchKeyError",
    "UnknownContextNameError",
    "UnknownDedupeError",
    "UnknownPatchOpError",
]
