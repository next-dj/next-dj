.. _howto-replay-request-state-on-a-pool-thread:

Replay request state on a pool thread
=====================================

Problem
-------

A deferred job runs on a pool thread with a database connection of its own, see :doc:`/content/topics/boundaries/async`.
The context variables of the view reach it, while state the view keeps on its connection or in a ``threading.local`` does not.
A multi-tenant project that sets the PostgreSQL ``search_path`` of the tenant on the request connection would then read the rows of the wrong schema from a job.
The framework detects such sources and keeps every job on the request thread instead, which ``manage.py check`` reports as ``next.W155`` for connection state and ``next.W171`` for thread-local state.

Solution
--------

``NEXT_FRAMEWORK["DEFER"]["CONNECTION_STATE"]`` lists ``ConnectionState`` backends, which capture state on the request thread and replay it on the connection a pool thread opens.
``NEXT_FRAMEWORK["DEFER"]["THREAD_STATE"]`` lists ``ThreadState`` backends, which do the same for a ``threading.local``.
Each backend names in ``covers`` the detected sources it replays, and a source every backend leaves uncovered keeps pinning the jobs to the request thread.

Walkthrough
-----------

Detected sources
~~~~~~~~~~~~~~~~

A source reads ``kind:detail``, and ``covers`` names it by the kind, which covers every source of that kind, or in full.

.. list-table::
   :header-rows: 1
   :widths: 30 70

   * - Kind
     - Detected from
   * - ``routers``
     - An entry of ``DATABASE_ROUTERS`` whose router does not set ``next_stateless = True``, the detail being its dotted path.
   * - ``tenant_middleware``
     - A ``MIDDLEWARE`` entry of ``django-tenants`` or ``django-tenant-schemas``.
   * - ``thread_local_middleware``
     - A middleware listed in ``DEFER["EXECUTOR"]["THREAD_LOCAL_MIDDLEWARE"]``.
   * - ``django-crum``, ``django-multitenant``, ``django-scopes``, ``django-tenant-users``, ``django-threadlocals``, ``django-currentuser``
     - An ``INSTALLED_APPS`` or ``MIDDLEWARE`` entry under the import root of that library.

``routers`` and ``tenant_middleware`` are per-connection sources, which a ``ConnectionState`` covers, and the rest are thread-local, which a ``ThreadState`` covers.

Replay the tenant schema
~~~~~~~~~~~~~~~~~~~~~~~~

The backend below reads the schema the tenant middleware put on the request and sets it as the ``search_path`` of the pool connection.

.. code-block:: python
   :caption: tenants/replay.py

   from django.db.backends.base.base import BaseDatabaseWrapper
   from django.http import HttpRequest

   from next.defer import ConnectionState

   class TenantSchema(ConnectionState):
       covers = frozenset({"tenant_middleware"})

       def capture(self, request: HttpRequest, alias: str) -> str | None:
           tenant = getattr(request, "tenant", None)
           return None if tenant is None else tenant.schema_name

       def apply(self, state: object, connection: BaseDatabaseWrapper) -> None:
           with connection.cursor() as cursor:
               cursor.execute(
                   "SELECT set_config('search_path', %s, true)", [f"{state}, public"]
               )

.. code-block:: python
   :caption: config/settings.py

   NEXT_FRAMEWORK = {
       "DEFER": {"CONNECTION_STATE": ["tenants.replay.TenantSchema"]},
   }

``capture`` runs in the view once per database alias, and ``None`` replays nothing for that alias.
``apply`` runs on the pool thread right before the first statement of the job that reads or writes data, inside a transaction the replay opens and commits when the job ends, so a ``set_config(..., true)`` or ``SET LOCAL`` stays local to it and never leaks to the next job on that connection.
A job that opens its own ``atomic`` block gets the state replayed inside that block instead.

Replay a thread-local
~~~~~~~~~~~~~~~~~~~~~

A ``ThreadState`` backend captures a value on the request thread, applies it on the pool thread before the job, and resets it after.
The backend below carries the current user of ``django-crum``.

.. code-block:: python
   :caption: accounts/replay.py

   from crum import get_current_user, impersonate
   from django.http import HttpRequest

   from next.defer import ThreadState

   class CurrentUser(ThreadState):
       covers = frozenset({"django-crum"})

       def capture(self, request: HttpRequest) -> object | None:
           return get_current_user()

       def apply(self, state: object) -> object:
           scope = impersonate(state)
           scope.__enter__()
           return scope

       def reset(self, token: object) -> None:
           token.__exit__(None, None, None)

.. code-block:: python
   :caption: config/settings.py

   NEXT_FRAMEWORK = {
       "DEFER": {"THREAD_STATE": ["accounts.replay.CurrentUser"]},
   }

``apply`` returns the token ``reset`` receives, and ``reset`` runs whether the job succeeded or raised.

Verification
------------

Run ``manage.py check``, and neither ``next.W155`` nor ``next.W171`` names a covered source.
On a page with deferred boundaries, ``boundary_settled`` reports the ``pool`` lane for each job once no other request-lane rule holds, see :doc:`/content/ref/signals`.

See also
--------

.. seealso::

   :doc:`/content/topics/boundaries/async` for the lanes and the request-lane rules.
   :doc:`/content/ref/defer` for the ``ConnectionState`` and ``ThreadState`` contracts.
