import inspect


def port_methods(port: type) -> list[str]:
    """Return the public method names `port` declares itself, sorted."""
    return sorted(
        name
        for name, member in vars(port).items()
        if not name.startswith("_") and inspect.isfunction(member)
    )


def call_shape(owner: type, name: str) -> list[tuple[str, object]]:
    """Return the parameter names and kinds, leaving out defaults an override adds."""
    signature = inspect.signature(getattr(owner, name))
    return [(param.name, param.kind) for param in signature.parameters.values()]


def parameter_shape(owner: type, name: str) -> list[tuple[str, object, object]]:
    """Return the parameter names, kinds and defaults of `owner.name`."""
    signature = inspect.signature(getattr(owner, name))
    return [
        (param.name, param.kind, param.default)
        for param in signature.parameters.values()
    ]
