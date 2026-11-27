"""The async runtime that runs coroutines to their result from sync code.

A sync view, a component and a loader await through it, their ORM calls on the caller.
"""

from .driver import drive


__all__ = ["drive"]
