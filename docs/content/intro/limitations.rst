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

The page and component pipeline is synchronous.
Every context function, component render, and form action runs inside a synchronous request, and the framework offers no async page and no async middleware support.
An async workload lives outside the routed page tree, behind a task queue or an ordinary Django view.

The one exception is the realtime layer.
``TopicStream`` provides broker-backed delivery over SSE and WebSocket, and its upstream handlers may be async, while ``PatchEventStream`` remains the manual source escape hatch.
It refuses a sync source under ASGI and an async source under WSGI, because Django buffers a mismatched iterator fully before the first byte.
See :doc:`/content/topics/partial-rendering/sse` for the source contract and the heartbeat options.

No suspense, optional WebSockets
--------------------------------

Zones are a lazy reveal and a poll, not an asynchronous streaming boundary.
A ``lazy=`` zone fills through a later client round trip, and a ``poll=`` zone re-fetches on a client timer.
A slow zone holds its response instead of streaming a placeholder, because there is no server-side suspense flush.
Realtime offers WebSocket first when the process mounts the socket through the built-in ``WebSocketApp`` under ASGI, and SSE otherwise.
Both transports carry server-authored notifications from server to client, and form actions remain the path for client mutations.
The WebSocket also carries upstream events from the client to a registered handler and, for actions that opt in, a tunnel for form posts, while login, logout, and every action that sets a cookie stay on HTTP.
Replay is a bounded catch-up from a per-topic log, and a reconnect the log cannot cover resynchronises its zones over HTTP, see :doc:`/content/topics/realtime`.
Presence is an attendance count, with member data only when the stream names an ``identify`` callable.

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

The sections above are boundaries the framework does not cross.
The model also charges for what it does deliver, and the price lands in four places.

A rename is a behaviour change.
   Moving a directory moves the URL, the URL name, the layout chain, and the ``page.py`` chain that decides its access at once, and no static check finds a ``{% url %}`` call left pointing at the old name.

A context name is a contract no tool checks.
   Renaming a published key, or the parameter that reads it, breaks the link silently, because a parameter no provider handles resolves to its default and to ``None`` when it has none.

A published context key shadows a URL segment of the same name.
   The by-name provider runs ahead of the URL and query providers, so ``Depends("name")`` and ``DUrl["segment"]`` are the forms that survive both a rename and a collision.

Registration happens on import.
   A form action is live as soon as its module is imported, and its endpoint accepts a POST from any visitor until the class, its page, or a ``page.py`` above it declares a guard, which reaches further than a view behind a URL entry would.

:doc:`/content/misc/design-philosophy` states the rejected alternative and the cost beside every principle.
