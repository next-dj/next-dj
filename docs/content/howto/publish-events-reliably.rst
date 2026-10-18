.. _howto-publish-events-reliably:

Publish events reliably
=======================

Problem
-------

A change in one application has to reach another, such as a credited wallet that the ledger of a reporting context records.
A direct call would make the lower application import the higher one and close a cycle, and the reaction must neither run for a change that rolled back nor vanish for one that committed.

Solution
--------

Turn the edge around with a signal, sent by the service that makes the change and received in the ``signals.py`` of the listener.
Connect the receiver with ``on_commit=True`` from ``next``, so it runs once the transaction commits and never after a rollback.
Where losing the reaction after the commit is not acceptable, write the event into an outbox table inside the same transaction and deliver it from a separate process.

Walkthrough
-----------

Prefer a call
~~~~~~~~~~~~~

A reaction that closes no cycle calls a function of the neighbour's ``services.py`` and needs no signal.
A signal exists to turn an edge around where an import would close a cycle, as :ref:`topics-boundaries-receivers` explains, so the lower application announces and the higher one listens.
Here ``wallets`` sits below ``reporting``, and the ledger of ``reporting`` reads the wallets, so ``wallets`` cannot import ``reporting`` back.

Declare and send the signal
~~~~~~~~~~~~~~~~~~~~~~~~~~~

The application that owns the change declares the signal in its ``signals.py``.

.. code-block:: python
   :caption: ledger/money/wallets/signals.py

   from django.dispatch import Signal

   wallet_credited = Signal()

The service that makes the change sends it from inside the transaction of the change, and ``next.W143`` keeps every send in ``services.py``.

.. code-block:: python
   :caption: ledger/money/wallets/services.py

   from django.db import transaction
   from ledger.money.wallets.models import Wallet
   from ledger.money.wallets.signals import wallet_credited

   def credit(scope, *, wallet_id: int, cents: int) -> None:
       with transaction.atomic():
           wallet = Wallet.objects.visible_to(scope).select_for_update().get(pk=wallet_id)
           wallet.balance_cents += cents
           wallet.save(update_fields=["balance_cents"])
           wallet_credited.send(sender=Wallet, wallet_id=wallet.pk, cents=cents)

``signals`` is a public module even of an isolated application, so the listener may import the signal from it.

Wait for the commit
~~~~~~~~~~~~~~~~~~~

The listener receives the signal in its own ``signals.py`` and connects with ``on_commit=True``.

.. code-block:: python
   :caption: ledger/reporting/ledgers/signals.py

   from ledger.money.wallets.signals import wallet_credited
   from ledger.reporting.ledgers.services import record_credit

   from next import receiver

   @receiver(wallet_credited, on_commit=True)
   def on_wallet_credited(sender, wallet_id: int, cents: int, **kwargs) -> None:
       record_credit(wallet_id=wallet_id, cents=cents)

The call waits for the commit of the outermost transaction and never runs when it rolls back.
A signal of the project sends no ``using`` keyword, so the receiver waits on the default database, and a project with several databases sends ``using=`` with the alias the change was written to.
:ref:`topics-signals` lists the other differences of a deferred receiver, among them that ``send`` no longer returns its value and that an exception it raises surfaces at the commit.

Know what the commit hook loses
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

:func:`~django.db.transaction.on_commit` keeps the hook in the memory of the process until the commit, and runs it right after.
A process that dies between the commit and the hook, a worker killed by a deploy, or a hook that raises leaves the change stored and the reaction undone, and nothing retries it.
For a reaction that can be rebuilt, such as a cache entry, a search index, or a total the ledger can recount, that is an acceptable trade.

Write an outbox where it is not
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

An outbox stores the event as a row in the same transaction as the change, so the change and the event commit or roll back together.
A separate process reads the rows not yet delivered, hands each one to its receivers or to a message broker, and marks it delivered, retrying a delivery that fails.
The delivery is at least once, so every receiver tolerates the same event twice, for example by keying its write on an id the event carries.

next.dj ships no outbox, and two Django libraries implement the pattern.
`django-domain-events <https://github.com/Artui/django-domain-events>`__ keeps a log of typed events with a durable outbox, retry per receiver, and a dead-letter store.
`django-jaiminho <https://github.com/loadsmart/django-jaiminho>`__ implements the outbox independently of the broker that delivers the messages.
Their own documentation describes how each one records and relays an event, and the service that makes the change stays the one place that publishes it, as it does with a signal.

Verification
------------

Run the service in a test inside :meth:`~django.test.TestCase.captureOnCommitCallbacks` with ``execute=True``, and assert that the ledger holds the credit once the block ends.
A second test that raises inside the ``atomic`` block of the service asserts that no hook ran and the ledger holds nothing.
``manage.py check --tag boundaries`` stays clean, which shows that the send lives in ``services.py``, the receiver in ``signals.py``, and that no import closes a cycle.

See also
--------

.. seealso::

   :doc:`/content/topics/signals` for ``receiver`` and the commit hook in full.
   :doc:`/content/topics/boundaries` for the rules on sending and receiving signals.
   :doc:`scale-to-contexts` for the contexts this recipe connects.
   :doc:`observe-framework-signals` for receivers of the framework signals.
