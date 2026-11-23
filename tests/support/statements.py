import re
from collections.abc import Iterator
from contextlib import contextmanager

from django.db import connection, connections
from django.db.backends.base.base import BaseDatabaseWrapper
from django.test.utils import CaptureQueriesContext


_SAVEPOINT_NAME = re.compile(r'"s\d+_x\d+"')
_TABLE = re.compile(r'^(INSERT INTO|DELETE FROM|UPDATE) "(\w+)".*')


def statement(sql: str) -> str:
    """Reduce one captured query to its verb and table, a savepoint to its kind."""
    sql = _SAVEPOINT_NAME.sub("sp", sql)
    if sql.startswith("SELECT"):
        return "SELECT"
    return _TABLE.sub(r"\1 \2", sql)


@contextmanager
def statement_log() -> Iterator[list[str]]:
    """Collect the reduced statements the block runs on the default connection."""
    statements: list[str] = []
    with CaptureQueriesContext(connection) as queries:
        yield statements
    statements.extend(statement(query["sql"]) for query in queries.captured_queries)


def close_driver(wrapper: BaseDatabaseWrapper) -> None:
    """Close the driver connection of `wrapper`, if it opened one."""
    if wrapper.connection is not None:
        wrapper.connection.close()


@contextmanager
def fresh_default() -> Iterator[None]:
    """Swap in a new `default` connection object and close it on the way out."""
    original = connections["default"]
    del connections["default"]
    try:
        yield
    finally:
        fresh = connections["default"]
        connections["default"] = original
        close_driver(fresh)


def query_missing_table() -> None:
    """Run a query that fails, as its table does not exist."""
    with connection.cursor() as cursor:
        cursor.execute("SELECT * FROM next_missing_table")


__all__ = [
    "close_driver",
    "fresh_default",
    "query_missing_table",
    "statement",
    "statement_log",
]
