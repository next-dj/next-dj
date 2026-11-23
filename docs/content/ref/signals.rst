.. _ref-signals:

Signals reference
=================

Module summary
--------------

``next.signals`` is an aggregator that re-exports every framework signal.
Importing a signal from ``next.signals`` is equivalent to importing it from its subpackage.
Receivers connect through Django's :func:`~django.dispatch.receiver`, and :ref:`topics-signals` shows how a receiver waits for the commit.

Signal catalog
--------------

Every signal below is a Django ``Signal``.
The ``sender`` column lists the value passed to ``Signal.send``.
Receivers connected with a matching ``sender`` only fire for that sender.

A receiver that raises behaves differently by signal, and :ref:`topics-signals` states the failure contract.

Most of the catalog is built with ``use_caching=True``, which makes Django key the receiver lookup on a weak reference to the sender.
Code that sends one of those signals itself has to pass a weak-referenceable sender, so ``None``, a string, and an instance of a slots class without ``__weakref__`` all raise ``TypeError``.
The realtime signals ``message_published``, ``upstream_received``, ``upstream_rejected``, ``stream_subscribed``, and ``stream_unsubscribed`` accept a sender without ``__weakref__``, because their receiver cache skips such a sender and looks its receivers up again.
Four signals stay uncached and accept any sender.
``asset_registered`` sends the ``StaticAsset`` itself, which carries no ``__weakref__``, ``collector_finalized`` sends a collector built for a single render, and ``provider_registered`` and ``settings_reloaded`` fire rarely enough that the weak-key bookkeeping buys nothing.

The dispatch-time form signals (``action_dispatched``, ``form_validation_failed``, ``wizard_step_submitted``, ``wizard_completed``, ``form_access_denied``) share two keyword arguments.
``uid`` is the registry identity of the action, the value the dispatch URL and the ``data-next-action`` markup attribute carry, or ``None`` when a custom backend stores no uid in its meta.
``request`` is the live ``HttpRequest`` being dispatched and must not be retained past the receiver call.

Signals announce backends the framework builds from settings.
The shared-loader signals send the resolved class as the sender and carry ``config``, a copy of the settings entry, and ``instance``, the object the loader built from it.
``broker_backend_loaded`` uses the same payload shape for the selected realtime broker.
Each family owns its own signal rather than sharing one, so a receiver connected with ``sender=`` does not have to sort one family's classes out of another's.
``router_backend_loaded`` and ``component_backend_loaded`` are skipped on a reload that asks not to notify, which is what a caller reloading from inside a receiver passes.

.. list-table::
   :header-rows: 1
   :widths: 18 18 34 30

   * - Signal
     - Sender
     - Keyword arguments
     - When it fires
   * - ``action_dispatched``
     - ``FormActionDispatch``
     - ``action_name``, ``uid``, ``request``, ``form``, ``url_kwargs``, ``duration_ms``, ``response_status``, ``dep_cache``
     - After an action handler runs and the response is coerced, and once per valid wizard step.
       ``form`` is the bound form, or ``None`` for handler-only actions.
       ``duration_ms`` times the handler call and is ``0.0`` on a wizard step advance, which runs no handler.
       ``dep_cache`` is a copy of the dispatch dependency-injection cache.
   * - ``action_registered``
     - Form action backend class
     - ``action_name``, ``uid``, ``form_class``, ``wizard_class``, ``file_path``, ``scope``, ``handler``
     - After the backend stores an action target for a name.
       Exactly one of ``handler``, ``form_class``, or ``wizard_class`` identifies the target, except the ``@action(form_class=...)`` path which supplies a handler and a form factory together.
       ``file_path`` is the module the form, wizard, or handler was declared in and ``scope`` is ``"page"`` or ``"shared"``, which together give a receiver a grouping key under the file-scoped model.
   * - ``asset_registered``
     - The ``StaticAsset`` instance
     - ``collector``, ``backend``
     - After a file is registered with a backend and added to the collector.
   * - ``boundary_failed``
     - The ``next.boundaries.contain`` function
     - ``id``, ``name``, ``addressing``, ``kind``, ``digest``, ``exception``, ``request``
     - Once per failure an error boundary contained, after it was logged or handed to ``ERRORS["REPORTER"]`` and before its ``error.djx`` renders.
       ``addressing`` is ``"component"`` for a component boundary and ``"page"`` for a page boundary, ``kind`` is the ``ErrorKind`` before ``ERRORS["KINDS"]`` hides it, and ``id`` is the boundary id its wrapper carries.
       ``name`` is the name of the component for a component boundary.
       A page boundary is named by the path of its ``loading.djx`` when it has one, streamed or rendered inline, and by the path of its ``error.djx`` otherwise, relative to ``BASE_DIR`` and absolute only for a file outside it, so ``boundary_rendered`` and ``boundary_gate(page=...)`` name it the same way.
       ``request`` may be ``None`` outside a request.
       A deferred job that raised past its own boundary is announced the same way once its error UI renders, with the wire id of its wrapper.
   * - ``boundary_rendered``
     - ``next.suspense.Job``
     - ``id``, ``name``, ``addressing``, ``mode``, ``state``, ``kind``, ``lane``, ``placement``, ``reason``, ``queue_ms``, ``run_ms``, ``request``
     - Once per deferred job of a response, on the request thread, as its outcome is spliced into the page, a nested job after its parent.
       ``state`` is ``"ready"`` or ``"error"``, ``kind`` the ``ErrorKind`` of an error and ``None`` otherwise, ``lane`` the ``Lane`` the job ran on and ``None`` for one that never started.
       ``placement`` is the ``Placement`` that chose the lane and ``reason`` its rule or verdict, such as ``atomic``, ``pool``, or ``foreground-full``.
       ``queue_ms`` is the time the job waited for its lane and ``run_ms`` the time its body ran.
       ``request`` may be ``None`` for a render outside a request.
   * - ``boundary_side_effect``
     - ``next.suspense.Job``
     - ``id``, ``kind``, ``request``
     - Once per kind of request state a deferred job wrote, beside a warning on the ``next.suspense`` logger.
       ``kind`` is ``"session"`` for a session write, ``"session-key"`` for a new session key, ``"messages"`` for messages read and marked used, and ``"csrf"`` for a CSRF secret the job rotated, as ``rotate_token()`` does.
       Jobs share the request, so such writes are unsupported, and the signal names the job that made them.
   * - ``boundary_token_rejected``
     - The token backend class
     - ``reason``, ``request``, ``component``
     - Once per refused request, garbage included, beside a log record on the ``next.suspense`` logger at a bounded rate, so a receiver must stay cheap or it amplifies a flood.
       ``reason`` is a ``TokenRejection``, one of ``malformed``, ``signature``, ``expired``, ``version``, ``binding``, ``url_mismatch``, ``too_large``, and ``storage_miss``, and ``component`` the component the server resolved the request to.
       The client is told only whether the token expired, so the reason reaches logs and receivers alone.
   * - ``collector_finalized``
     - The static collector
     - ``page_path``, ``request``
     - When the static manager begins injection, after template rendering completes.
       ``page_path`` is the file path of the rendered page.
       A standalone zone render does not fire this signal.
       ``request`` may be ``None`` outside a request.
   * - ``component_backend_loaded``
     - The component backend class
     - ``config``, ``instance``
     - After a component backend is created from its ``COMPONENT_BACKENDS`` entry.
       A ``reload(notify=False)`` builds the backends without sending it.
   * - ``component_registered``
     - ``ComponentRegistry``
     - ``info``
     - After a single component is registered.
       Not fired from the bulk path.
   * - ``component_rendered``
     - ``ComponentsManager``
     - ``info``, ``template_path``
     - After a component is rendered to HTML.
       ``template_path`` may be ``None`` for components without a template file.
   * - ``broker_backend_loaded``
     - The realtime broker class
     - ``config``, ``instance``
     - After the configured broker is created on first use.
       The manager lock is already released, so a receiver may read the broker again from any thread.
   * - ``message_published``
     - The realtime broker class
     - ``topic``, ``message``, ``ephemeral``
     - After the broker accepts a publication.
       ``ephemeral`` is ``True`` for an unnumbered message, a presence snapshot, whose ``message.numbered`` is ``False`` and which never enters the replay log.
   * - ``upstream_received``
     - The handler of the matched ``@on``
     - ``upstream``
     - Before the handler of an ``up`` frame runs, once the topic grant and the access recheck passed.
       ``upstream`` is the ``Upstream`` the handler gets, and a raising receiver is logged while the handler still runs.
   * - ``upstream_rejected``
     - ``type(socket)``, so ``TopicSocket``
     - ``topic``, ``name``, ``reason``, ``connection``, ``subject``
     - When an ``up`` frame is not delivered.
       Every reason but ``limit`` is reported before the ``ack`` goes out, while a ``limit`` refusal sends its ``ack`` or ``error`` frame first and reports from a task.
       ``reason`` is the ``UpstreamError`` the ack carries, ``limit``, ``too_big``, ``topic``, ``denied``, ``unknown``, ``timeout``, or ``internal``, and an event without an ``id`` is reported too.
       ``topic`` and ``name`` are what the client sent, unchecked, so a receiver that labels metrics with them bounds their values first.
       ``connection`` and ``subject`` are the ``Upstream.connection`` and ``Upstream.subject`` a handler would have seen.
   * - ``stream_subscribed``
     - ``type(connection)``, so ``TopicStream`` or ``TopicSocket``
     - ``transport``, ``topics``, ``connection``, ``request``, ``subject``, ``user``
     - Right after a stream enters its broker subscription.
       ``transport`` is ``"sse"`` or ``"ws"``, ``topics`` the tuple of topics the subscription joined, and ``connection`` the ``TopicStream`` or ``TopicSocket`` itself.
       An SSE stream carries its ``HttpRequest``, the ``user`` of that request, which is ``None`` without ``AuthenticationMiddleware``, and a ``subject`` of ``None``.
       When the request has a session key and its user is the lazy user of Django's ``AuthenticationMiddleware``, still unloaded, the stream swaps it for the user of the stored session on the request too, so a receiver reading ``request.user`` never loads Django's, which may rotate the session key.
       A lazy user set by another middleware stays untouched.
       A WebSocket carries the opaque ``subject`` and the ``user`` of its handshake and a ``request`` of ``None``.
       No argument carries the credential, though ``connection`` is the ``TopicSocket``, so server code reaches ``connection.handshake.credential`` only on purpose.
       The request must not be retained past the receiver call.
   * - ``stream_unsubscribed``
     - ``type(connection)``, so ``TopicStream`` or ``TopicSocket``
     - ``transport``, ``topics``, ``connection``, ``duration``, ``reason``
     - When a stream that opened while a receiver listened to either lifecycle signal leaves its topics, once per stream.
       ``duration`` is the seconds the subscription stayed open on the monotonic clock and ``reason`` an ``UnsubscribeReason``.
   * - ``components_registered``
     - ``ComponentRegistry``
     - ``infos``
     - After a batch of components is registered.
       ``infos`` is the tuple of added components.
   * - ``compression_backend_loaded``
     - The codec backend class
     - ``config``, ``instance``
     - After one ``COMPRESSION["BACKENDS"]`` entry is instantiated, when the compression manager first needs its codecs and again once the ``COMPRESSION`` mapping changes.
       A path that names no backend is skipped without it.
   * - ``consent_backend_loaded``
     - The consent backend class
     - ``config``, ``instance``
     - After the single ``CONSENT`` backend is built, on the first read of it and again after a settings reload drops the cached one.
   * - ``context_registered``
     - ``PageContextRegistry``
     - ``file_path``, ``key``
     - After a context function is attached to a page module.
   * - ``field_validated``
     - The active partial protocol backend class
     - ``action_name``, ``uid``, ``request``, ``field_names``, ``error_count``
     - After a validate-only blur pass runs, always behind the action guard so unauthenticated validate traffic never reaches telemetry.
       ``field_names`` is the validated subset.
       ``error_count`` is the number of error messages that survived the scrub, across every row of a formset.
   * - ``form_access_denied``
     - ``FormActionDispatch``
     - ``action_name``, ``uid``, ``request``, ``layer``, ``reason``
     - When the access rule of the page tree, the origin page, a dynamic permission hook, or a registered permission checked on the bound form denies a request, never on the static guard path.
       ``layer`` is ``"page"``, ``"view"``, or ``"object"``, and a registered permission refused on the bound form is ``"object"`` with ``"denied"``.
       ``reason`` is ``"raised"``, ``"denied"``, or ``"response"``, and a ``"page"`` denial is ``"denied"`` for a refused access rule or an origin outside the directory of an owner page with a ``render()``, a route with no page included, and ``"response"`` for a ``render()`` that answered.
       It is sent outside any transaction of the dispatch, once a refusal on the bound row has rolled back, so a receiver's writes and its :func:`~django.db.transaction.on_commit` hooks persist.
   * - ``form_backend_loaded``
     - The form action backend class
     - ``config``, ``instance``
     - After one ``FORM_ACTION_BACKENDS`` entry is instantiated, once per entry on every manager reload.
   * - ``form_validation_failed``
     - ``FormActionDispatch``
     - ``action_name``, ``uid``, ``request``, ``error_count``, ``field_names``
     - When the bound form fails validation during dispatch.
   * - ``html_injected``
     - A ``StaticManager`` instance
     - ``html_before``, ``html_after``, ``collector``, ``placeholders_replaced``, ``injected_bytes``, ``request``
     - After placeholder replacement completes.
       ``placeholders_replaced`` is the tuple of replaced slot names.
       ``injected_bytes`` is the length delta.
   * - ``intercept_access_denied``
     - ``Intercept``
     - ``file_path``, ``target_file``, ``request``, ``status``, ``reason``
     - When the access chain of an intercept page refuses a zone GET over its target, after the refusal is rendered.
       ``file_path`` is the page of the intercept and ``target_file`` the ``page.py`` of the target.
       ``status`` is the status code of the answer, and ``reason`` is ``"raised"`` for a ``PermissionDenied`` or an ``Http404`` the project handler rendered and ``"redirect"`` for a login redirect.
       A refusal on the form or action path raises ``PermissionDenied`` instead and sends nothing.
   * - ``intercept_registered``
     - ``FileRouterBackend``
     - ``target_file``, ``file_path``, ``marker``
     - Once per intercept rule, after the file router publishes the rules of a page tree to the intercept table.
       ``target_file`` is the ``page.py`` of the routed page the rule sits over, ``file_path`` is the page of the intercept, and ``marker`` is the prefix of its directory name, ``(.)``, ``(..)`` repeated once per level, or ``(...)``.
       A walk that began before a router reload and finished after it publishes nothing and sends nothing.
   * - ``metadata_registered``
     - ``PageMetadataRegistry``
     - ``file_path``, ``inherit``
     - After a ``@page.metadata`` callable is attached to a page module.
       ``inherit`` is the flag the registration carried.
   * - ``navigation_rendered``
     - ``next.router.navigation.Routing``
     - ``page_path``, ``purpose``, ``depth``, ``cut``, ``kept``, ``diverged_at``, ``reason``, ``streamed``, ``request``
     - Once per client router navigation or prefetch the page view rendered and answered with an envelope, never for one refused or answered before any render.
       ``purpose`` is ``navigation`` or ``prefetch``, ``depth`` is the ``Depth`` of the prefetch, ``shell`` or ``full``, and ``None`` for a navigation, and ``cut`` is ``slot``, ``body`` or ``visit``, and ``kept`` the ids of the layouts kept on screen, outer first.
       ``diverged_at`` names the first layout that did not match, ``reason`` why, ``id``, ``digest``, ``head`` or ``declaration``, or ``clear`` for a response that clears the router and so keeps no layout, and both are ``None`` when every layout matched.
       ``streamed`` is ``True`` when deferred boundaries follow the envelope as an event stream.
   * - ``page_rendered``
     - ``Page``
     - ``file_path``, ``duration_ms``, ``styles_count``, ``scripts_count``, ``context_keys``, ``purpose``, ``depth``
     - After ``Page.render`` produces HTML and injects static assets.
       ``duration_ms`` times the render.
       ``context_keys`` is the tuple of context keys.
       ``purpose`` is the ``Purpose`` of ``request.next``, ``document``, ``navigation``, ``prefetch`` or ``speculation``.
       ``depth`` is the ``Depth`` of ``request.next``, ``shell`` or ``full`` for a router prefetch and ``None`` for any other request.
       ``depth`` does not count visits, so see :ref:`topics-router-prefetch-visits` for how to count them.
       Fired only when a receiver for ``Page`` is connected, and the ``duration_ms`` timer runs under the same gate.
   * - ``page_stream_declined``
     - ``next.suspense.StreamPolicy``
     - ``delivery``, ``reason``, ``transport_reason``, ``partial``, ``request``
     - Once per page or partial response whose deferred jobs are assembled inline, as the first deferred call of the render is decided.
       ``delivery`` is ``Delivery.INLINE``, ``reason`` the row of the delivery policy that held the stream back, such as ``buffered`` or ``sync-worker``, and ``transport_reason`` the fact behind the answer of the transport, such as ``wsgi.multithread`` or a buffering mark.
       A ``HEAD`` request, a shell prefetch, and a streamed response send nothing.
       Fired only when a receiver for ``StreamPolicy`` is connected.
   * - ``page_stream_opened``
     - ``next.suspense.StreamingPageResponse``, or ``next.suspense.PartialStream`` for a partial response
     - ``page_path``, ``delivery``, ``reason``, ``boundaries``, ``request_lane``, ``partial``, ``request``
     - Once per streamed page or streamed partial response, when the server starts reading its body and before the first byte is written.
       A response closed or dropped before its body starts sends neither this signal nor ``page_stream_closed``.
       ``partial`` is ``True`` for a zone, poll, layer or boundary reload answered as an event stream.
       ``delivery`` is ``Delivery.STREAM`` and ``reason`` the policy's reason for it.
       ``boundaries`` is a tuple of ``(name, mode)`` pairs, one per boundary of the shell, the pairs the ``X-Next-Boundaries`` debug header lists.
       ``request_lane`` is the request-lane rule that pins a job of the page, such as ``atomic``, and ``None`` when no rule does.
       It is read as the body starts, once the transactions the view closed have committed, so it names the lane the jobs run on.
       Under ASGI it is sent through ``asend_robust`` from the server loop, so a synchronous receiver runs on the request thread and an async one on the loop.
   * - ``page_stream_closed``
     - ``next.suspense.StreamingPageResponse``, or ``next.suspense.PartialStream`` for a partial response
     - ``page_path``, ``sent``, ``failed``, ``late``, ``shed``, ``duration_ms``, ``aborted``, ``partial``, ``request``
     - Once per streamed page or streamed partial response when its body ends, after the closing tags or the ``end`` event are written or when the reader left.
       ``sent`` counts the chunks or boundary frames written and ``failed`` those settled as an error, ``late`` and ``shed`` count the jobs that missed the deadline or were shed.
       ``aborted`` is ``True`` when the reader left before the end or the jobs were cancelled.
       Under ASGI it is sent through ``asend_robust`` from the server loop, unless the server closed the response from a thread, which sends it there.
       The response owns the stream slot counted against ``MAX_STREAMS`` from the moment the view admits it, and releases it exactly once, as the body ends, as the response closes, or as a response never read is garbage collected.
   * - ``partial_backend_loaded``
     - The partial protocol backend class
     - ``config``, ``instance``
     - After the single configured protocol backend is built, on the first read of it and again after a settings reload drops the cached one.
   * - ``patch_op_registered``
     - ``PatchOpRegistry``
     - ``name``
     - After a custom patch verb is registered through ``register_patch_op``.
   * - ``prefetch_shed``
     - ``next.router.navigation.Routing``
     - ``page_path``, ``waited_ms``, ``request``
     - Once per client router prefetch answered ``429`` before any render, because the oldest job in the pool queue waited past ``QUEUE_TIMEOUT``.
       ``waited_ms`` is that wait.
   * - ``provider_registered``
     - The ``RegisteredParameterProvider`` subclass
     - none
     - When a ``RegisteredParameterProvider`` subclass is added to the auto-registry.
   * - ``route_registered``
     - ``FileRouterBackend``
     - ``url_path``, ``file_path``
     - After a URL pattern is created for a discovered page.
   * - ``router_backend_loaded``
     - The router backend class
     - ``config``, ``instance``
     - After one ``PAGE_BACKENDS`` entry is instantiated, once per entry on every manager reload.
       A ``reload(notify=False)`` builds the backends without sending it.
   * - ``router_cleared``
     - ``next.router.navigation.Routing``
     - ``reason``, ``request``
     - Once per request whose response tells the client router to drop its snapshots and its prefetched answers with ``X-Next-Router: clear``, sent from ``router_clear`` even while the router is off.
       ``reason`` is ``login`` or ``logout`` from the auth signals, ``session`` for a session key that changed during the request, or what ``router_clear(request, reason)`` passed, ``policy`` by default.
   * - ``router_reloaded``
     - The router manager class
     - none
     - After the router manager rebuilds its pattern set.
   * - ``scripts_registered``
     - ``ScriptsRegistry``
     - ``root``, ``source``, ``scripts``
     - When the scripts manager registers the ``scripts.py`` of a page tree, on discovery, after a reload, and again when the file changes under ``DEBUG``.
       ``source`` is the ``ScriptsSource``, with the ``path`` of the file and any import ``error``, and ``scripts`` its tuple of ``Script`` values.
       A tree without a ``scripts.py`` sends nothing.
   * - ``settings_reloaded``
     - ``NextFrameworkSettings``
     - none
     - After ``NextFrameworkSettings.reload`` drops its caches.
       Every receiver runs even when one raises, so a receiver that validates a settings value never leaves the managers behind it holding state built from the settings that reload discarded.
       The first error reaches the caller that asked for the reload once the chain is done.
   * - ``sitemap_backend_loaded``
     - The sitemap backend class
     - ``config``, ``instance``
     - After one ``SEO["SITEMAP_BACKENDS"]`` entry is instantiated, once per entry on every manager reload.
   * - ``sse_stream_closed``
     - The concrete stream class
     - ``request``, ``duration_ms``, ``envelopes_sent``
     - When a patch event stream ends, its source exhausted or the client gone.
       ``envelopes_sent`` counts envelope frames yielded to Django's response iterator.
       It does not confirm network delivery after a disconnect.
       A streamed partial response sends ``page_stream_closed`` instead.
   * - ``sse_stream_opened``
     - The concrete stream class
     - ``request``
     - When iteration starts and the first stream frame is about to be sent.
       A streamed partial response sends ``page_stream_opened`` instead.
   * - ``static_backend_loaded``
     - The static backend class
     - ``config``, ``instance``
     - After one ``STATIC_BACKENDS`` entry is instantiated.
       An empty setting falls back to one default entry, which is announced the same way.
   * - ``template_loaded``
     - ``Page``
     - ``file_path``
     - After a template source is registered on a page.
   * - ``watch_specs_ready``
     - ``iter_all_autoreload_watch_specs``
     - ``specs``
     - After the reloader resolves the full list of watch specs.
   * - ``wizard_backend_loaded``
     - The wizard backend class
     - ``config``, ``instance``
     - After the single ``FORM_WIZARD_BACKEND`` entry is built, on the first read of it and again after a settings reload drops the cached one.
   * - ``wizard_completed``
     - The wizard class
     - ``cleaned_data``, ``uid``, ``request``
     - After the wizard ``done`` method runs for the final step and its response is below HTTP 400.
       An error response from ``done`` skips the signal and keeps the saved drafts.
       ``cleaned_data`` is the merged mapping passed to ``done``.
   * - ``wizard_step_submitted``
     - The wizard class
     - ``step``, ``cleaned_data``, ``uid``, ``request``
     - After a ``FormWizard`` step validates during dispatch.
       ``cleaned_data`` is a copy of that step's validated data.
   * - ``zone_registered``
     - The compiled page template class
     - ``template``, ``zone_name``, ``poll``, ``reload``
     - Once per zone of a compiled composed template, when its named zones are first read.
       ``poll`` is the interval in milliseconds or ``None``, ``reload`` is ``"keep"`` or ``"loading"``.
   * - ``zone_rendered``
     - ``ZoneRenderResult``
     - ``zone_name``, ``page_path``, ``request``, ``duration_ms``
     - After a zone body renders for a partial request.
       ``duration_ms`` times the render of that zone's body alone, without the context build, and the timer runs only while a receiver is connected.

.. _ref-signals-stream-lifecycle:

Stream lifecycle
----------------

A stream announces itself once it holds a broker subscription and again when it lets go, over SSE and over a WebSocket alike.
The pair brackets the life of one ``TopicStream`` response or one ``TopicSocket``, so a receiver counts live streams, times their length, or tells how they ended.
A stream sends ``stream_unsubscribed`` at most once, and only if it opened while a receiver listened to ``stream_subscribed`` or ``stream_unsubscribed``.
A receiver of ``stream_unsubscribed`` alone therefore hears every stream that opened after it connected.
A receiver connected while a stream is open never sees that stream close, and a counter it keeps never drops below zero when it listens to both signals from the start.
A stream refused before it subscribes, by a failed ticket or a denied topic, sends neither signal.
Nothing is sent or timed while no receiver listens.

``reason`` is a member of ``UnsubscribeReason``, a ``StrEnum`` that compares equal to its string.

.. list-table::
   :header-rows: 1
   :widths: 16 42 42

   * - Reason
     - SSE
     - WebSocket
   * - ``client``
     - The browser disconnected.
       Django cancels the response on ``http.disconnect``, and a cancellation reads as the client leaving.
     - The peer closed, stopped reading, or a send failed.
   * - ``cancelled``
     - Never used.
     - The serving task was cancelled.
   * - ``revoked``
     - The session or the topic grant failed a recheck.
     - The socket closed with ``4401`` or ``4403``.
   * - ``error``
     - The broker connection failed or a stage raised.
     - The broker connection or the transport failed, or a stage raised.
   * - ``server``
     - The broker subscription ended on its own.
     - The broker subscription ended, or the socket closed for a protocol violation, a rate limit, or an oversized frame.

The receiver below keeps the number of live streams per transport in each process.
Connect it from ``AppConfig.ready`` by importing the module there.

.. code-block:: python
   :caption: observability/streams.py

   import logging
   import threading
   from collections import Counter

   from django.dispatch import receiver

   from next.realtime.signals import UnsubscribeReason
   from next.signals import stream_subscribed, stream_unsubscribed

   logger = logging.getLogger(__name__)
   live: Counter[str] = Counter()
   lock = threading.Lock()

   @receiver(stream_subscribed)
   def stream_opened(sender, transport, topics, **kwargs) -> None:
       with lock:
           live[transport] += 1

   @receiver(stream_unsubscribed)
   def stream_closed(sender, transport, topics, duration, reason, **kwargs) -> None:
       with lock:
           live[transport] -= 1
       if reason is UnsubscribeReason.ERROR:
           logger.warning("stream on %s failed after %.1fs", topics, duration)

A receiver of a WebSocket or of an async stream runs on the event loop when it is async, and a synchronous one runs on a worker thread, so a receiver shared by both guards its state with a lock as above.
A receiver that raises an ``Exception`` does not end the stream, because both signals go out with ``send_robust``, as do ``message_published``, ``upstream_received``, and ``upstream_rejected``.
The error is logged at ``ERROR`` on the ``next.realtime.signals`` logger with the transport and the topics, and Django logs it on ``django.dispatch`` as well.
A ``CancelledError`` from a receiver is not swallowed.

Subpackage signals
------------------

The aggregator ``next.signals`` forwards from the modules below.

Pages
~~~~~

.. automodule:: next.pages.signals
   :members:

Components
~~~~~~~~~~

.. automodule:: next.components.signals
   :members:

URLs
~~~~

.. automodule:: next.urls.signals
   :members:

Forms
~~~~~

.. automodule:: next.forms.signals
   :members:

Static
~~~~~~

.. automodule:: next.static.signals
   :members:

Realtime
~~~~~~~~

.. automodule:: next.realtime.signals
   :members:

Partial rendering
~~~~~~~~~~~~~~~~~

.. automodule:: next.partial.signals
   :members:

SEO
~~~

.. automodule:: next.seo.signals
   :members:

Scripts
~~~~~~~

.. automodule:: next.scripts.signals
   :members:

Consent
~~~~~~~

.. automodule:: next.consent.signals
   :members:

Dependencies
~~~~~~~~~~~~

.. automodule:: next.deps.signals
   :members:

Server
~~~~~~

.. automodule:: next.server.signals
   :members:

Configuration
~~~~~~~~~~~~~

.. automodule:: next.conf.signals
   :members:

Router
~~~~~~

.. automodule:: next.router.signals
   :members:

Boundaries, deferred boundaries, and compression
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

``boundary_failed`` is defined in ``next.boundaries.signals``.
``boundary_rendered``, ``boundary_side_effect``, ``boundary_token_rejected``, ``page_stream_opened``, ``page_stream_closed``, and ``page_stream_declined`` are defined in ``next.suspense.signals``, and ``compression_backend_loaded`` in ``next.compression.signals``.

See also
--------

.. seealso::

   :doc:`/content/topics/signals` for receiver patterns and testing helpers.
