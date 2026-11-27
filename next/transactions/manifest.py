"""The startup wiring of the transaction policy and its lazy savepoints."""

from django.db.backends.signals import connection_created

from next.manifest import Manifest

from . import checks
from .lifecycle import TransactionsParticipant
from .savepoints import cover_new_connection


manifest = Manifest(
    "transactions",
    connects=((connection_created, cover_new_connection),),
    participants=(TransactionsParticipant(),),
    checks=checks,
)


__all__ = ["manifest"]
