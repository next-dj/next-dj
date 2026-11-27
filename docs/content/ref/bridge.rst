.. _ref-bridge:

Async bridge reference
======================

Module summary
--------------

``next.bridge`` is the async runtime of the kernel, the code that runs an ``async def`` callable to its result from a sync caller.
A page view with an async ``render``, a component with an async ``@component.context`` callable, a sync resolve that meets an async dependency, and a ``Loader`` batch all run through it, with or without deferred boundaries.
It sits in the lowest tier of the kernel and imports only the foundation, so ``next.deps``, ``next.components``, and ``next.views`` import it directly, and a project without a single deferred boundary never loads ``next.defer`` for it.

``drive(work)`` runs ``work``, a coroutine or a factory of one, and answers its result or raises its error with the original traceback.
The coroutine runs on the event loop of the ASGI server when the calling thread is a ``sync_to_async`` thread of a running server loop, and on one process-wide background loop otherwise, so a WSGI process starts one loop thread rather than a loop per call.
Every thread-sensitive call the coroutine makes, a ``sync_to_async`` call and the Django async ORM among them, runs on the calling thread while it waits, so it shares the connection and the transaction of the sync code around it.
Context variables and asgiref ``Local`` values of the caller reach the coroutine, and the values it writes come back when ``drive`` returns.
Calling ``drive`` on a thread that runs an event loop raises ``RuntimeError``, since such code awaits the coroutine directly.

.. automodule:: next.bridge
   :members: drive

The runtime
-----------

The submodules are imported by their full path, by the framework areas that need them and by a project that builds on them.

``next.bridge.driver``
   ``drive`` and the run of one coroutine on a loop, the outcome handed back to the request thread by a hop that carries the context of the coroutine with it.
``next.bridge.loop``
   ``BackgroundLoop``, the daemon thread with one event loop that a caller outside a server loop runs coroutines on, started by the first caller, forgotten in a forked child, and stopped as the interpreter exits, and ``server_loop()``, which answers the server loop running the calling thread.
``next.bridge.request``
   ``RequestThreadExecutor``, the executor the waiting request thread serves, and ``serve_until(future)``, which blocks until the future is done and runs the hops queued for the thread meanwhile, a plain wait on a thread no driver serves.
``next.bridge.context``
   ``ContextSnapshot``, the snapshot of a request's context that work deferred from the request restores on its own thread or loop.
``next.bridge.tasks``
   ``StealableTask``, a unit of work that runs at most once on whichever caller claims it first, and ``contextless``, which starts a thread or a loop in an empty context so nothing it holds pins the caller's values.
``next.bridge.deadline``
   ``remaining()``, the seconds left before the deadline of the current job, and ``deadline(at)``, which runs a block under one.
``next.bridge.hops``
   The one module that reads or writes the private surface of asgiref, see below.

Deadlines
---------

A deferred job runs under the deadline of its boundary, and code inside it reads the time left through ``remaining()``, ``None`` outside a job or for a boundary without a ``timeout``.

.. autofunction:: next.bridge.deadline.remaining

.. autofunction:: next.bridge.deadline.deadline

Context snapshots
-----------------

asgiref 3.12 and later show a ``Local`` value only on a thread one of its own hops carried it to, so the language, the URLconf, and a ``Local`` of the project reach a pool job, a loop job, a ``drive`` coroutine, and a loader batch through a ``ContextSnapshot``.
``context`` is the request's context as captured, and ``thread`` the identity of the thread it was captured on.
``get()`` answers the context restored on the calling thread, ``context`` itself on the capturing thread, and ``None`` until a copy was kept there.
``restore()`` answers the snapshot restored on the calling sync thread through a hop from the server loop of that thread or from the background loop, waiting no longer than the deadline of the current job, and keeps the copy for later calls on the thread.
``arestore()`` answers the snapshot restored on the running loop through a hop that runs on the thread of that loop, so a busy default executor of the loop never holds it up, and one hop serves every caller on the loop.

.. autoclass:: next.bridge.context.ContextSnapshot
   :members: get, keep, restore, arestore

The asgiref surface
-------------------

``next.bridge.hops`` holds every access to the private surface of asgiref the runtime depends on, the executor a context routes thread-sensitive calls to, the loop markers of a ``sync_to_async`` thread, the executor classes asgiref leaves alone, and the hop that hands a pool unit over as a ``StealableTask``.
Its contract tests pin the asgiref releases the runtime is known to work on.
``next.E244`` reports an installed asgiref the hops cannot use, and ``next.W188`` one the contract tests have not run on, both from ``next.bridge.checks``, see :doc:`system-checks`.
The first ``drive`` of a process raises the error of ``next.E244`` and logs the warning of ``next.W188`` to ``next.bridge.hops``, since a server such as gunicorn or uvicorn runs no system check.
The background loop reports a failure of its own tasks and callbacks to the ``next.bridge`` logger.

See also
--------

.. seealso::

   :doc:`/content/topics/boundaries/async` for the async callables the bridge runs and the thread and transaction rules of a deferred job.
   :doc:`defer` for the pool lane and the deferred boundaries that build on the runtime.
   :doc:`/content/internals/overview` for where ``next.bridge`` sits among the layers.
