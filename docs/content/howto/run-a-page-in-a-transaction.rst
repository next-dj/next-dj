.. _howto-run-a-page-in-a-transaction:

Run a page in a transaction
===========================

Problem
-------

A page claims a one-time invitation on a ``GET``, and the claim and the rows that follow it must commit together or not at all.
Another page posts to itself and then calls a slow service, and holds a connection inside a transaction while it waits.
A third page takes a ``POST`` that mostly writes nothing, and still connects to the database to open a transaction it never uses.

Solution
--------

next.dj views follow the ``TRANSACTIONS`` rule, whose default follows Django's ``ATOMIC_REQUESTS``.
The rule wraps a ``POST`` and every request on an alias that sets ``ATOMIC_REQUESTS``, and leaves any other ``GET`` in autocommit.
``page.transaction(atomic=True)`` wraps every request of one page, a ``GET`` included, and ``page.transaction(atomic=False)`` takes every request of it out.
``page.transaction(atomic="writes")`` opens the transaction right before the first write, so a request that writes nothing opens none.

Walkthrough
-----------

Know the default
~~~~~~~~~~~~~~~~

.. code-block:: python
   :caption: the default of NEXT_FRAMEWORK["TRANSACTIONS"]

   {"SAFE": "auto", "UNSAFE": "auto", "ALIASES": ("default",), "RULE": None}

``SAFE`` decides for ``GET``, ``HEAD``, ``OPTIONS``, and ``TRACE``, and ``UNSAFE`` for every other method.
``"auto"`` opens a transaction on every alias that sets ``ATOMIC_REQUESTS``, for an unsafe method on each alias of ``ALIASES`` too, nested so the last alias holds the outermost transaction, as Django nests them.
The transaction opens eagerly before the view runs, so an :func:`~django.db.transaction.on_commit` hook registered before the first query waits for the commit.
An access check of the page runs before it opens, so a refused request opens none.
An exception leaving the view rolls the transaction back, and a returned response commits it whatever its status.

Wrap one page
~~~~~~~~~~~~~

.. code-block:: python
   :caption: shop/pages/invites/accept/[token]/page.py

   from shop import services
   from shop.models import Invite

   from next import context, page

   page.transaction(atomic=True)

   @context("invite")
   def invite(token: str) -> Invite:
       claimed = services.claim(token)
       services.grant_seat(claimed)
       return claimed

.. code-block:: jinja
   :caption: shop/pages/invites/accept/[token]/template.djx

   <p>The invite {{ invite.token }} is accepted.</p>

Every request of the page runs inside one transaction on ``TRANSACTIONS["ALIASES"]``, so a failure in ``grant_seat`` takes the claim back.
The full page, a zone request, a boundary reload, and a client navigation of the page all read the declaration.
``aliases`` names others.

.. code-block:: python
   :caption: shop/pages/ledger/page.py

   from next import page

   page.transaction(atomic=True, aliases=["default", "ledger"])

``aliases="all"`` takes every alias ``DATABASES`` declares.
The declaration binds its own page alone and never the form actions the page declares, which take their own choice through ``@action(atomic=...)`` or ``Meta.atomic``, see :ref:`topics-forms-actions-transactions`.
The last call of a module wins, a mode other than ``True``, ``False``, or ``"writes"``, or an ``aliases`` value of another shape, raises :exc:`~django.core.exceptions.ImproperlyConfigured` when the module runs, and a call inside a function raises ``DeclarationOutsideModuleError``.

Take a page out of the transaction
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

A ``POST`` that does slow work outside the database holds its connection inside an open transaction while that work runs.

.. code-block:: python
   :caption: shop/pages/report/page.py

   from next import page

   page.transaction(atomic=False)

The page then runs in autocommit, a ``POST`` included, and an ``on_commit`` hook fires at once.
The rows it writes commit one statement at a time, so a page that opts out writes through an explicit :func:`~django.db.transaction.atomic` block around whatever must stay together.

Open the transaction at the first write
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

A search form or a validation round trip posts often and writes rarely.

.. code-block:: python
   :caption: shop/pages/search/page.py

   from next import page

   page.transaction(atomic="writes")

The page runs in autocommit until its first write or :func:`~django.db.transaction.on_commit` hook, the transaction opens on that alias right before it, and everything after it commits or rolls back as one unit.
The reads before the first write run in autocommit, and a request that writes nothing opens no connection and issues no statement.
Several aliases each open on their own first write and settle in the order ``True`` settles them.
The same value works wherever a mode is chosen, ``SAFE`` and ``UNSAFE``, ``@action(atomic="writes")``, ``Meta.atomic``, and the ``AtomicOverride`` a ``RULE`` returns.

.. list-table::
   :header-rows: 1
   :widths: 15 40 45

   * - Mode
     - When the transaction opens
     - What ``on_commit`` does
   * - ``True``
     - On entry, before the view runs.
     - Waits for the commit, a hook registered before the first query included.
   * - ``"writes"``
     - Right before the first write or hook on each alias.
     - Opens the transaction when registered before the first write, and waits for the commit.
   * - ``False``
     - Never, the view runs in autocommit.
     - Runs at once.

A hook registered before the first write counts as a write, so it never runs before the writes that follow it and a rollback drops it.
The classifier that spots a write reads the SQL, so a statement it does not know counts as a write, and a call through ``cursor.callproc`` opens nothing.
An atomic block entered before the first write, one the view opens or one Django opens inside ``get_or_create``, ``update_or_create``, or ``bulk_create``, commits on its own, and only the writes after it join the view's transaction.
A component or page boundary inside the view nests in the transaction once it opens, so a failed boundary takes back its own writes under every mode.

Change the rule for the project
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

.. code-block:: python
   :caption: config/settings.py

   NEXT_FRAMEWORK = {
       "TRANSACTIONS": {"SAFE": False, "UNSAFE": True, "ALIASES": "all"},
   }

``SAFE`` and ``UNSAFE`` take ``True``, ``False``, ``"writes"``, or ``"auto"``, and ``ALIASES`` names the databases, or ``"all"`` for every alias.
A value outside those shapes raises :exc:`~django.core.exceptions.ImproperlyConfigured` at the first request, naming the key, the value, and what it takes.
An alias ``DATABASES`` does not declare is skipped, so a project without a database opens no transaction, and ``manage.py check`` reports it as ``next.W179``.
A database alias with ``ATOMIC_REQUESTS`` set under a ``SAFE``, ``UNSAFE``, or ``RULE`` that stops following it is reported as ``next.W154``.

``RULE`` replaces ``SAFE``, ``UNSAFE``, and ``ALIASES`` with a callable.
The callable takes the request and the kind of the view, which is ``page``, ``zone``, ``boundary``, ``navigate``, or ``action``.
It returns the aliases to wrap on entry, or an ``AtomicOverride`` that names the mode as well.

.. code-block:: python
   :caption: config/transactions.py

   from django.http import HttpRequest

   from next.requests import RequestKind
   from next.transactions import AtomicOverride

   def rule(request: HttpRequest, kind: RequestKind) -> tuple[str, ...] | AtomicOverride:
       if kind is RequestKind.ACTION:
           return AtomicOverride(atomic="writes", aliases=("default", "ledger"))
       return ("default",) if request.method == "POST" else ()

.. code-block:: python
   :caption: config/settings.py

   NEXT_FRAMEWORK = {
       "TRANSACTIONS": {"RULE": "config.transactions.rule"},
   }

``page.transaction()`` and ``@action(atomic=...)`` still win over the rule for their own view.
An ``AtomicOverride`` the rule returns with a mode it does not know raises :exc:`~django.core.exceptions.ImproperlyConfigured` for that request.

See what the page issues
~~~~~~~~~~~~~~~~~~~~~~~~

Inside the transaction, every component or page boundary protects its subtree with a savepoint, so a failed component takes back its own writes and the page goes on.
The outermost boundary takes its savepoint right before the first statement of its subtree, and a boundary inside it takes one right before its first write.
In autocommit, the default for a ``GET``, a boundary issues no statement of its own, see :doc:`/content/topics/boundaries/errors`.
A query count taken under pytest-django or a ``TestCase`` sees the ``SAVEPOINT`` and ``RELEASE`` of the outermost boundary that queried, because the test itself runs in a transaction.
A page that must be asserted against the number of its own queries counts them in a test marked ``@pytest.mark.django_db(transaction=True)``.

Mind the streamed page
~~~~~~~~~~~~~~~~~~~~~~

A page with deferred boundaries writes its chunks after the view returned.
The jobs of a streamed page therefore run after the view's own transaction ended, and see committed rows only.
A request whose jobs must share one transaction, with the writes the request made, is answered in one piece, which is what the delivery policy does for every ``POST``, and its jobs run on the request thread, see :doc:`/content/topics/boundaries/async`.

Verification
------------

A boundary that fails takes its writes back.
This test reads ``/notes/``, a page whose ``page.py`` declares ``page.transaction(atomic=True)`` and whose template calls a ``note`` component.
The context callable of that component creates a ``Note`` and then raises, and its ``error.djx`` renders an element with ``data-note-error``.

.. code-block:: python
   :caption: tests/test_notes.py

   from shop.models import Note

   from next.testing import boundary_errors

   def test_a_failed_component_leaves_no_row(next_client, db) -> None:
       with boundary_errors("render"):
           response = next_client.get("/notes/")
       assert b"data-note-error" in response.content
       assert not Note.objects.exists()

The same test passes without ``page.transaction(atomic=True)``, since a boundary opens its own transaction before its first write.
The declaration matters for the writes outside any boundary, which the page commits or rolls back as a unit.
The next test reads the invite page above, and its ``invite`` fixture saves an unclaimed ``Invite``.

.. code-block:: python
   :caption: tests/test_invites.py

   import pytest
   from shop.models import Invite

   def refuse(claimed: Invite) -> None:
       msg = "no seats left"
       raise RuntimeError(msg)

   def test_a_failed_grant_takes_the_claim_back(next_client, invite, monkeypatch) -> None:
       monkeypatch.setattr("shop.services.grant_seat", refuse)
       with pytest.raises(RuntimeError):
           next_client.get(f"/invites/accept/{invite.token}/")
       invite.refresh_from_db()
       assert invite.claimed_at is None

``manage.py check`` reports a ``page.transaction()`` outside a ``page.py`` as ``next.E240``.

See also
--------

.. seealso::

   :doc:`/content/ref/pages` for ``page.transaction()`` and :doc:`/content/ref/settings` for the ``TRANSACTIONS`` scope.
   :doc:`/content/topics/boundaries/errors` for what a failed boundary rolls back and what it leaves behind.
   :doc:`/content/topics/forms/actions` for the transaction of a form action.
   :doc:`contain-a-failure-with-an-error-boundary` for the error UI that replaces a failed subtree.
