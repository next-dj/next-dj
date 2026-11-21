from access.services import audit_totals

from next import component


component.defer("visible", props=("kind",))


@component.context("totals")
def totals(kind: str) -> dict[str, int]:
    """Count the rows of `kind` per channel, every row for a value that names none."""
    return audit_totals(kind=kind)
