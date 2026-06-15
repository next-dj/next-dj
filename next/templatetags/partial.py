"""Template tag library exposing `{% zone %}` to Django as a builtin.

The zone node, the placeholder branch, and the standalone zone-body
renderable live in `next.partial.markers`. This module re-exports the
library so the framework can register the tag through the same builtin
wiring as the form and component tags.
"""

from next.partial.markers import register


__all__ = ["register"]
