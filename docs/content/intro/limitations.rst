.. _intro-limitations:

Limitations
===========

next.dj draws a few deliberate boundaries.
Knowing them up front prevents an architecture built on an assumption the framework does not hold.

One layout placeholder
----------------------

A layout offers exactly one ``{% template %}`` placeholder.
Composition produces one flat template, so a page cannot override a named region of an ancestor and there is no ``{{ block.super }}``.
A region that varies per page is expressed through ``@context`` and a component.
A section that needs a different document shell opens its layout with ``{% layout root %}``, which stops the chain at that layout.
The head is the exception, filled through the metadata a ``page.py`` declares and one ``{% metadata %}`` tag renders, see :doc:`/content/topics/seo/metadata`.
See :doc:`/content/topics/layouts` for the composition rules.

A synchronous render pipeline
-----------------------------

The view that serves a page is a synchronous Django view.
A page ``render``, an ``@context`` callable, an ``@page.metadata`` callable, an ``@component.context`` callable, a named dependency, and a ``Depends`` factory may be ``async def``, and the view drives them to completion on an event loop while the request thread waits.
The async part runs on the event loop of the ASGI server, or on a background loop under WSGI, and a thread-sensitive call it makes runs on the request thread.
See :doc:`/content/topics/boundaries/async` for how the callables overlap and what they share.

Everything else on the request path stays synchronous.
An ``@action`` handler and the form and wizard hooks refuse ``async def``, registration raises :exc:`~next.errors.AsyncCallableError`, and ``manage.py check`` reports the module as ``next.E218``, see :ref:`topics-pages-sync-callables`.
An async generator is refused wherever an async callable is allowed.
The pipeline provides no async middleware layer of its own, so a middleware of the project that is sync or async follows Django's rules for adapting it.
A long async workload lives behind a task queue or an ordinary Django view rather than in the routed page tree.

The one exception is the realtime layer.
``TopicStream`` provides broker-backed delivery over SSE and WebSocket, and its topic transforms and upstream handlers may be async, while ``PatchEventStream`` remains the manual source escape hatch.
``PatchEventStream`` refuses a sync source under ASGI and an async source under WSGI, because Django buffers a mismatched iterator fully before the first byte.
See :doc:`/content/topics/partial-rendering/sse` for the source contract and the heartbeat options.

Deferred boundaries, optional WebSockets
----------------------------------------

A page with a ``loading.djx`` and a deferred component are deferred boundaries, which the response sends as their loading UI first and fills once their job settles.
A full page streams each settled boundary into the document, and a streamed partial response, such as a zone reload, sends the boundaries nested in its envelope as event-stream frames.
A zone itself is a reload region rather than a boundary, so a slow zone with no deferred boundary inside holds its response, and a component deferred with ``load``, ``visible`` or ``manual`` fills through a later client round trip.
A boundary delivers server-rendered HTML and nothing more, so there is no hydration of client components.
The ``<head>`` is not flushed before the shell renders, since it carries the assets the body render collects.
The framework bundles no skeleton, spinner or progress bar, so the loading UI is whatever ``loading.djx`` or ``BOUNDARIES["LOADING_TEMPLATE"]`` renders.
A job runs on a thread or on the server loop, never in another process or sub-interpreter, because it needs the live request and its database connection.
The limits of streamed boundaries, from broken markup to a third-party script in a navigation, are listed in :ref:`topics-boundaries-streaming-limits`.

Realtime offers WebSocket first when the process mounts the socket through the built-in ``WebSocketApp`` under ASGI, and SSE otherwise.
Both transports carry server-authored notifications from server to client, and form actions remain the path for client mutations.
The WebSocket also carries upstream events from the client to a registered handler and, for actions that opt in, a tunnel for form posts, so login, logout, and every action that sets a cookie keep the default ``transport="http"``.
Replay is a bounded catch-up from a per-topic log, and a reconnect the log cannot cover resynchronises its zones over HTTP, see :doc:`/content/topics/realtime`.
Presence is an attendance count, with member data only when the stream names an ``identify`` callable.

Quiet streams on the request lane
---------------------------------

A job on the request lane runs on the thread of its request, which is the default for an SQLite project and for any job a request-lane rule pins.
Under a WSGI server that thread also writes the stream, and synchronous code cannot be interrupted, so while such a job runs the stream writes nothing, no heartbeat included, and the frames of boundaries that already settled wait behind it.
A stream is therefore quiet for as long as its longest request-lane job, and a proxy whose idle timeout is shorter than that cuts it.
A proxy in front of a streaming project allows an idle read of at least the response deadline, ``NEXT_FRAMEWORK["DEFER"]["DEADLINE"]``.

Single partial backend
----------------------

The partial backend manager runs one protocol backend.
``PARTIAL_BACKENDS`` activates its first entry.
A second entry does not fail ``manage.py check`` and is reported as the ``next.W071`` warning instead.
See :doc:`/content/topics/partial-rendering/index` for the partial model and its own boundary list.

Web-coupled dependency injection
--------------------------------

The dependency resolver is bound to the request and response cycle.
``ResolutionContext.request`` is typed as :class:`django.http.HttpRequest` or ``None``, and providers resolve against the request in flight.
There is no request-free resolution mode, so a background job that wants injected dependencies builds a synthetic request first.

What the model costs
--------------------

The model also charges for what it delivers, through a rename that moves a URL, a context name no tool checks, a context key that shadows a URL segment, and an action that is live from import.
:doc:`/content/misc/design-philosophy` states each cost beside the principle that incurs it.
