.. _ref-compression:

Compression reference
=====================

Module summary
--------------

``next.compression`` encodes responses in the content coding a client accepts, zstd, brotli or gzip, negotiated per request from ``Accept-Encoding``.
It imports no other area package, only Django, the standard library, and the shared layers ``next.conf``, ``next.checks``, and ``next.utils``.
``NEXT_FRAMEWORK["COMPRESSION"]`` configures it, see :doc:`settings`.

Codecs
------

Each codec is a ``CompressionBackend``, which names its content-coding token in ``coding`` and reports whether its library imports through ``available()``.
``create(level, window, *, pad=0)`` builds one streaming ``Compressor`` per stream, and ``compress_once(data, level, *, pad=0)`` compresses a buffered body in one call.
``levels`` and ``windows`` hold the inclusive ranges the ``LEVEL`` and ``WINDOW`` options of a ``BACKENDS`` entry may take, ``windows`` being ``None`` for a fixed window.

``pad`` is the most random bytes the codec adds in a field its decoders skip, and ``extra`` names the package extra that installs its library, ``None`` for a codec of the standard library.
A ``Compressor`` takes ``compress(data)``, ``flush()``, which makes everything written so far decodable by the client, and ``finish()``, which ends the stream.
A codec whose library is missing stays unavailable rather than failing the import, and ``create`` then raises ``CodecUnavailableError``.

.. list-table::
   :header-rows: 1
   :widths: 18 12 40 15 15

   * - Backend
     - Token
     - Library
     - Default ``LEVEL``
     - Default ``WINDOW``
   * - ``ZstdBackend``
     - ``zstd``
     - ``compression.zstd`` from Python 3.14, the ``zstandard`` package below, installed with the ``zstd`` extra
     - 3
     - ``windowLog`` 17
   * - ``BrotliBackend``
     - ``br``
     - ``brotli`` or ``brotlicffi``, installed with the ``brotli`` extra
     - 4
     - ``lgwin`` 18
   * - ``GzipBackend``
     - ``gzip``
     - the standard library ``zlib``
     - 4
     - 32 KB, fixed

A gzip stream is one gzip member per response, raw deflate between a header and a trailer the backend writes, flushed with ``Z_SYNC_FLUSH`` and ended with ``Z_FINISH``.
The header carries a file name of up to ``pad`` letters, which browsers skip, and zstd and brotli take ``pad`` without adding anything, since browsers have no field of theirs they are known to skip.
A zstd stream flushes with ``FLUSH_BLOCK`` and ends with ``FLUSH_FRAME``.
A buffered zstd body reuses one compression context per thread and level.

Every zstd frame, streamed or buffered, uses a window of at most ``windowLog`` 23, 8 MB, the most the ``zstd`` content coding asks a browser to decode.
A high level never raises it, and a ``WINDOW`` above it is reported as ``next.E228``.
Each backend also answers ``decoder()``, a function that decodes one of its streams write by write, which tests and tools read responses with.
``deflate`` is not offered.

.. automodule:: next.compression.backends
   :members: Compressor, CompressionBackend, ZstdBackend, BrotliBackend, GzipBackend, zstd_library, brotli_library, forget_codec_libraries, ZSTD_WINDOW_LIMIT

.. automodule:: next.compression.errors
   :members:

Negotiation
-----------

Each response is sent in the coding of ``ORDER`` the ``Accept-Encoding`` header weighs highest among the loaded backends, or as identity.
The client weight decides first, ties go to the server order, ``q=0`` excludes a coding, ``*`` weighs every coding the header leaves unnamed, and ``x-gzip`` reads as ``gzip``.
An absent or empty header asks for identity.
The parse reads at most the first 256 characters, drops the element the cut splits, skips a malformed element, never raises, and is cached per header value.

The middleware
--------------

``next.middleware.CompressionMiddleware`` compresses every response no stream compressed itself, next.dj buffered pages and every other Django view alike.
It is sync and async capable, following the mode of the handler below it.
List it directly below ``SecurityMiddleware``, above ``CommonMiddleware``, ``ConditionalGetMiddleware``, and the cache middleware, the placement Django gives its own ``GZipMiddleware``, so those middleware read the body before it is compressed.
Without it a streamed page still compresses itself and every other response leaves plain, which is a valid setup that no check reports, and the framework never inserts middleware on its own.

.. code-block:: python

   MIDDLEWARE = [
       "django.middleware.security.SecurityMiddleware",
       "next.middleware.CompressionMiddleware",
       "django.contrib.sessions.middleware.SessionMiddleware",
       "django.middleware.common.CommonMiddleware",
   ]

A response is left as it is, without ``Vary``, in these cases.

- Compression is off.
- The response already carries ``Content-Encoding`` or a ``Content-Range``.
- Its status is 1xx, 204, 206, or 304.
- It carries ``next_compress`` set to ``False``.
- It is a ``FileResponse`` over a file.
- It carries ``Cache-Control: no-transform`` while ``RESPECT_NO_TRANSFORM`` holds.

A file response keeps the server's ``wsgi.file_wrapper`` path and is sent as it is, since its blocks are too small to compress and flush one by one, so a static file is precompressed by the storage or the proxy instead.
A media type outside ``CONTENT_TYPES``, an event stream while ``SSE`` is off, a response the ``EXCLUDE`` callable keeps plain, a buffered body under ``MIN_SIZE``, and a response that is neither an ``HttpResponse`` nor a stream are left as they are without ``Vary``.
Every other response gets ``Vary: Accept-Encoding``.
A larger buffered body is compressed once and kept only when it comes out smaller, with ``Content-Length`` rewritten.
A strong ``ETag`` becomes weak, as the encoded bytes differ from the identity ones.

A ``HEAD`` request negotiates like ``GET``.
Its response gets the same ``Content-Encoding``, ``Vary``, and weakened ``ETag``, and loses its ``Content-Length``, while the body stays untouched.

A streamed body that does not compress itself is wrapped, with ``Content-Length`` removed, empty output never sent, and the source closed however the stream stops.
A sync stream is compressed and flushed per non-empty chunk.
An async stream stays async and flushes its first chunk at once, then once 16 KB of input gathers or its source stays idle for ``COALESCE``.
An async event stream flushes every event.
Under ASGI a buffered body or a chunk larger than 16 KB is compressed in a worker thread.

With ``UpdateCacheMiddleware`` above the middleware and ``FetchFromCacheMiddleware`` below it, the order the checks ask for, the cache stores the compressed bytes under ``Vary: Accept-Encoding``, and a hit already carries ``Content-Encoding``, so it passes through without being compressed again.
The middleware never decodes a request ``Content-Encoding``, so a request body cannot unpack into a decompression bomb.

Django's ``GZipMiddleware`` skips a response that already carries ``Content-Encoding``, and on Django below 6.1 it buffers a stream rather than flushing each chunk.
``next.compression.manager.FLUSHING_GZIP`` holds that Django version, ``(6, 1)``.
``manage.py check`` refuses both middleware listed together as ``next.E227``, so a project uses one or the other.

Streams that compress themselves
--------------------------------

A next.dj stream that knows its own chunk boundaries compresses itself.
Those are the streamed page, the streamed partial, the event stream of a zone, and a realtime topic stream.
It negotiates as it is built, sets ``Content-Encoding`` and ``Vary``, drops ``Content-Length``, and weakens a strong ``ETag``, and the middleware then passes it through untouched.

A stream that negotiates identity is marked so the middleware leaves it plain too, except for a document stream while ``STREAMS`` is ``"middleware"``, which is left to the middleware.
A streamed page negotiates after its ``Cache-Control`` is final, ``private, no-cache`` added to the ``no-store`` or ``no-transform`` the page set, so a page that sets ``no-transform`` streams plain.
An event stream follows ``SSE``, and a realtime topic stream follows ``SSE_TOPICS``, ``"reset"`` choosing only a codec that can drop its window, never brotli.

Each write of such a stream is padded, compressed and flushed on its own, so the client decodes every chunk as it arrives.
Under ASGI a write larger than 16 KB is compressed in a worker thread.

``decode_response(response)`` answers the whole body of a response decoded from its ``Content-Encoding``, so a test reads a compressed page or stream as plain bytes.

.. autofunction:: next.compression.decode_response

.. _ref-compression-breach:

BREACH
------

Compressing a response that holds a secret beside input an attacker controls lets the attacker learn the secret from compressed lengths, the BREACH attack.
Compression of HTML is the industry default and the risk is managed, not removed.
Django masks the CSRF token per response, and CSP nonces are random per response.

Every gzip body and gzip stream carries a header file name of random length, up to ``PADDING`` bytes, the field Django's ``GZipMiddleware`` pads, so every gzip length carries noise whatever the media type.
Every buffered body the middleware compresses, and every write of a self-compressing stream, also ends with filler of random length its format ignores, up to ``PADDING`` characters, in any coding.
HTML, XHTML, XML and any ``+xml`` type get ``<!--...-->``, CSS and JavaScript ``/*...*/``, and an event stream a ``:...`` comment line, each filled with letters, digits, ``.`` and ``_``.
JSON and any ``+json`` type get trailing blanks, which a JSON parser skips, so a patch envelope stays valid JSON.

Plain text, CSV, NDJSON and other types get no filler, so under zstd or brotli their lengths carry no noise.
A stream the middleware wraps gets the gzip header noise alone, with no filler after its writes.

Realtime topic streams stay uncompressed by default, since one stream interleaves content other users wrote with the subscriber's own.
A page or view that renders a secret beside reflected input opts out of compression, see the next section, or through ``EXCLUDE``.
``manage.py check`` warns with ``next.W166`` when ``SESSION_COOKIE_SAMESITE`` or ``CSRF_COOKIE_SAMESITE`` is ``'None'`` while compression is on, as another site can then drive credentialed requests.

WebSocket ``permessage-deflate`` is negotiated by the ASGI server, outside this area.

Opting out
----------

``page.compress(enabled=False)`` at the top of a ``page.py`` keeps every response of its page plain, the full page, its stream, and its zone answers alike, and ``page.compress(enabled=True)`` states the default.
The call binds its own page alone, the last call of a module wins, a value that is no bool, a flag passed by position, or a call inside a function raises when the module runs, and a call outside a ``page.py`` applies nothing and is reported as ``next.E245``.
It reaches the response through the ``compress`` field of the page's ``ResponsePolicy``, which marks the response with ``next_compress = False`` before the stream negotiates and before the middleware runs.

.. code-block:: python
   :caption: pages/statement/page.py

   from next import page

   page.compress(enabled=False)

A plain Django view takes the ``no_compress`` decorator of ``next.compression``, sync or async, which marks each of its responses the same way, and a view may also set ``response.next_compress = False`` itself.

.. code-block:: python

   from next.compression import no_compress

   @no_compress
   def statement(request):
       ...

.. automodule:: next.compression.decorators
   :members: no_compress

Proxies
-------

The application sets ``Content-Encoding`` and ``Vary: Accept-Encoding`` on every response it compresses.
A proxy or a CDN in front of the application has to keep ``Accept-Encoding`` in its cache key, forward the header, and leave an encoded body as it is, which a request from outside the proxy confirms, see :ref:`deployment-compression`.
``no-transform`` is not added to ordinary pages, as it would block edge optimisations.

Checks and signals
------------------

``next.compression.checks`` reports the ``COMPRESSION`` shape as ``next.E035`` and ``next.E076``, unusable paths as ``next.E219``, and codec problems as ``next.E228``, see :doc:`system-checks`.
It reports both compressing middleware listed together as ``next.E227``, the middleware listed too low as ``next.W165``, and ``STREAMS`` of ``"middleware"`` without the middleware as ``next.W193``.
Cross-site cookies while compression is on draw ``next.W166``.
``compression_backend_loaded`` is sent once per backend the manager builds, with the backend class as the sender, the ``BACKENDS`` entry as ``config``, and the backend as ``instance``.

See also
--------

.. seealso::

   :doc:`settings` for the ``COMPRESSION`` keys.
   :doc:`system-checks` for the checks.
   :repo:`observability <tree/main/examples/observability>` for a compressed scrape and :repo:`_template <tree/main/examples/_template>` for the middleware placement, see :doc:`/content/misc/examples`.
