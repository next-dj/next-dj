.. _ref-testing:

Testing reference
=================

Module summary
--------------

``next.testing`` exposes a test client, partial envelope decoding, signal recorder, registry isolation, action helpers, HTML utilities, rendering helpers, loaders, patching helpers, dependency context builders, and a WebSocket communicator with a memory port for realtime.
``next.testing.plugin`` sits apart from that surface as an opt-in pytest plugin and is the only module in the package that imports pytest.
The package imports ``defusedxml`` for ``parse_sitemap``, so it needs the ``testing`` extra, ``next.dj[testing]``.

Public API
----------

.. automodule:: next.testing
   :members:
   :no-index:

Pytest plugin
~~~~~~~~~~~~~

``next.testing.plugin`` registers the ``next_pages``, ``next_components``, and ``next_clear_cache`` ini options together with the ``next_client`` fixture, once a project adds ``-p next.testing.plugin`` to its pytest ``addopts``.

.. automodule:: next.testing.plugin
   :members:

Client
~~~~~~

``NextClient`` extends Django's test client with form-action shortcuts and ``get_zones`` for end to end HTTP tests.
The module also ships ``envelope_of`` and ``PartialEnvelope`` for decoding patch responses.

.. automodule:: next.testing.client
   :members:

Signal capture
~~~~~~~~~~~~~~

``SignalRecorder`` and the ``capture_signals`` wrappers capture framework signal payloads inside a context manager.
They live in ``next.testing.capture``, which leaves the name ``signals`` to the framework signal modules the recorder connects to.

.. automodule:: next.testing.capture
   :members:

Isolation
~~~~~~~~~

``reset_registries`` and its narrower variants clear the framework registries between tests.
``reset_form_registration_state`` additionally clears the registration diagnostics and the cached wizard backend.
``reset_scripts`` drops every discovered ``scripts.py`` and ``reset_seo`` the discovered SEO sources and the ``@sitemap.items`` registrations, for a test that rewrites a source, since outside ``DEBUG`` each one is read once.
``reset_failure_logs`` makes every failure the framework contained log again, for a test that asserts on a log record whatever ran before it.

.. automodule:: next.testing.isolation
   :members:

Actions
~~~~~~~

``resolve_action_url`` and ``build_form_for`` exercise a registered action without crafting POST bodies.
A bare name makes ``resolve_action_url``, and through it ``NextClient.post_action``, import every routed ``page.py`` once before it answers, and a lookup with ``page`` does so on a miss.
A test therefore needs no visit of the page first, and a name still missing raises ``FormActionNotFoundError``.
The ``page`` keyword names the ``page.py``, or its directory, whose action the name means, and without it a name declared in several files raises ``LookupError`` listing them.
Only a name that pages scope to themselves is checked, since a shared name is one per project.
A ``page`` without the action raises ``FormActionNotFoundError`` naming the files that declare it.
A relative ``page`` resolves against ``BASE_DIR``, or against the working directory when the setting is absent.

.. automodule:: next.testing.actions
   :members:

Rendering
~~~~~~~~~

``render_page`` and ``render_component_by_name`` render a single page or component without an HTTP round trip.
``at`` drives visibility and seeds the ambient path the body composes from, so a nested ``{% component %}`` resolves from it as well, the way a field component does under :ref:`topics-forms-field-components-composition`.
The ``collector`` keyword takes a ``StaticCollector`` that catches the co-located assets of the component itself and of anything it nests, which is the one ambient value ``context`` cannot state without naming a private key.
The ``page_module_path`` keyword names the ``page.py`` a page-scoped ``{% form %}`` or ``{% action_url %}`` in the body resolves against.
A ``context`` entry naming a seeded key wins over the seed, so a test that wants a different ambient value states it there.

.. automodule:: next.testing.rendering
   :members:

Loaders
~~~~~~~

``eager_load_components``, ``eager_load_pages``, and ``clear_loaded_dirs`` force-import or reset the per-directory memoisation of ``page.py`` and ``component.py`` modules in tests.
``eager_load_pages`` runs each ``page.py`` through the loader a request uses, so a page never executes as a second module that registers its actions again, and a broken page raises ``PageModuleImportError`` with the original exception as its cause.
``clear_loaded_dirs`` drops the loaded modules with the directory memo, so the next call executes the files afresh.

.. automodule:: next.testing.loaders
   :members:

HTML utilities
~~~~~~~~~~~~~~

``find_anchor``, ``find_form``, ``assert_has_class``, and ``assert_missing_class`` inspect rendered HTML fragments, while ``form_action``, ``form_fields``, ``hidden_fields``, and ``init_payload`` pull submit targets, input values, and the ``Next._init`` bootstrap payload out of a rendered page.
``find_anchor`` and ``find_form`` locate the element with the standard library HTML parser and return its verbatim source span, so an element needs its end tag to be found and markup inside a comment or a script body is never matched.

.. automodule:: next.testing.html
   :members:

Patching
~~~~~~~~

The ``override_*`` context managers and ``patch_static_collector`` swap framework wiring for the duration of a block.

.. automodule:: next.testing.patching
   :members:

Metadata and SEO
~~~~~~~~~~~~~~~~

``assert_metadata(response, **expected)`` parses the head of a response or an HTML string and compares the tags it names, ``title``, ``description``, ``keywords``, ``viewport``, ``robots``, ``googlebot``, ``canonical``, ``alternates``, ``jsonld``, ``og``, and ``twitter``.
``None`` expects a tag to be absent, ``og`` and ``twitter`` take a dict of property suffixes, and ``jsonld`` lists every node, the members of the ``@graph`` flattened in.
``og=None`` and ``twitter=None`` expect no tag of that block at all.
An unknown key raises ``TypeError``, and every mismatch is reported in one ``AssertionError``.
The head is read by ``head_tags(html)`` of ``next.testing.metadata`` into a ``HeadTags`` value, and parsing stops at ``</head>``, so a tag in the body never counts.
The read is strict, so a single-valued tag rendered twice, a second ``<title>`` or canonical link among them, and a JSON-LD script that holds no JSON raise ``HeadParseError``, a ``ValueError``, rather than reading the first value.
A response is decoded with its own charset and a streaming one is read to its end, the same way ``parse_sitemap`` reads one.
``parse_sitemap`` answers the ``SitemapUrl`` values of a sitemap or an index response, each with ``loc``, ``lastmod``, and ``alternates``, both exported from ``next.testing``.
A body that is no well-formed XML raises ``xml.etree.ElementTree.ParseError``, so a malformed sitemap fails the test rather than reading as empty.
A robots file is plain text, so a test reads ``response.content`` directly.

.. automodule:: next.testing.metadata
   :members:

.. automodule:: next.testing.seo
   :members:
   :exclude-members: SitemapUrl

Realtime
~~~~~~~~

The realtime helpers cover both transports and the broker between them.

``memory_broker(**options)`` serves realtime from a fresh ``MemoryBroker`` for a block and yields it.
``options`` are its ``OPTIONS``, the other keys of the ``REALTIME`` mapping stay as configured, and the configured broker is built anew on its first use after the block.
Wrap every test that publishes in it, so no message or sequence number leaks from one test into the next.

``capture_published(*topics)`` records the messages that ``message_published`` reports, in publish order, and returns a ``PublishedMessages``.
It is a context manager, ``messages`` lists the recorded ``Message`` objects of the requested topics, ``recorder`` is the ``SignalRecorder`` of ``message_published`` underneath, and it is iterable and sized.
Without topics it records every topic.
It rides the signal, so it sees what ``publish`` and ``apublish`` send, a deferred ``on_commit`` publication after its commit, and the presence snapshots, on any broker and from sync or async code.
A direct ``broker.publish()`` does not send the signal and stays invisible to it.

``SseReader(response)`` parses the body of a ``TopicStream`` or any streaming SSE response into ``SseEvent`` values with ``event``, ``data``, ``id``, and ``json()``.
It iterates with ``for`` and ``next`` over a WSGI response and with ``async for`` and ``anext`` over an ASGI one.
Heartbeat comments, ``retry:`` lines, and blocks without ``data`` are not events, and the last ``retry:`` value is ``SseReader.retry``.
``close()`` and ``aclose()`` end the stream the way a disconnecting browser does, so the broker subscription is released and ``stream_unsubscribed`` goes out with the reason ``client``.
``aclose()`` on a response that was never read starts nothing and sends no signal, and a cancellation of the caller propagates out of it.
The reader is a context manager of both kinds.

.. code-block:: python
   :caption: polls/tests/test_stream.py

   import pytest
   from django.test import Client

   from next.partial import Patches
   from next.realtime import publish
   from next.testing import SseReader, capture_published, memory_broker

   @pytest.mark.django_db
   def test_stream_delivers_a_refresh(client: Client) -> None:
       with memory_broker(), capture_published("poll:1") as published:
           response = client.get("/polls/1/stream/")
           with SseReader(response) as events:
               assert next(events).event == "next-hello"
               publish("poll:1", Patches(None).refresh(zone="results"), on_commit=False)
               patches = next(events)
       assert patches.event == "next-patches"
       assert patches.json()["topic"] == "poll:1"
       assert len(published) == 1

``WebSocketCommunicator(app, path, headers=(), subprotocols=(), *, timeout=1)`` drives any ASGI application through one WebSocket connection.
The ``timeout`` in seconds belongs to the constructor and bounds every receive and every wait below, because a receive that times out ends the application.
``subscribe(ticket, last_seq=None)`` sends the ``sub`` frame of a ``SocketTicket`` and returns the ``hello`` frame as a dict.
``send_up(topic, name, data=None)`` sends an ``up`` event with a fresh id and returns ``None`` once the handler ran, or the ``UpstreamError`` its ``ack`` names.
An ``ack`` that names no known error raises ``AssertionError``.
``request(method, path, *, headers=None, body="")`` tunnels one HTTP request over the socket and returns a ``Reply`` with ``status``, ``headers``, and ``body``, or a ``Refusal``.
Frames that arrive while a call waits for its own answer are held and handed out by the next ``receive_json_from``, so a ``patches`` frame published before the ``hello`` is not lost.
A close that arrives during such a wait is held too, a failed call leaves it for ``receive_close()``, and a repeated wait fails at once instead of timing out.
``Reply``, ``Refusal``, and ``UpstreamError`` live in ``next.realtime.frames``.

``issue_socket_ticket(topics, *, authorizer_ref, transform_ref=None, presence=False, session=None, cookies: Mapping[str, str] | None = None)`` issues a ticket through the negotiation path of the configured ``TICKET_BINDING``.
Without a ``session`` it builds one from ``SESSION_ENGINE``, so a test that signs a user in passes the session it prepared.
The optional ``cookies`` mapping of names to values is placed on the request before the binding runs and is carried in the cookie header of the returned ticket.
The returned ``SocketTicket`` holds the ``grant``, the ``session``, the ``cookies`` the browser would present, and the ``credential`` the binding reads from them.
Its ``cookie`` property is the ready ``cookie`` header pair for the handshake.
``subscribe_frame()`` builds the ``sub`` frame.
The ``credential`` is what a ``Handshake`` for the ticket carries.
``aissue_socket_ticket`` takes the same arguments and awaits the session work on a worker thread, so an async test issues a ticket inside its scenario without ``SynchronousOnlyOperation``, under ``django_db(transaction=True)`` as the socket itself needs.
Under the default binding the credential is the session key, and under ``SignedCookieBinding`` it is the ``user:`` or ``visitor:`` credential of the binding.

.. code-block:: python
   :caption: rooms/tests/test_upstream.py

   import asyncio

   import pytest
   from django.core.asgi import get_asgi_application

   from next.realtime import application
   from next.testing import (
       WebSocketCommunicator,
       capture_published,
       issue_socket_ticket,
       memory_broker,
       override_websocket_mount,
   )

   @pytest.mark.django_db(transaction=True)
   def test_typing_event_is_published_to_the_room() -> None:
       ticket = issue_socket_ticket(("room:42",), authorizer_ref="rooms.access.can_watch")
       headers = [
           (b"origin", b"http://testserver"),
           (b"host", b"testserver"),
           ticket.cookie,
       ]

       async def scenario() -> None:
           app = application(get_asgi_application())
           communicator = WebSocketCommunicator(app, "/_next/ws/", headers, timeout=5)
           connected, _ = await communicator.connect()
           assert connected
           assert (await communicator.subscribe(ticket))["type"] == "hello"
           assert await communicator.send_up("room:42", "typing") is None
           await communicator.disconnect()

       with override_websocket_mount(), memory_broker(), capture_published("room:42") as sent:
           asyncio.run(scenario())
       assert len(sent) == 1

The handler under test is the ``typing`` handler of :doc:`/content/topics/realtime`, which publishes through ``apublish`` and so shows up in ``capture_published``.
A test of the socket without an ASGI application builds a ``TopicSocket`` over a ``MemoryPort``, which records the frames the socket sends in ``sent`` and ``close_code``.
``push`` and ``push_json`` queue client frames, ``receive`` and ``receive_json`` wait for the next server frame, ``disconnect`` leaves as the client, and ``inbound()`` is the frame source that ``TopicSocket.serve`` reads.

Creating a ``WebSocketApp`` marks the whole process as serving ``ws``, so a test session that starts an ASGI server in process would keep offering ``ws`` to every later WSGI test.
Wrap such a server in ``override_websocket_mount()``, which hands the flag back on exit, or pass ``mounted=True`` to offer ``ws`` without building the app.

.. code-block:: python
   :caption: tests/e2e/test_websocket.py

   from collections.abc import Iterator

   import pytest

   from next.testing import override_websocket_mount

   @pytest.fixture()
   def websocket_mount() -> Iterator[None]:
       with override_websocket_mount():
           yield

.. automodule:: next.testing.realtime
   :members:

Dependencies
~~~~~~~~~~~~

``make_resolution_context`` and ``resolve_call`` build dependency-injection test doubles for provider unit tests.

.. automodule:: next.testing.deps
   :members:

See also
--------

.. seealso::

   :doc:`/content/topics/testing` for the topic guide.
   :doc:`/content/howto/test-a-page-with-actions` for a recipe.
   :doc:`/content/howto/test-a-component-in-isolation` for rendering one component without HTTP.
