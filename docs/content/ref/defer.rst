.. _ref-defer:

Deferred boundaries reference
=============================

Module summary
--------------

Four modules carry deferred boundaries and error boundaries.
``next.defer`` holds the vocabulary every area shares, the modes, the deliveries, the prefetch depths, the error kinds, the options a boundary declares, and the deadline of the current job.
``next.boundaries`` is the package of the inline error boundary and the ``boundary_failed`` signal.
``next.transactions`` is the package of the transaction rule of next.dj views and the lazy savepoint a boundary guards its subtree with.
``next.suspense`` runs deferred jobs, decides the delivery of a response, writes a streamed page, and mints the tokens of client-trip boundaries.
Its ``__all__`` lists the extension points and the types their signatures name, and the rest imports by the full path of its module.
:doc:`/content/topics/boundaries/index` is the guide, and the ``DEFER`` and ``TRANSACTIONS`` scopes are in :doc:`settings`.

Declarations
------------

``page.defer(*, timeout=None, parallel=None)`` at the top of a ``page.py`` bounds the job of the page boundary by ``timeout`` and places it, ``True`` pinning the pool, ``False`` the request thread, and ``None`` leaving it to placement.

``component.defer(mode="stream", *, timeout=None, props=(), tag=WrapperTag.SITE, parallel=None, tokens=None)`` at the top of a ``component.py`` delivers the component as a boundary in ``mode``.
``props`` names the props a reload or a retry renders from, ``tag`` the wrapper element, ``WrapperTag.SITE`` leaving it to the call site and ``None`` adopting the root of the body, and ``tokens`` overrides ``max_age``, ``bucket``, ``binding``, or ``max_bytes`` of ``DEFER["TOKENS"]["OPTIONS"]`` for this component.
``component.props(*names)`` declares the props alone, and naming props through both is an error.

``page.atomic(atomic, *, aliases=None)`` declares the transaction of a page, see :doc:`pages`, and ``page.compress(enabled)`` its compression, see :doc:`compression`.
A declaration outside its module kind, inside a function, or with a value outside its shape declares nothing and is reported by ``next.E222`` or ``next.E240``.

The vocabulary
--------------

.. autoclass:: next.defer.DeferMode
   :no-special-members:

.. autoclass:: next.defer.Delivery
   :no-special-members:

.. autoclass:: next.defer.Depth
   :no-special-members:

``JOBLESS`` is the set of deliveries that answer the loading UI and run no deferred job, ``SHELL`` and ``PREFETCH``.

.. autoclass:: next.defer.WrapperTag
   :no-special-members:

.. autoclass:: next.defer.ErrorKind
   :no-special-members:

.. autoclass:: next.defer.BoundaryOptions
   :members: parse

.. autoclass:: next.defer.BoundaryFailure
   :members: of

.. autoclass:: next.defer.DebugBoundaryFailure

.. autoexception:: next.defer.BoundaryOptionError

``next.defer`` defines ``BoundaryOptionError``, as the modules that raise it cannot import ``next.suspense``, and ``next.suspense`` re-exports it beside the other boundary errors.

.. autofunction:: next.defer.parse_duration

.. autofunction:: next.defer.remaining

.. autofunction:: next.defer.deadline

``RESERVED_OPTIONS`` holds ``defer``, ``timeout``, ``reload``, and ``delay``, the keywords a component never takes as a prop.
A component tag reads ``defer`` and ``timeout`` as boundary options, and ``reload`` and ``delay`` belong to ``{% zone %}`` alone, so a component tag that writes either is a template syntax error.
``MAX_DURATION`` is one day in seconds, the longest duration an option takes.

Error boundaries
----------------

``contain(boundary, render, fallback)`` returns what ``render`` returns, or the error UI of ``fallback`` when it raises an exception a boundary contains, and every page and component boundary goes through it.
``contained(exc, started=False)`` answers whether a boundary contains ``exc``, and ``kind_of(exc)`` the ``ErrorKind`` it settles as.

.. autofunction:: next.boundaries.contain

.. autofunction:: next.boundaries.contained

.. autofunction:: next.boundaries.kind_of

.. autofunction:: next.boundaries.render_boundaries

.. autofunction:: next.boundaries.protect

Transactions
------------

.. autoclass:: next.transactions.ViewKind
   :no-special-members:

.. autoclass:: next.transactions.AtomicOverride

.. autoclass:: next.transactions.AtomicScope

.. autofunction:: next.transactions.atomic_scope

.. autofunction:: next.transactions.request_atomic

.. autofunction:: next.transactions.skip_atomic_requests

.. autofunction:: next.transactions.probe_atomic_aliases

.. autoclass:: next.transactions.LazySavepoint
   :members: rollback

.. autofunction:: next.transactions.keep_savepoints

.. autofunction:: next.transactions.safe_aliases

.. autofunction:: next.transactions.statement_kind

``LazySavepoint`` has caveats that follow from the transaction a request is in.
In autocommit, a write in an atomic block opened before the handle's transaction commits with that block, not with the subtree.
A durable block after the first write of the subtree raises the ``RuntimeError`` Django raises, except under a ``TestCase``.
A rollback to the savepoint keeps the ``on_commit`` hooks the subtree added.
Once a write lands in the handle's own transaction, a database error the subtree catches dooms that transaction.

.. py:data:: next.transactions.AtomicMode

   ``True``, ``False``, or ``"writes"``, the value ``DEFER["ERRORS"]["ATOMIC"]`` and the ``atomic`` argument of ``LazySavepoint`` take.
   ``True`` opens a transaction before the first statement of the subtree, ``"writes"`` before its first write, and ``False`` never.

.. py:data:: next.transactions.TransactionRule

   The callable ``TRANSACTIONS["RULE"]`` names, taking the ``HttpRequest`` and the ``ViewKind`` and returning the iterable of database aliases to wrap.

.. autoclass:: next.transactions.StatementKind
   :no-special-members:

Delivery and streams
--------------------

.. autoclass:: next.suspense.StreamPolicy
   :members: delivery, streams_partials

.. autoclass:: next.suspense.DefaultStreamPolicy
   :no-members:

.. autoclass:: next.suspense.DeliveryReason
   :no-special-members:

.. autoclass:: next.suspense.PageFacts
   :no-special-members:

.. autoclass:: next.suspense.Delivered
   :no-special-members:

.. autoclass:: next.suspense.StreamingPageResponse
   :members: settle

.. autoclass:: next.suspense.StreamTransport
   :members: facts

.. autoclass:: next.suspense.DefaultStreamTransport
   :no-members:

.. autoclass:: next.suspense.TransportFacts
   :members: reason
   :no-special-members:

``DEFER["POLICY"]["BACKEND"]`` names the policy class and ``DEFER["TRANSPORT"]["BACKEND"]`` the transport class, which the suspense manager builds with their settings entries.

Lanes
-----

.. autoclass:: next.suspense.BoundaryExecutor
   :members: submit, steal, capacity, shutdown

.. autoclass:: next.suspense.LaneExecutor
   :no-members:

.. autoclass:: next.suspense.Admission
   :members: admit

.. autoclass:: next.suspense.QueueDelay
   :no-members:

.. autoclass:: next.suspense.Capacity
   :no-special-members:

.. autoclass:: next.suspense.Admit
   :no-special-members:

.. autoclass:: next.tasks.StealableTask
   :members: when_claimed, claim, run

``QuitParentExecutor`` of ``next.tasks`` is the executor class a pool thread binds as its current executor, which asgiref reads as a parent to leave alone.

.. autoclass:: next.suspense.connections.ThreadState
   :members: capture, apply, reset

.. autoclass:: next.suspense.connections.ConnectionState
   :members: capture, apply

``DEFER["EXECUTOR"]["BACKEND"]`` names the executor, ``DEFER["ADMISSION"]`` the admission, and ``DEFER["THREAD_STATE"]`` and ``DEFER["CONNECTION_STATE"]`` list the replay backends, each declaring in ``covers`` the detected sources it replays.

Errors
------

``next.suspense.BoundaryOptionError`` is ``next.defer.BoundaryOptionError``, defined there and described above.

.. autoexception:: next.suspense.BoundaryRefusedError
   :no-members:

.. autoexception:: next.suspense.BoundaryShedError
   :no-members:

.. autoexception:: next.suspense.BoundaryDeadlineError
   :no-members:

.. autoexception:: next.suspense.ExecutorStoppedError
   :no-members:

.. autoexception:: next.suspense.BoundaryTokenError
   :no-members:

.. autoexception:: next.suspense.BoundaryPropsError
   :no-members:

.. autoexception:: next.suspense.BoundarySizeError
   :no-members:

.. autoexception:: next.suspense.BoundaryValueError
   :no-members:

.. autoexception:: next.suspense.BoundaryBindingError
   :no-members:

Tokens
------

``DEFER["TOKENS"]["BACKEND"]`` names the token backend, ``next.suspense.StackedTokenBackend`` by default, which stacks a codec, a storage, a seal, a binding, and a transport from its ``OPTIONS``.

.. list-table::
   :header-rows: 1
   :widths: 18 34 48

   * - Option
     - Default
     - Choices
   * - ``CODEC``
     - ``JsonCodec``
     - ``JsonCodec``, ``MsgpackCodec``, or a ``TokenCodec`` subclass.
   * - ``STORAGE``
     - ``InlineStorage``
     - ``InlineStorage`` keeps the claims in the token, ``CacheStorage`` keeps them in the cache, and ``SpillStorage`` keeps them inline until the token outgrows ``MAX_BYTES``.
   * - ``SEAL``
     - ``SignedSeal``
     - ``SignedSeal`` signs without hiding, ``EncryptedSeal`` hides the claims and needs ``cryptography``, and ``NoSeal`` serves ``DEBUG`` alone.
   * - ``BINDING``
     - ``NoBinding``
     - ``NoBinding`` binds a token to nobody, while ``SessionBinding`` and ``UserBinding`` bind it to the session or the user, so another visitor cannot redeem it.
       The short names ``"none"``, ``"session"``, and ``"user"`` select the same classes, and the dotted path of a request callable binds the subject it returns through ``CallableBinding``, which the setting never names itself since it takes that callable.
   * - ``TRANSPORT``
     - ``HeaderTransport``
     - ``HeaderTransport`` sends ``X-Next-Boundary``, ``QueryTransport`` the ``_nb`` query field, and ``BodyTransport`` a ``POST`` form field.

Each name lives in ``next.suspense``, and the values are dotted paths, such as ``"next.suspense.EncryptedSeal"``.
A project class takes the place of a shipped one by subclassing the matching base.
``available()`` of a codec and of a seal is a class method that tells whether the library it needs imports, and a class that answers false mints no token, which ``next.E230`` and ``next.E237`` report.

.. autoclass:: next.suspense.TokenCodec
   :members: available, dumps, loads

.. autoclass:: next.suspense.TokenStorage
   :members: put, get

.. autoclass:: next.suspense.TokenSeal
   :members: encrypts, available, length, seal, open

.. autoclass:: next.suspense.TokenTransport
   :members: extract, describe

.. autoclass:: next.suspense.tokens.context.TokenContext
   :no-special-members:

.. autoclass:: next.suspense.TokenRejection
   :no-special-members:

.. autoclass:: next.suspense.BoundaryTokenBackend
   :no-members:

.. autoclass:: next.suspense.StackedTokenBackend
   :no-members:

Signals
-------

``boundary_failed`` lives in ``next.boundaries``, and ``boundary_rendered``, ``boundary_side_effect``, ``boundary_token_rejected``, ``page_stream_opened``, ``page_stream_closed``, and ``page_stream_declined`` in ``next.suspense``, all re-exported from ``next.signals``, see :doc:`signals`.

Checks
------

``next.suspense.checks`` and ``next.pages.checks.declarations`` report the deferral declarations, the token settings, the bundles, the stream nonce, and the request-lane sources, see :doc:`system-checks`.

See also
--------

.. seealso::

   :doc:`/content/topics/boundaries/index` for the guide.
   :doc:`views` for the views that answer a boundary reload.
   :doc:`deps` for ``RequestScope`` and ``Loader``.
   :repo:`search-catalog <tree/main/examples/search-catalog>` for streamed boundaries and a ``Loader``, and :repo:`observability <tree/main/examples/observability>` for a page boundary and a ``manual`` one, see :doc:`/content/misc/examples`.
