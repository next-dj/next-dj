.. _ref-defer:

Deferred boundaries reference
=============================

Module summary
--------------

``next.defer`` runs deferred jobs, decides the delivery of a response, writes a streamed page, and mints the tokens of client-trip boundaries.
Its ``__all__`` lists the extension points and the types their signatures name, and the rest imports by the full path of its module.
``next.defer.remaining()`` is ``remaining`` of ``next.bridge.deadline``, the seconds the current job has left, exported here for the code a job runs, see :doc:`bridge`.
The boundary unit it streams and reloads, the options, the error kinds and the error boundary, lives in ``next.boundaries``, see :doc:`boundaries`, and the transaction rule in ``next.transactions``, see :doc:`transactions`.
:doc:`/content/topics/boundaries/index` is the guide, and the ``DEFER``, ``BOUNDARIES``, and ``TRANSACTIONS`` scopes are in :doc:`settings`.

Declarations
------------

``page.defer(*, timeout=None, parallel=True)`` at the top of a ``page.py`` bounds the job of the page boundary by ``timeout``, and ``parallel=False`` keeps that job on the request thread.

``component.defer(mode="stream", *, timeout=None, props=(), tag=WrapperTag.SITE, parallel=True, tokens=None)`` at the top of a ``component.py`` delivers the component as a boundary in ``mode``.
``props`` names the props a reload or a retry renders from, ``tag`` the wrapper element, ``WrapperTag.SITE`` leaving it to the call site and ``None`` adopting the root of the body, and ``tokens`` overrides ``max_age``, ``binding``, or ``max_bytes`` of ``DEFER["TOKENS"]["OPTIONS"]`` for this component.
``component.props(*names)`` declares the props alone, and naming props through both is an error.

``page.transaction(*, atomic, aliases=None)`` declares the transaction of a page, see :doc:`pages`, and ``page.compress(*, enabled)`` its compression, see :doc:`compression`.
A declaration with a value outside its shape raises :exc:`~django.core.exceptions.ImproperlyConfigured` naming the file, the argument and the values it takes, one inside a function raises ``DeclarationOutsideModuleError``, both when the module runs, and one outside its module kind declares nothing and is reported by ``next.E222`` or ``next.E240``.

Delivery and streams
--------------------

.. autoclass:: next.defer.Delivery
   :no-special-members:

``JOBLESS`` of ``next.defer.markers`` is the set of deliveries that answer the loading UI and run no deferred job, ``SHELL`` and ``PREFETCH``.
A router prefetch names the ``PrefetchDepth`` of ``next.requests`` it asks for, see :doc:`views`.

.. autoclass:: next.defer.DeliveryPolicy
   :members: delivery, streams_partials

.. autoclass:: next.defer.DefaultDeliveryPolicy
   :no-members:

.. autoclass:: next.defer.DeliveryReason
   :no-special-members:

.. autoclass:: next.defer.DeliveryFacts
   :no-special-members:

.. autoclass:: next.defer.ServerKind
   :no-special-members:

``DeliveryFacts.server`` names the server the request arrived through, ``ServerKind.ASGI`` or one of the two WSGI kinds, threaded or not.

.. autoclass:: next.defer.DeliveryDecision
   :no-special-members:

.. autoclass:: next.defer.StreamingPageResponse
   :members: settle

.. autoclass:: next.defer.ServerProbe
   :members: facts

.. autoclass:: next.defer.DefaultServerProbe
   :no-members:

.. autoclass:: next.defer.ServerFacts
   :members: reason
   :no-special-members:

``DEFER["DELIVERY"]["BACKEND"]`` names the policy class and ``DEFER["SERVER"]["BACKEND"]`` the server probe class, which the defer manager builds with their settings entries.
A policy builds the ``DeliveryDecision`` it returns from ``Delivery`` and ``DeliveryReason``, both exported by ``next.defer``.

Lanes
-----

.. autoclass:: next.defer.BoundaryExecutor
   :members: submit, steal, capacity, shutdown

.. autoclass:: next.defer.LaneExecutor
   :no-members:

.. autoclass:: next.defer.Admission
   :members: admit

.. autoclass:: next.defer.QueueDelay
   :no-members:

.. autoclass:: next.defer.Capacity
   :no-special-members:

.. autoclass:: next.defer.Admit
   :no-special-members:

.. autoclass:: next.defer.ThreadState
   :members: capture, apply, reset

.. autoclass:: next.defer.ConnectionState
   :members: capture, apply

``DEFER["EXECUTOR"]["BACKEND"]`` names the executor class, ``next.defer.LaneExecutor`` by default, and its ``OPTIONS`` size the pool.
A project executor subclasses ``BoundaryExecutor``, takes the settings entry in its constructor, and reads its own ``OPTIONS`` from ``self.options``.
``DEFER["ADMISSION"]["BACKEND"]`` names the admission class, ``next.defer.QueueDelay`` by default, and a project one subclasses ``Admission``.

``DEFER["THREAD_STATE"]`` and ``DEFER["CONNECTION_STATE"]`` list the replay backends.
Each replay backend names in ``covers`` the detected sources it replays, by the kind of a source or by its full ``kind:detail`` name.
The kinds are ``routers`` and ``tenant_middleware`` for a ``ConnectionState``, and ``thread_local_middleware`` and the name of a detected library, such as ``django-crum``, for a ``ThreadState``, see :doc:`/content/howto/replay-request-state-on-a-pool-thread`.

Errors
------

``BoundaryOptionError`` lives in ``next.boundaries``, see :doc:`boundaries`.
Every error below derives from ``BoundaryError``, the refusals of a mint from ``BoundaryMintError``, which a project catches to tell a boundary that went out without a token.
A job the pool refuses, sheds, stops, or runs past its deadline never raises to the project, its boundary settles as ``overloaded`` or ``timeout`` instead.

.. autoexception:: next.defer.BoundaryError
   :no-members:

.. autoexception:: next.defer.BoundaryTokenError
   :members: reason, expired, detail

.. autoexception:: next.defer.BoundaryMintError
   :no-members:

.. autoexception:: next.defer.BoundaryPropsError
   :no-members:

.. autoexception:: next.defer.BoundarySizeError
   :no-members:

.. autoexception:: next.defer.BoundaryValueError
   :no-members:

.. autoexception:: next.defer.BoundaryBindingError
   :no-members:

.. autoexception:: next.defer.BoundaryStorageError
   :no-members:

Tokens
------

``DEFER["TOKENS"]["BACKEND"]`` names the token backend, ``next.defer.StackedTokenBackend`` by default, which stacks a storage, a seal, a binding, and a carrier from its ``OPTIONS``.

.. list-table::
   :header-rows: 1
   :widths: 18 34 48

   * - Option
     - Default
     - Choices
   * - ``STORAGE``
     - ``InlineStorage``
     - ``InlineStorage`` keeps the claims in the token, ``CacheStorage`` keeps them in the cache, and ``SpillStorage`` keeps them inline until the token outgrows ``MAX_BYTES``.
   * - ``SEAL``
     - ``SignedSeal``
     - ``SignedSeal`` signs with Django's ``Signer``, compressed when that saves room, and ``EncryptedSeal`` hides the claims under ``MultiFernet`` and needs ``cryptography``.
   * - ``BINDING``
     - ``"auto"``
     - ``"auto"`` binds the user ``AuthenticationMiddleware`` sets on the request and nothing on a request without one, ``"none"`` binds a token to nobody, and ``"session"`` and ``"user"`` bind it to the session or the user, so another visitor cannot redeem it.
       An anonymous visitor proves one subject to ``"auto"`` and ``"user"``, so a shell minted for anonymous visitors stays shareable, while a token bound to a signed-in user makes the response private.
       A binding tells this through ``SubjectBinding.binds``, false for one that ties nothing, and ``SubjectBinding.shared``, the subjects many visitors prove alike.
       The dotted path of a ``SubjectBinding`` subclass selects a binding of the project, and the dotted path of a request callable binds the subject it returns.
   * - ``CARRIER``
     - ``"header"``
     - ``"header"`` sends ``X-Next-Boundary``, ``"query"`` the ``_nb`` query field, and ``"body"`` a ``POST`` form field, the three places the client runtime knows.

The storage, the seal and the binding are dotted paths, such as ``"next.defer.EncryptedSeal"``, and a project class takes the place of a shipped one by subclassing ``TokenStorage``, ``TokenSeal`` or ``SubjectBinding``.
A token reads ``version.epoch.profile.sealed``, where the profile names the ``tokens=`` overrides of the component it was minted for, ``0`` for none.
The seal is salted with the header and the digest of the bound subject, so an edited header fails the seal, and a reload opens a token only under the overrides it names and settles it only for a component that declares the same ones.
Every seal signs or derives its key from ``SECRET_KEY``, and a token minted before a rotation opens while its key stays in ``SECRET_KEY_FALLBACKS``.

Seal and storage hooks
~~~~~~~~~~~~~~~~~~~~~~

``available()`` of a seal is a class method that tells whether the library it needs imports, and a seal that answers false mints no token, which ``next.E230`` reports.
``inline`` of a storage tells that every token carries its claims, so ``MAX_BYTES`` caps a prop rather than ``MAX_STORED_BYTES``.
``overflow`` answers the reference of claims whose inline token outgrew ``MAX_BYTES``, as ``SpillStorage`` does with the cache.
``protects`` of a seal is false for one a client can forge, which ``next.E221`` refuses while ``DEBUG`` is off.

A storage on a cache turns a failed cache call into a refusal, so a mint renders that boundary without a token and a reload answers ``409`` with ``storage_down``.
``next.testing.NoSeal`` seals nothing, so a test reads the claims of its tokens.

.. autoclass:: next.defer.BoundaryClaims
   :no-members:

.. autoclass:: next.defer.BoundaryTarget
   :no-members:

A storage and a seal carry ``BoundaryClaims``, what one token proves, and ``settle`` of a token backend holds them against the ``BoundaryTarget`` the server resolved a reload to.

.. autoclass:: next.defer.TokenStorage
   :members: inline, put, get, overflow

.. autoclass:: next.defer.StorageTerms
   :no-special-members:

.. autoclass:: next.defer.TokenSeal
   :members: protects, available, seal, open

.. autoclass:: next.defer.TokenRejection
   :no-special-members:

.. autoclass:: next.defer.SubjectBinding
   :members: issue, bound, binds, shared, needs_sessions

A token backend implements ``issue``, ``open``, and ``settle``, the three methods the core calls, and inherits the rest.
``redeem`` opens and settles in one call for a test, and ``announce`` sends ``boundary_token_rejected`` through ``send_robust`` and logs the refusal to ``next.defer``, a refusal that is no stale token also to ``django.security.BoundaryTokenError``.

.. autoclass:: next.defer.BoundaryTokenBackend
   :members: issue, open, settle, redeem, announce, bound, profile, prop_limit, validate, carrier

.. autoclass:: next.defer.StackedTokenBackend
   :no-members:

Signals
-------

``boundary_failed`` lives in ``next.boundaries``, and the other boundary signals in ``next.defer``, all re-exported from ``next.signals``, see :doc:`signals`.
Those are ``boundary_job_started``, ``boundary_job_finished``, ``boundary_settled``, ``boundary_state_written``, ``boundary_token_rejected``, ``page_stream_opened``, ``page_stream_closed``, and ``page_stream_declined``.
``defer_backend_loaded`` is sent once per ``DEFER`` backend the manager builds, from ``EXECUTOR``, ``ADMISSION``, ``DELIVERY``, ``SERVER``, or ``TOKENS``.
``boundary_job_started``, ``boundary_job_finished``, ``boundary_settled``, and ``boundary_state_written`` are sent by the ``Job`` of one deferred boundary, and ``page_stream_opened`` and ``page_stream_closed`` of a partial response by its ``PartialStream``.

.. autoclass:: next.defer.Job
   :no-members:

.. autoclass:: next.defer.PartialStream
   :no-members:

Checks
------

``next.defer.checks`` reports the deferral declarations, the token settings, the bundles, the stream nonce, and the request-lane sources, see :doc:`system-checks`.

See also
--------

.. seealso::

   :doc:`/content/topics/boundaries/index` for the guide.
   :doc:`views` for the views that answer a boundary reload.
   :doc:`deps` for ``Loader``.
   :repo:`search-catalog <tree/main/examples/search-catalog>` for streamed boundaries and a ``Loader``, and :repo:`observability <tree/main/examples/observability>` for a page boundary and a ``manual`` one, see :doc:`/content/misc/examples`.
