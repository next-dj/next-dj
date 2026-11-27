"""Exceptions of the boundary unit, raised where its options are declared."""


class BoundaryOptionError(ValueError):
    """A boundary option written with a value outside its shape."""


__all__ = ["BoundaryOptionError"]
