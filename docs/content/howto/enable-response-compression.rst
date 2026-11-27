.. _howto-enable-response-compression:

Enable response compression
===========================

Problem
-------

Pages and JSON answers leave the server as plain text, and a streamed page costs the full size on the wire.
Compression is available, but it must not compress twice when a proxy in front of the project already does, and it must not run on a page that renders a secret beside reflected input.

Solution
--------

List ``next.middleware.CompressionMiddleware`` in ``MIDDLEWARE``, install a codec extra when zstd or brotli is wanted, and decide which layer compresses.
The framework negotiates the coding per request from ``Accept-Encoding``, compresses streamed pages as they are written, and sets ``Content-Encoding`` and ``Vary`` itself.

Walkthrough
-----------

Add the middleware
~~~~~~~~~~~~~~~~~~

Without the middleware a streamed page still compresses itself and every other response leaves plain, so a project adds one line directly below ``SecurityMiddleware`` to compress the rest.

.. code-block:: python
   :caption: config/settings.py

   MIDDLEWARE = [
       "django.middleware.security.SecurityMiddleware",
       "next.middleware.CompressionMiddleware",
       "django.contrib.sessions.middleware.SessionMiddleware",
       "django.middleware.common.CommonMiddleware",
   ]

The position matters.
Middleware above it read the compressed body, and ``CommonMiddleware`` and the cache middleware below it read the plain one, which is what they need to set a correct ``ETag`` and ``Content-Length``.
``manage.py check`` reports one listed too low as ``next.W165``.
Django's ``GZipMiddleware`` and this one do not mix, and ``next.E227`` reports both.

The defaults need no setting.
``COMPRESSION`` is on, and zstd is offered first, then brotli, then gzip.
A coding whose library is missing is left out.
gzip needs nothing, zstd comes from the standard library from Python 3.14 and from the ``zstd`` extra below it, and brotli needs the ``brotli`` extra.

.. code-block:: bash
   :caption: shell

   uv add "next.dj[zstd,brotli]"

A codec named in ``ORDER`` or ``BACKENDS`` whose library is missing is ``next.E228``, with the extra to install in its hint.
With ``STREAMS`` set to ``"middleware"``, a ``MIDDLEWARE`` without this middleware is ``next.W193``, since no page stream is then compressed.

Know what is compressed
~~~~~~~~~~~~~~~~~~~~~~~

A buffered response over ``MIN_SIZE`` bytes whose media type is in ``CONTENT_TYPES`` is compressed once, and is sent plain when the result is not smaller.
``CONTENT_TYPES`` matches a media type exactly, by a ``text/*`` family, or by a structured suffix such as ``application/*+json``, which covers the patch envelope of a partial answer.

A streamed page compresses itself, one flushed write at a time, so the browser decodes each chunk as it arrives.
An event stream follows ``SSE``, and a realtime topic stream stays plain unless ``SSE_TOPICS`` says otherwise.
A file response, a response that already has ``Content-Encoding``, a ``206``, a ``304``, and a response with ``Cache-Control: no-transform`` are left as they are.
The concept page :doc:`/content/topics/compression` explains the negotiation and what buffered and streamed responses do differently.

Tune it
~~~~~~~

.. code-block:: python
   :caption: config/settings.py

   NEXT_FRAMEWORK = {
       "COMPRESSION": {
           "BACKENDS": [
               {"BACKEND": "next.compression.ZstdBackend", "OPTIONS": {"LEVEL": 5}},
               {"BACKEND": "next.compression.BrotliBackend", "OPTIONS": {"LEVEL": 5}},
               {"BACKEND": "next.compression.GzipBackend", "OPTIONS": {"LEVEL": 6}},
           ],
           "MIN_SIZE": 512,
           "EXCLUDE": "config.compression.keep_plain",
       },
   }

``BACKENDS`` replaces the default list whole, so it names every codec the project offers.
``LEVEL`` in the ``OPTIONS`` of an entry sets the compression level of that codec, and a ``WINDOW`` left out keeps the window of the codec.
A level outside the range of the codec is ``next.E228``, and an option other than ``LEVEL`` and ``WINDOW`` is ``next.E076``.
``MIN_SIZE`` is the smallest buffered body that is compressed.
``EXCLUDE`` names a callable that takes the request and the response and returns ``True`` to keep the response plain.

.. code-block:: python
   :caption: config/compression.py

   from django.http import HttpRequest, HttpResponseBase

   def keep_plain(request: HttpRequest, response: HttpResponseBase) -> bool:
       return request.path.startswith("/downloads/")

Keep a secret out of a compressed page
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

A page that renders a secret beside input the visitor controls can leak the secret through the size of the compressed response.
Every compressed body and every write of a stream the framework writes carries filler of random length against this, and a page that cannot take the risk opts out.

.. code-block:: python
   :caption: statements/pages/statement/page.py

   from next import page

   page.compress(enabled=False)

``page.compress(enabled=False)`` keeps every response of that page plain, the full page, its stream, and its zone answers.
A plain Django view takes the ``no_compress`` decorator of ``next.compression``.

.. code-block:: python
   :caption: statements/views.py

   from django.http import HttpRequest, HttpResponse

   from next.compression import no_compress

   @no_compress
   def statement(request: HttpRequest) -> HttpResponse:
       return HttpResponse(render_statement(request))

Decide who compresses when a proxy sits in front
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

A proxy or a CDN that finds ``Content-Encoding`` on a response passes the body through, so letting the application compress is the safe default.
A proxy that compresses everything it forwards, or that rewrites responses at the edge, compresses a second time or breaks the flush of a stream.
Pick one layer.
When the proxy compresses, turn the application off.

.. code-block:: python
   :caption: config/settings/prod.py

   NEXT_FRAMEWORK = {"COMPRESSION": {"ENABLED": False}}

``ENABLED`` set to ``False`` switches off the middleware and every self-compressing stream at once.
A proxy that compresses must also forward each write of a streamed page as it arrives, and a deployment where it buffers states ``DEFER["SERVER"]["OPTIONS"]["INCREMENTAL"] = False``, see :doc:`/content/deployment/wsgi-asgi`.

Verification
------------

Ask for a page with each coding and read the headers.

.. code-block:: bash
   :caption: shell

   curl -s -o /dev/null -D - -H "Accept-Encoding: zstd, br, gzip" https://example.com/ | grep -iE "content-encoding|vary"

The response names one coding and ``Vary: Accept-Encoding``.
Asking with ``Accept-Encoding: identity`` returns no ``Content-Encoding``.
Check from outside the proxy as well, since only that request shows whether a layer in between changed the answer.

A test asks for the coding and decodes the body.
The tests read two pages of the project, ``/``, whose body holds ``It works`` and runs over ``MIN_SIZE`` bytes, since a smaller body leaves plain, and ``/stats/``, which has a ``loading.djx`` and streams.

.. code-block:: python
   :caption: tests/test_compression.py

   from next.compression import decode_response
   from next.testing import NextClient

   def test_a_page_leaves_compressed_for_a_client_that_accepts_it(db) -> None:
       response = NextClient().get("/", headers={"Accept-Encoding": "gzip"})
       assert response["Content-Encoding"] == "gzip"
       assert "Accept-Encoding" in response["Vary"]
       assert b"It works" in decode_response(response)

A page with a deferred boundary reads through ``get_stream``, which decodes each write before it splits the shell from the chunks.

.. code-block:: python
   :caption: tests/test_compression.py

   def test_a_streamed_page_decodes_to_its_shell(db) -> None:
       streamed = NextClient().get_stream("/stats/", headers={"Accept-Encoding": "gzip"})
       assert streamed.response["Content-Encoding"] == "gzip"
       assert streamed.shell

See also
--------

.. seealso::

   :doc:`/content/topics/compression` for how a coding is negotiated and what each kind of response does.
   :doc:`/content/ref/compression` for the codecs, the middleware, and the backends.
   :doc:`/content/ref/settings` for the ``COMPRESSION`` keys.
   :doc:`/content/deployment/wsgi-asgi` for compression behind a proxy.
   :repo:`_template <tree/main/examples/_template>` for a test of a compressed page, and :repo:`observability <tree/main/examples/observability>` for a scrape that accepts gzip.
