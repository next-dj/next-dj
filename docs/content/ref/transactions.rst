.. _ref-transactions:

Transactions reference
======================

Module summary
--------------

``next.transactions`` is the package of the transaction rule of next.dj views and the lazy savepoint an error boundary guards its subtree with.
The ``TRANSACTIONS`` and ``BOUNDARIES`` scopes are in :doc:`settings`, and :doc:`/content/howto/run-a-page-in-a-transaction` walks through a page in a transaction.

Public API
----------

.. autoclass:: next.transactions.AtomicOverride

.. autoclass:: next.transactions.AtomicScope

.. autofunction:: next.transactions.atomic_scope

.. autofunction:: next.transactions.request_atomic

.. autofunction:: next.transactions.skip_atomic_requests

.. py:data:: next.transactions.AtomicMode

   ``True``, ``False``, or ``"writes"``, the one vocabulary of every transaction choice.
   ``TRANSACTIONS["SAFE"]`` and ``TRANSACTIONS["UNSAFE"]``, ``page.transaction()``, ``@action(atomic=...)``, ``Meta.atomic`` on a form class, ``AtomicOverride.atomic``, and ``BOUNDARIES["ATOMIC"]`` all take it.
   For a view, ``True`` opens the transaction on entry, before any statement, ``"writes"`` opens it right before the first write on each alias, and ``False`` opens none.
   Inside an error boundary that runs in autocommit, ``True`` opens the subtree's transaction before its first statement, ``"writes"`` before its first write, and ``False`` never, while a boundary inside a transaction takes a savepoint at its first statement there whatever the mode.

.. py:data:: next.transactions.TransactionRule

   The callable ``TRANSACTIONS["RULE"]`` names, taking the ``HttpRequest`` and the ``RequestKind`` of ``next.requests`` and returning either the iterable of database aliases to wrap on entry or an ``AtomicOverride`` that names the mode and the aliases.

Boundary savepoints
-------------------

An error boundary guards its subtree with a savepoint it takes lazily, at the first statement inside a transaction or at the moment ``BOUNDARIES["ATOMIC"]`` names in autocommit, so a subtree that runs no query costs no round trip.
That savepoint has caveats that follow from the transaction a request is in.

In autocommit, a write in an atomic block opened before the boundary's transaction commits with that block, not with the subtree.
A durable block after the first write of the subtree raises the ``RuntimeError`` Django raises, except under a ``TestCase``.
A rollback to the savepoint keeps the ``on_commit`` hooks the subtree added.
Once a write lands in the boundary's own transaction, a database error the subtree catches dooms that transaction.

See also
--------

.. seealso::

   :doc:`boundaries` for the error boundary that takes the savepoint.
   :doc:`pages` for ``page.transaction()``.
   :doc:`views` for the ``RequestKind`` a transaction rule receives.
