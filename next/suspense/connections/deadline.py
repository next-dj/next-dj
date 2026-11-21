"""The statement that hands a job's remaining budget to PostgreSQL per transaction."""

import math
from typing import Final


DEADLINE_SQL: Final = (
    "SELECT set_config('statement_timeout', %s, true), "
    "set_config('idle_in_transaction_session_timeout', %s, true)"
)
"""Both timeouts local to the transaction, so they end with it and never leak."""


def timeout_ms(remaining: float, floor: float) -> int:
    """Return `remaining` seconds in whole milliseconds rounded up, at least `floor`."""
    return max(math.ceil(remaining * 1000), math.ceil(floor * 1000))


def deadline_statement(remaining: float, floor: float) -> tuple[str, tuple[str, str]]:
    """Return the SQL and parameters setting both timeouts to the remaining budget.

    `set_config` takes text, so the milliseconds travel as a string parameter.
    """
    value = str(timeout_ms(remaining, floor))
    return DEADLINE_SQL, (value, value)


__all__ = ["DEADLINE_SQL", "deadline_statement", "timeout_ms"]
