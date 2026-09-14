"""Template tag library exposing `{% zone %}` to Django as a builtin.

The tag itself lives in `next.partial.zone`, re-exported for the builtin wiring.
"""

from next.partial.zone import register


__all__ = ["register"]
