.. _internals-boundary-pipeline:

Deferred boundaries inside
==========================

This page follows one deferred boundary from the component call in a template to the fragment the browser applies.
It names the module that owns each step and the invariants that no single file shows.
Read :doc:`/content/topics/boundaries/index` first for what a boundary is and how a project declares one.

.. contents::
   :local:
   :depth: 2

Where to start
--------------

A contributor who fixes a bug here opens three to five files, and the step that misbehaves tells which.

.. list-table::
   :header-rows: 1
   :widths: 30 70

   * - The symptom
     - The first files
   * - A boundary does not stream
     - ``next/defer/policy.py`` and ``next/defer/assembly/delivery.py``
   * - A boundary never settles
     - ``next/defer/coordinator/machine.py`` and the driver in ``next/defer/coordinator/drivers/``
   * - A boundary shows its error UI
     - ``next/defer/render.py`` and ``next/defer/assembly/outcomes.py``
   * - A frame is wrong or missing in the stream
     - ``next/defer/response/writer.py``, ``chunks.py`` and ``pump.py``
   * - A token is refused
     - ``next/defer/reload.py`` and ``next/defer/tokens/backends.py``
   * - A job sees the wrong database state
     - ``next/defer/connections/guard.py`` and ``next/defer/executors/hygiene.py``

:ref:`internals-boundary-pipeline-debugging` turns each row into a recipe.

Entities and who owns them
--------------------------

Each entity lives in ``next/defer/markers.py`` unless the list says otherwise, and each one is built by the step before it and handed to the step after it.

``DeferredCall``
   Built by the component tag in ``next/components/tags.py`` or the page boundary node in ``next/pages/nodes.py``.
   It names the call site, the options of the boundary, and the renderer that can draw it.
   The tag hands it to the sink bound to ``next.ports.deferrals_slot``, which is the only door into ``next.defer``, so the kernel never imports the feature.
``Deferring``
   The render session of ``next/defer/components.py``, held in one context variable of ``next/boundaries/ids.py``.
   It owns the ``ResponseJobs`` until the render ends, and it carries the page render and the decision of the delivery.
``Job``
   What the sink makes of a call that a pool or a request thread may run later.
   It holds the ``SiteCapture`` of the context at the call site, the ``run`` body, and the ``error_ui`` that draws the error UI without running anything.
``ResponseJobs``
   ``next/defer/ledger.py``.
   The jobs of one response with the facts and captures the view read for them.
   The request thread writes it until the response starts, and every thread only reads it after.
``JobPlan``
   What ``ResponseJobs.plan`` returns to the machine, the jobs in document order with the members of a group kept together, and the calls past ``MAX_INSTANCES`` as expired jobs.
``Report``
   ``next/defer/coordinator/runner.py``.
   How one task ended on a lane, its ``Rendered`` result or the exception, with its timing.
``Outcome``
   The machine's verdict on a job, the rendered content or the error kind it settled as, with the timing and the lane.
   Every job reaches exactly one ``Outcome``, whatever happened to its task.
``Piece``
   An ``Outcome`` taken on the request side by ``Outcomes.take``, with the signals sent.
   It is the fragment of one boundary that a frame carries.
``Writer``, ``Lifetime``, ``StreamSlot``
   ``next/defer/response/``.
   The writer builds the bytes of a batch of pieces, the lifetime owns the slot, the counts and the closing signals of one stream, and the slot is the unit of ``MAX_STREAMS``.

Ownership follows the order of the steps.
``Deferring`` owns the ``ResponseJobs`` until the render ends.
``PageRender`` or ``PartialRender`` of ``next/defer/assembly/`` owns the stream slot until the response is built.
``Lifetime`` owns it until the stream closes.

One driver and one ``Machine`` own one run of the jobs.
The process owns the backends, the pool, and the counters of open streams, see `State`_.

The life of one boundary
------------------------

.. mermaid::

   flowchart TB
       Tag["tag<br/>components/tags.py"] --> Sink["sink<br/>defer/components.py"]
       Sink --> Decide["delivery decision<br/>defer/policy.py"]
       Decide -- "stream" --> Shell["shell with loading UI<br/>defer/assembly/delivery.py"]
       Decide -- "inline, shell, prefetch" --> Inline["assembly/inline.py"]
       Shell --> Coord["coordinator<br/>Machine and a driver"]
       Inline --> Coord
       Coord --> Run["task runner<br/>coordinator/runner.py"]
       Run --> Outcome["Outcome<br/>assembly/outcomes.py"]
       Outcome --> Writer["Writer<br/>response/writer.py"]
       Writer --> Pump["pump<br/>response/pump.py"]
       Pump --> Close["close<br/>response/lifetime.py"]

The tag
~~~~~~~

The component tag finds a call whose component declares a loading UI or a ``defer`` mode and does not render it.
It builds a ``DeferredCall`` and asks the sink to queue it, and what the sink returns is the text the template gets in the call's place.
A client-trip call, one the browser loads later through a token, takes the same road and ends differently, see `Reloading a boundary by its token`_.

The sink
~~~~~~~~

``ComponentDeferrals.queue`` in ``next/defer/components.py`` builds a ``Job`` that holds the context of the call site, and queues it on the ``ResponseJobs``.
A call inside a running job becomes a nested job of that job, counted against ``MAX_INSTANCES`` and ``MAX_DEPTH``, and a call past either renders in place or as its timeout error UI.
It returns the loading wrapper, whose markup ``response/markup.py`` writes and finds again, so the shell always carries a marker for each job it owes.
A page view hands its render ``PageDeferrals`` from ``assembly/delivery.py``, which asks the delivery policy at the first call and then queues like any other sink.
A nested job reaches the stream after its parent, in a write of its own, since its wrapper does not exist before the parent's chunk applies.

The delivery decision
~~~~~~~~~~~~~~~~~~~~~

The policy decides once per request how the response delivers its boundaries.
``DefaultDeliveryPolicy.delivery`` in ``next/defer/policy.py`` answers with a ``DeliveryDecision``, the ``Delivery`` and the row that chose it.

``SHELL``
   The request is a ``HEAD``.
   The response carries the loading UI and runs no job.
``PREFETCH``
   A router prefetch of depth ``shell`` asked for the page without its jobs.
   It is decided before every other row, so it never opens a stream.
``STREAM``
   No row holds the response back.
``INLINE``
   A row holds it back, and every job settles before the response is sent.

The rows of ``DefaultDeliveryPolicy._reason`` read in this order, and the first that holds names the reason.

1. ``prefetch`` holds for a speculative prefetch, which the browser marks in ``Sec-Purpose``.
2. ``method`` holds for a method other than ``GET``.
3. ``noscript`` holds for the ``_nojs`` fallback of a browser without scripts.
4. ``bot`` holds for a user agent the ``BOTS`` pattern matches.
5. ``cache`` holds for a response a shared cache keeps.
6. ``gzip`` holds for a gzip coding from a middleware that buffers the stream.
7. ``buffered`` holds for a server that does not deliver a write as it happens.
8. ``sync_worker`` holds for a worker that serves one request at a time while ``SYNC_WORKERS`` is ``"inline"``.
9. ``runtime`` holds for a page whose client runtime is disabled.
10. ``capacity`` holds for a process at its ``MAX_STREAMS``.
11. ``partial`` holds for a partial request that does not accept an event stream.
12. ``option`` holds while ``STREAM`` is ``False``.

Each row is a ``DeliveryReason``, and the facts they read come from ``delivery_facts``, which gathers them once on the request thread.
The cure for a row of the deployment, such as a buffering middleware, rides with its log record, see `Where to put a breakpoint`_.

The decision is held on ``request.next.defer``, so an error boundary, a second render, or a partial render of the same request never asks twice.
A stream takes its ``StreamSlot`` at the decision.
The render has to know before it defers anything whether to stream or to assemble, and a partial response has no way back to inline once its response object exists.

The shell
~~~~~~~~~

For a stream, ``assembly/delivery.py`` keeps the ``ResponseJobs`` on the ``PageRender`` and returns the shell with the loading wrappers in place.
``prune`` drops the jobs whose wrapper the final HTML no longer holds, so a boundary inside a subtree that failed and discarded its markup never runs.
The shell asks the static collector for the ``boundaries`` bundle that applies the chunks and is split where ``{% collect_scripts %}`` puts its slot.
For an inline delivery, ``assembly/inline.py`` runs the jobs and splices each result into its wrapper before the response is built.

The coordinator
~~~~~~~~~~~~~~~

``Coordinator`` in ``next/defer/coordinator/drivers/facade.py`` runs the jobs of one response.
It asks the thread driver for batches when the response is sync, and the async driver when it is not.
A batch is a tuple of outcomes that settled within ``COMPRESSION["COALESCE"]`` of each other and share one write.
Asking for the next batch acknowledges the last as written, so a consumer that stops early cancels what still runs.

The scheduling lives in ``Machine`` of ``coordinator/machine.py``, which owns no thread, loop, or clock.
It takes events with the driver's monotonic ``now`` and answers with commands.

``start``, ``capacity``, ``tick``, ``finished``, ``written``, ``refused``, ``full``, ``cancel``
   The events a driver feeds it.
``RunPool``, ``RunRequest``, ``Abandon``, ``Emit``, ``Wake``, ``Finish``
   The commands it answers with.
   ``Emit`` is the batch to write, ``Wake`` asks for a ``tick`` at an instant, and ``Finish`` ends the response and carries the totals.

Two drivers execute the commands.
``ThreadDriver`` runs on the request thread, serves request-lane units in the body generator, steals its own queued pool units while it waits, and reports every lane into one inbox.
``AsyncDriver`` runs on the server loop and turns every wait into an await.
``coordinator/drivers/base.py`` holds what both share, the bookkeeping of runs, the counting of loader jobs from admission, and the log that tells a SQLite project its boundaries ran one after another.
``permits.py`` bounds the request-lane work under ASGI across the whole process.

The task runner
~~~~~~~~~~~~~~~

``run`` in ``coordinator/runner.py`` executes one task on the thread a lane gave it.
It skips a task whose deadline has already passed, restores a fresh copy of the snapshot the ``ResponseJobs`` took, and runs the job body inside the deadline and the connection guard of its lane.
``boundary_job_started`` and ``boundary_job_finished`` go out from this thread and this context, so a receiver sees the deadline and the replayed database state.
Whatever the body raises settles the job, so a failing job never takes the thread down.

The outcome
~~~~~~~~~~~

``Outcomes.take`` in ``next/defer/assembly/outcomes.py`` turns an ``Outcome`` into a ``Piece`` on the request side, for an inline delivery and a stream alike.
It sends ``boundary_settled``, reports a failure to ``next.defer``, and notes the writes a job made to the session or the messages, which a streamed response can no longer send as headers.
Every ``Outcome`` is final, and a result that arrives after its job timed out is dropped by the machine.

The writer and the pump
~~~~~~~~~~~~~~~~~~~~~~~

``Writer.feed`` in ``response/writer.py`` returns the writes of one batch, and ``PageWriter`` and the partial writer give it a ``frame``.
A page stream writes a nested chunk apart from its parent's, since the target a parent inserts does not exist before its chunk applies.
A frame that fails to build leaves its piece in place, and ``rescue`` frames the piece again as an ``error`` with its error UI, so one bad fragment never drops its boundary.

``pump`` and ``apump`` in ``response/pump.py`` read the batches, call the writer, and hand the bytes to the server.
``watched`` wraps the async body with a timer per write, and ``Lifetime.stalled`` closes a stream whose client takes nothing for ``WRITE_TIMEOUT``.

The close
~~~~~~~~~

``Writer.closing`` runs on the request thread when the stream ends, releases the connections the jobs left, and after a broken stream frames each boundary no piece was handed over for as an ``error``.
``Lifetime`` gives back the slot, sends ``page_stream_closed`` with the counts, and logs the stream cut by the deadline.
The slot is a handle with its own finalizer, so a response dropped unread or a render that raised gives it back without any holder registering for it.

The invariant of the machine and the writer
-------------------------------------------

``Machine`` and ``Writer`` do no input or output.
They read no clock, start no thread, touch no socket, and open no connection.
The drivers and the pumps are the only code that waits or writes.

The reason is the shape of the failures.
A boundary bug appears in a rare order of events, a result that lands after its timeout, a client that leaves between two batches, a shed job next to a group that ran, a late write after the cut.
A machine fed events by a test reaches any of those orders at once, with no thread to race and no sleep to tune.
The machine's tests assert on commands for arbitrary orders of events, and the driver tests then only prove that a driver executes commands as it was told.
A change that makes the machine read ``time.monotonic()`` or the writer touch the response breaks that split, so keep the new input in an event or a parameter.

The machine also fails loudly on a broken protocol.
A driver that feeds an event out of protocol raises ``ProtocolError`` of ``next/defer/errors.py``.
A job commanded twice, settled twice, or emitted twice raises ``InvariantError``, a ``ProtocolError`` for broken bookkeeping, so a driver bug stops at the call that caused it.

Lanes, admission and deadlines
------------------------------

A job runs on one of two lanes.
The pool lane is the ``BoundaryExecutor`` that ``DEFER["EXECUTOR"]`` names, by default the bounded process-wide ``LaneExecutor`` of ``next/defer/executors/pool.py``.
The request lane is the thread that ran the view, and under ASGI it is one hop onto that thread.
Sync work of a job always runs on a lane, and the ``async def`` callables it meets run through ``next.bridge`` while the job's thread waits, see :doc:`/content/topics/boundaries/async`.

A ``Placement`` says where one job goes and why.
A request-lane rule forces it onto the request thread, in the precedence of ``LaneRule``.
The rules are a transaction around the view, SQLite, per-connection state no backend replays, thread-local state no backend replays, and an opt-out.
A job no rule forces goes to the pool unless the process is busy.

The ``Admission`` that ``DEFER["ADMISSION"]`` names, defined in ``next/defer/admission.py``, decides that from one reading of the pool, ``Capacity``.
The shipped ``QueueDelay`` queues a job while the oldest queued job waits less than ``QUEUE_TIMEOUT``.
It hands the job to its request thread when the delay or the queue length passes its bound, and sheds it once the delay passes ``SHED_DELAY``.
A shed job settles as ``overloaded`` without running.

Each response also holds at most ``PER_REQUEST`` units on the pool at once, so one page cannot take the whole pool.
The machine reads the verdict again whenever a pool slot frees, so a queued unit can move to the request lane later.

Every job has the earlier of its own ``timeout`` and the response ``DEADLINE``.
A task that has not started when its deadline passes is skipped by the runner and settles as ``timeout`` with no run.
A task that is running when it passes settles as ``timeout`` in the machine at once, and the machine counts it as late.
Sync code cannot be interrupted, so the task keeps its thread until it returns, and its result is then dropped.

A stream is cut ``WRITE_GRACE`` seconds past the deadline, and the client marks the boundaries it still owes as incomplete and fetches them again.
The ``Abandon`` command stops the wait for running units and drops the queued pool work no thread claimed yet.

State
-----

Three kinds of state exist, and a fix often belongs to the wrong one.

On the request
   ``RequestState`` in ``next/defer/state.py`` sits on ``request.next.defer``, one slot of the namespace of :ref:`ref-request-next`.
   It holds the delivery decision, the server facts, the URL kwargs a trip token binds, the subjects issued to a binding, the context processor results the jobs share, and the session a render outside a render session counts on.
   Its lazy fields are made once under one lock, whichever thread asks first.
   The kernel types the slot as ``object`` and the feature narrows it, since the kernel never imports the feature.
In context variables
   The render session in ``next/boundaries/ids.py`` follows the context into a template render and across a hop.
   The current job and its deadline, kept in ``next/bridge/deadline.py``, are set around a task, and the error scope of an error boundary lives in ``next.boundaries``.
   ``partial_streams`` tells the init payload whether partial responses may stream.
   A context variable is the right home for what must follow a render through hops that a request attribute cannot reach.
In the process
   The backends of ``defer_manager``, the executor among them, hold process state, and so do the count of open streams, the threads seen under WSGI, the permits of the request lane, and the memo of each log that fires once.
   Each attaches its reset to the ``FORK`` source of ``next.caches``, so a new process counter or cache attaches one too.

Reloading a boundary by its token
---------------------------------

A client-trip boundary is not run during the page response.
``next/defer/trip.py`` mints a token for its wrapper, and the browser sends it back as ``X-Next-Boundary``, in the query, or in a form field, as ``CARRIER`` says.

The request is a ``boundary`` kind of the lifecycle, and ``DeferParticipant`` in ``next/defer/lifecycle.py`` claims it.
The path is short and stops early.

1. The gate admits only the methods the reload answers, refuses a request another site sent, and runs the page access of the URL.
2. ``reload.answer`` reads the boundary ident from the request.
3. ``redeem`` opens the token, finds the component the page resolves its claims to, and settles the claims against the URL, the user, and the binding.
4. ``_authorize`` runs the guards of the page, the permissions the token carries, and the access rule of the component, and decodes the props with the limits of the token.
5. The component renders from its props alone, in an isolated renderer, and the answer is an envelope with the fragment, its assets, and a retry token if the render failed.

Every refusal is one 403 with ``X-Next-Reload: refused``, so the response reveals nothing about which step refused.
An expired token or a stale ``EPOCH`` answers 409, and the client reloads the page once.
The reason goes to ``boundary_token_rejected`` and the log, and only a ``DEBUG`` server explains it in the body.

A token reads ``version.epoch.profile.sealed``.
The ``profile`` names the ``tokens=`` overrides of the component, so a reload finds the right settings with one open and no trial.
The seal is salted with the header and the digest of the bound subject, so an edited header or another visitor fails the seal.
The settled token has to match the overrides of the component it resolves to, so a token minted under weaker settings never opens a stricter component.

The claims travel in the token or in a cache, as the storage says, and props are a strict typed JSON, never pickle.
The seams, the storages, and the bindings are in :doc:`/content/ref/defer`, and the replaceable parts are in :doc:`/content/howto/replace-the-boundary-token-storage`.

Database connections on pool threads
------------------------------------

A pool thread has its own Django connections, so it needs three kinds of care.

Hygiene
   ``executors/hygiene.py`` runs around every task.
   Before it, each connection gets Django's request-start check, closing one that is broken or past ``CONN_MAX_AGE``.
   After it, the execute wrappers return to what they were, a connection the task left inside an atomic block is closed so no next task inherits its transaction, and every other connection is checked again.
   The warning for an abandoned block fires once per alias and pool.
   The request thread gets the same release when the stream closes.
State transfer
   ``connections/guard.py`` replays what the view read before the job.
   A ``ThreadState`` backend captures request-thread state, and a ``ConnectionState`` backend replays per-connection state on the connection the pool thread opens, lazily at its first statement.
   The facts that make a job unsafe for the pool are detected once per process in ``connections/detection.py``, and they become the request-lane rules.
   The replay hooks into the execute wrappers, so ``cursor.callproc`` and the raw driver connection run without it.
The deadline reaching the database
   Under ``DEADLINE_TO_DB="always"`` a PostgreSQL job runs with ``statement_timeout`` and ``idle_in_transaction_session_timeout`` set local to a transaction, from the time its deadline leaves.
   A request-lane job gets the same unless its connection is already inside the transaction of the request, since a local setting would outlive the job.

Request state that lazy objects share, the user, the session, the messages, and the CSRF token, settles on the request thread before a stream starts, in ``connections/settle.py``.
Lazy querysets are copied per job in ``connections/snapshot.py``, so no two jobs evaluate one.

.. _internals-boundary-pipeline-debugging:

Where to put a breakpoint
-------------------------

A boundary does not stream
~~~~~~~~~~~~~~~~~~~~~~~~~~

Run the page with ``DEBUG`` on and read ``X-Next-Stream`` on the response, which names the delivery and the reason.
Break in ``DefaultDeliveryPolicy._reason`` in ``next/defer/policy.py`` and inspect ``DeliveryFacts``, since every row reads one field of it.
If the policy answered ``STREAM`` and the page is still inline, break in ``_decided`` and ``take_slot`` of ``next/defer/assembly/``, where ``MAX_STREAMS`` turns a stream inline as ``capacity``.
If the policy is right and the bytes still arrive together, the server or its proxy is the cause, so read ``next/defer/probe.py`` and the middleware of the project.
The first inline response of each reason logs on the ``next.defer`` logger, and ``page_stream_declined`` reports every one.

A boundary hangs
~~~~~~~~~~~~~~~~

Break in ``Machine.tick`` and ``Machine.finished`` of ``coordinator/machine.py`` and read the entries that are neither ended nor reported.
A job that never started is still queued, so read ``Machine._pump_pool`` and the ``Capacity`` the driver fed it.
A job that started and never reports is blocked in its body, so break in ``run`` of ``coordinator/runner.py`` and read the thread it holds.
A stream that stops writing with all jobs settled is in ``response/pump.py``, where ``watched`` closes a client that takes nothing.
The cut warning on ``next.defer`` and ``page_stream_closed`` with ``cut`` say that the deadline plus ``WRITE_GRACE`` passed.

A boundary fails
~~~~~~~~~~~~~~~~

Break in ``_run`` of ``coordinator/runner.py``, where a raised exception becomes the result, then in ``Outcomes.take`` of ``assembly/outcomes.py`` where it becomes a ``Piece`` and is logged.
The body of the job is ``next/defer/render.py``, so a failure of the component itself is there, and a failure of its error UI is in ``next/boundaries/``.
``boundary_job_finished`` carries the exception and ``boundary_settled`` the settled kind.
A boundary that settles as ``timeout`` or ``overloaded`` has no exception, so read the machine for the verdict.

A token is rejected
~~~~~~~~~~~~~~~~~~~

Break in ``redeem`` of ``next/defer/reload.py`` and step into ``open`` and ``settle`` of ``next/defer/tokens/backends.py``.
The ``TokenRejection`` names the step, ``signature`` before ``expired`` before ``binding``, and the check of the page access and of the guards comes after the token opens, in ``_authorize``.
``announce`` logs every refusal to ``next.defer``, a refusal that is no stale token also to ``django.security.BoundaryTokenError``, and sends ``boundary_token_rejected``.
With ``DEBUG`` on, the body of the 403 or 409 carries the reason and the client prints it with its dev diagnostics.

See also
--------

.. seealso::

   :doc:`request-lifecycle` for the participants that claim a boundary reload and gather the deferred calls of a render.
   :doc:`router-pipeline` for the navigation that streams the boundaries of a page.
   :doc:`/content/topics/boundaries/streaming` for the stream the browser receives.
   :doc:`/content/ref/defer` for the executor, the tokens, and the signals.
   :doc:`/content/ref/settings` for the ``DEFER`` keys.
