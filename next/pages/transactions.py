"""The transaction a `page.py` declares for its own page through `page.transaction`."""

from next.transactions import AtomicMode, AtomicOverride
from next.transactions.policy import AliasChoice, override_of, override_problem

from .declarations import PageDeclaration


def _override(
    *, atomic: AtomicMode, aliases: AliasChoice | list[str] = None
) -> AtomicOverride | None:
    """Return the override `atomic` and `aliases` make, `ValueError` for none."""
    override = override_of(atomic, aliases)
    if override is None:
        raise ValueError(override_problem(atomic, aliases))
    return override


transaction_declaration = PageDeclaration("transaction", _override, None)
"""The `page.transaction` call, which runs its page in a transaction or out of one."""

page_transaction = transaction_declaration.of
"""Return the transaction override of the page at a `page.py` path, `None` for none."""


__all__ = ["page_transaction", "transaction_declaration"]
