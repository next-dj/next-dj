"""Wire names the areas below the partial protocol spell, declared once for them all.

`next.partial.keys` and `next.partial.headers` re-export them as part of the protocol.
"""

from typing import Final


REQUEST_FLAG: Final = "X-Next-Request"
"""The header marking a request the client runtime sends."""

RESPONSE_FORM: Final = "X-Next-Form"
"""The response header naming the outcome of a form submission."""

RESPONSE_ACTION: Final = "X-Next-Action"
"""The response header naming the form uid an invalid submission belongs to."""

BOUNDARY_ATTR: Final = "data-next-boundary"
"""The attribute naming a boundary wrapper by its wire id."""

BOUNDARY_KEY_ATTR: Final = "data-next-boundary-key"
"""The key of the loader call a streamed boundary wrapper stands for."""

SOURCE_ATTR: Final = "data-next-boundary-source"
"""The component or page a failed boundary wrapper was declared in."""

STATE_ATTR: Final = "data-next-state"
"""The state of a boundary wrapper, `loading`, `idle`, `ready` or `error`."""

ERROR_ATTR: Final = "data-next-error"
"""The error kind of a boundary wrapper settled as `error`."""

DIGEST_ATTR: Final = "data-next-error-digest"
"""The digest a `DEBUG` server writes on a failed boundary wrapper."""

DETAIL_ATTR: Final = "data-next-error-detail"
"""The message a `DEBUG` server writes on a failed boundary wrapper."""

KEY_ATTR: Final = "data-next-key"
"""The key of an item a list partial or a form names for the client."""

ASSET_ATTR: Final = "data-next-asset"
"""The owner of a stylesheet a routed render writes, `page` for the page."""

CURRENT_ATTR: Final = "data-next-current"
"""The active-link mark of an anchor, `page` or `ancestor`, and `off` to opt out."""

ACTION_ATTR: Final = "data-next-action"
"""The uid of the form action a form tag posts to."""
