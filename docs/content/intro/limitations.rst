.. _intro-limitations:

Limitations
===========

next.dj draws a few deliberate boundaries.
Knowing them up front prevents an architecture built on an assumption the framework does not hold.

Synchronous only
----------------

The page and component pipeline is synchronous.
There is no async view and no async middleware support.
Every context callable, component render, and form action runs inside a synchronous request.
An async workload lives outside the routed page tree, behind a task queue or an ordinary Django view.

No suspense, no WebSockets
--------------------------

Zones are a lazy reveal and a poll, not an asynchronous streaming boundary.
A ``lazy=`` zone fills through a later client round trip, and a ``poll=`` zone re-fetches on a client timer.
A slow zone holds its response instead of streaming a placeholder, because there is no server-side suspense flush.
Real-time delivery is Server-Sent Events only, one direction from server to client.
There is no WebSocket transport, so client-to-server push over a persistent socket stays outside the model.

Single partial backend
----------------------

``PartialBackendManager`` runs one protocol backend.
``PARTIAL_BACKENDS`` activates its first entry.
A second entry does not fail ``manage.py check`` and is reported as the ``next.W071`` warning instead.
See :doc:`/content/topics/partial-rendering/index` for the partial model and its own boundary list.

Web-coupled dependency injection
--------------------------------

The dependency resolver is bound to the request and response cycle.
``ResolutionContext.request`` is typed as :class:`django.http.HttpRequest` or ``None``, and providers resolve against the request in flight.
There is no request-free resolution mode, so a background job that wants injected dependencies builds a synthetic request first.
