"""The boundary unit, its options, error UI and wire ids, and the inline error boundary.

Every area that draws a boundary reads it from here, the deferred engine included.
"""

from .containment import contain
from .errors import BoundaryOptionError
from .failures import ErrorKind
from .signals import boundary_failed


__all__ = ["BoundaryOptionError", "ErrorKind", "boundary_failed", "contain"]
