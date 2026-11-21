.. _ref-compression:

Compression reference
=====================

Module summary
--------------

``next.compression`` encodes responses in the content coding a client accepts, zstd, brotli or gzip, negotiated per request from ``Accept-Encoding``.
It imports no other next.dj area, only Django, the standard library, the settings layer ``next.conf``, and the check tag of ``next.checks``.
``NEXT_FRAMEWORK["COMPRESSION"]`` configures it, see :doc:`settings`.

Codecs
------

Each codec is a ``CompressionBackend``, which names its content-coding token in ``coding``, reports whether its library imports through ``available()``, builds one streaming ``Compressor`` per stream through ``create(level, window)``, and compresses a buffered body in one call through ``compress_once(data, level)``.
A ``Compressor`` takes ``compress(data)``, ``flush()``, which makes everything written so far decodable by the client, and ``finish()``, which ends the stream.
A codec whose library is missing stays unavailable, it is never an import error, and ``create`` then raises ``CodecUnavailableError``.

.. list-table::
   :header-rows: 1
   :widths: 18 12 40 15 15

   * - Backend
     - Token
     - Library
     - Default level
     - Stream window
   * - ``ZstdBackend``
     - ``zstd``
     - ``compression.zstd`` from Python 3.14, the ``zstandard`` package below, a core dependency on Python 3.12 and 3.13
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

A gzip stream is one gzip member per response, flushed with ``Z_SYNC_FLUSH`` and ended with ``Z_FINISH``.
A zstd stream flushes with ``FLUSH_BLOCK`` and ends with ``FLUSH_FRAME``.
A buffered zstd body reuses one compression context per thread and level.
Every zstd frame, streamed or buffered, uses a window of at most ``windowLog`` 23, 8 MB, the most the ``zstd`` content coding asks a browser to decode, so a high level never raises it and ``WINDOWS`` refuses more as ``next.E228``.
Each backend also answers ``decoder()``, a function that decodes one of its streams write by write, which tests and tools read responses with.
``deflate`` is not offered.

.. automodule:: next.compression.backends
   :members: Compressor, CompressionBackend, ZstdBackend, BrotliBackend, GzipBackend, zstd_library, brotli_library, forget_codec_libraries, ZSTD_WINDOW_LIMIT

.. automodule:: next.compression.errors
   :members:

Negotiation
-----------

``negotiate(header, offered)`` answers the coding of ``offered`` to send for an ``Accept-Encoding`` value, ``None`` for identity.
The client weight decides first, ties go to the server order, ``q=0`` excludes a coding, ``*`` weighs every coding the header leaves unnamed, and ``x-gzip`` reads as ``gzip``.
An absent or empty header asks for identity.
The parse reads at most the first 256 characters, drops the element the cut splits, skips a malformed element, never raises, and is cached per header value.

.. automodule:: next.compression.negotiation
   :members: negotiate, parse_accept_encoding, MAX_HEADER

The middleware
--------------

``next.middleware.CompressionMiddleware`` compresses every response no stream compressed itself, next.dj buffered pages and every other Django view alike.
It is sync and async capable, following the mode of the handler below it.
List it directly below ``SecurityMiddleware``, above ``CommonMiddleware``, ``ConditionalGetMiddleware``, and the cache middleware, the placement Django gives its own ``GZipMiddleware``, so those middleware read the body before it is compressed.
The project scaffold lists it there, and the framework never inserts middleware on its own.

.. code-block:: python

   MIDDLEWARE = [
       "django.middleware.security.SecurityMiddleware",
       "next.middleware.CompressionMiddleware",
       "django.contrib.sessions.middleware.SessionMiddleware",
       "django.middleware.common.CommonMiddleware",
   ]

A response is left as it is when compression is off, when it already carries ``Content-Encoding``, for a status of 1xx, 204, 206 or 304, with a ``Content-Range``, with ``next_compress`` set to ``False`` on it, as a ``FileResponse`` over a file, or with ``Cache-Control: no-transform`` while ``RESPECT_NO_TRANSFORM`` holds.
A file response keeps the server's ``wsgi.file_wrapper`` path and is sent as it is, since its blocks are too small to compress and flush one by one, so a static file is precompressed by the storage or the proxy instead.
A media type outside ``CONTENT_TYPES``, an event stream while ``SSE`` is off, and a response the ``EXCLUDE`` callable keeps plain are left as they are without ``Vary``.
Every other response gets ``Vary: Accept-Encoding``, and a ``HEAD`` request stops there.
A buffered body under ``MIN_SIZE`` stays plain, and a larger one is compressed once, kept only when it comes out smaller, with ``Content-Length`` rewritten.
A streamed body that does not compress itself is wrapped, each chunk compressed and flushed so the client decodes it at once, empty output never sent, ``Content-Length`` removed, and the source closed however the stream stops.
An async stream stays async, and under ASGI a buffered body or a chunk larger than ``OFFLOAD_BYTES`` is compressed in a worker thread.
A strong ``ETag`` becomes weak, as the encoded bytes differ from the identity ones.

With ``UpdateCacheMiddleware`` above the middleware and ``FetchFromCacheMiddleware`` below it, the order the checks ask for, the cache stores the compressed bytes under ``Vary: Accept-Encoding``, and a hit already carries ``Content-Encoding``, so it passes through without being compressed again.
The middleware never decodes a request ``Content-Encoding``, so a request body cannot unpack into a decompression bomb.

Django's ``GZipMiddleware`` skips a response that already carries ``Content-Encoding``, and on Django below 6.1 it buffers a stream rather than flushing each chunk.
``manage.py check`` refuses both middleware listed together as ``next.E227``, so a project uses one or the other.

Streams that compress themselves
--------------------------------

A streaming response that knows its own chunk boundaries compresses itself.
At construction it calls ``prepare_stream(request, response, topic=False)``, which negotiates the coding, sets ``Content-Encoding`` and ``Vary``, drops ``Content-Length``, weakens a strong ``ETag``, and answers a ``StreamEncoding``.
The middleware then passes the response through untouched.
``None`` sends the stream plain and marks it so the middleware leaves it plain too, except for a document stream while ``STREAMS`` is ``"middleware"``, which is left to the middleware.
A streamed page negotiates after its ``Cache-Control`` is final, ``private, no-cache`` added to the ``no-store`` or ``no-transform`` the page set, so a page that sets ``no-transform`` streams plain.
An event stream follows ``SSE``, and with ``topic=True`` a realtime topic stream follows ``SSE_TOPICS``, ``"reset"`` choosing only a codec that can drop its window, never brotli.

``StreamEncoding.write(chunk)`` answers the chunk padded, compressed and flushed, ``awrite(chunk)`` the same in a worker thread when the chunk is above ``OFFLOAD_BYTES`` or ``offload=True`` asks for it, and ``end()`` the bytes that end the stream.
``wrap(chunks)`` and ``awrap(chunks)`` encode a whole sync or async iterator, which is what the middleware runs.
``offloads(data)`` answers whether a buffer is above ``OFFLOAD_BYTES``.

``decode_response(response)`` answers the whole body of a response decoded from its ``Content-Encoding``, and ``response_decoder(response)`` the write-by-write decoder of that coding, so a test reads a compressed page or stream as plain bytes.

.. automodule:: next.compression.manager
   :members: prepare_stream, compress_response, acompress_response, offloads, CompressionManager, compression_manager, OPT_OUT, decode_response, response_decoder

.. automodule:: next.compression.streams
   :members: StreamEncoding, padding, media_type

BREACH
------

Compressing a response that holds a secret beside input an attacker controls lets the attacker learn the secret from compressed lengths, the BREACH attack.
Compression of HTML is the industry default and the risk is managed, not removed.
Django masks the CSRF token per response, and CSP nonces are random per response.

Every write of a self-compressing HTML document or event stream, and every buffered HTML body the middleware compresses, carries a comment of random length, up to ``PADDING`` characters of letters, digits, ``.`` and ``_``.
An HTML body gets ``<!--...-->`` and an event stream a ``:...`` comment line, which the browser and the ``EventSource`` parser ignore, so every observed length carries noise.
A stream the middleware wraps is not padded, since the middleware cannot tell where a chunk ends.
``padding(content_type, limit)`` answers the comment for a content type, empty for any other type.

Realtime topic streams stay uncompressed by default, since one stream interleaves content other users wrote with the subscriber's own.
A page or view that renders a secret beside reflected input opts out of compression, see the next section, or through ``EXCLUDE``.
``manage.py check`` warns with ``next.W166`` when ``SESSION_COOKIE_SAMESITE`` or ``CSRF_COOKIE_SAMESITE`` is ``'None'`` while compression is on, as another site can then drive credentialed requests.

WebSocket ``permessage-deflate`` is negotiated by the ASGI server, outside this area.

Opting out
----------

``page.compress(False)`` at the top of a ``page.py`` keeps every response of its page plain, the full page, its stream, and its zone answers alike, and ``page.compress(True)`` states the default.
The call binds its own page alone, the last call of a module wins, and a call inside a function, outside a ``page.py``, or with a value that is no bool applies nothing and is reported as ``next.E222``.
It reaches the response through the ``compress`` field of the page's ``ResponsePolicy``, which marks the response with ``next_compress = False`` before the stream negotiates and before the middleware runs.

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

The application sets ``Content-Encoding`` and ``Vary``, so nginx, Caddy, Cloudflare and CloudFront pass the body through without compressing it again.
``no-transform`` is not added to ordinary pages, as it would block edge optimisations.

Checks and signals
------------------

``next.compression.checks`` reports the ``COMPRESSION`` shape (``next.E035``, ``next.E076``), unusable paths (``next.E219``), codec problems (``next.E228``), the middleware pair (``next.E227``), a missing middleware (``next.W164``), the middleware listed too low (``next.W165``), and cross-site cookies (``next.W166``), see :doc:`system-checks`.
``compression_backend_loaded`` is sent with the backend class as the sender and ``config`` and ``instance`` once per backend the manager builds.
