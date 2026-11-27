.. _topics-compression:

Response compression
====================

next.dj encodes a response in the content coding the client accepts.
One middleware compresses every buffered response, and a streamed page compresses itself so the browser decodes each chunk as it arrives.
The codecs, the middleware, and the settings are in :doc:`/content/ref/compression`, and :doc:`/content/howto/enable-response-compression` walks through turning it on.

.. contents::
   :local:
   :depth: 1

What is negotiated
------------------

The client states what it decodes in ``Accept-Encoding``, and the server answers one coding or none.
In ``negotiate`` the client weight decides first, and a tie goes to the server order in ``COMPRESSION["ORDER"]``.
``q=0`` excludes a coding, and an absent or empty header asks for identity.
zstd, brotli, and gzip are offered, in that order of preference.
A coding whose library is missing is left out, so a server without the brotli package answers a client that accepts both brotli and gzip with gzip.

gzip comes with the standard library, zstd from Python 3.14 and from the ``zstd`` extra below it, and brotli from the ``brotli`` extra.
``deflate`` is not offered.

Every compressed response carries ``Content-Encoding`` and ``Vary: Accept-Encoding``.
A strong ``ETag`` becomes weak, since the encoded bytes differ from the plain ones.
A response left plain for its media type, its size, or an exclusion carries no ``Vary``.
A ``HEAD`` request negotiates like ``GET`` and gets the same headers, with ``Content-Length`` dropped and the body untouched.

Buffered and streamed responses
-------------------------------

The middleware compresses a buffered response in one call, whether it is a page that arrives in one piece, a JSON answer, or any other Django view.
A body under ``MIN_SIZE`` stays plain, and a larger one is compressed once and kept only when the result is smaller, with ``Content-Length`` rewritten.
The media types come from ``CONTENT_TYPES``, matched exactly, by a ``type/*`` family, or by a structured suffix such as ``application/*+json``.
The patch envelope of a navigation, a zone, or an action, ``application/vnd.next.patches+json``, is therefore compressed like any JSON answer.

A streamed page, and a partial response that streams, compresses itself.
Each write is padded, compressed, and flushed, so the client decodes everything written so far and the shell is on screen before the last boundary settles.
Output that is ready within ``COMPRESSION["COALESCE"]`` of each other shares one write.
The middleware passes such a response through untouched.

A stream the framework does not know how to cut is wrapped by the middleware instead, and under ``STREAMS = "middleware"`` a document stream is left to it.
A sync stream the middleware wraps is compressed and flushed per non-empty chunk.
An async stream flushes at its first chunk, then once ``FLUSH_BYTES``, 16 KB of input, gathers or its source stays idle for ``COALESCE``, so a long export of small rows compresses as well as a buffered body.
A wrapped async event stream flushes every event.

Under ASGI a body or a chunk larger than 16 KB is compressed in a worker thread, so a large write never holds the event loop.

What is left alone
------------------

A response is not compressed when compression is off or when it already carries ``Content-Encoding``.
A ``1xx``, ``204``, ``206``, or ``304`` status, a ``Content-Range``, and a file response stay plain too, and so does ``Cache-Control: no-transform`` while ``RESPECT_NO_TRANSFORM`` holds.
A media type outside ``CONTENT_TYPES`` stays plain, and so does a response the ``EXCLUDE`` callable keeps.
An event stream follows ``SSE``, and a realtime topic stream follows ``SSE_TOPICS``, off by default, since one stream interleaves content other users wrote with the subscriber's own.
A request body is never decoded, so a compressed upload cannot unpack into a decompression bomb.

A page that sets ``no-transform`` streams plain, because a streamed page negotiates after its ``Cache-Control`` is final.
Django's ``GZipMiddleware`` below Django 6.1 holds a stream until it ends, so a client that takes gzip from it receives a page assembled in one piece, see :doc:`boundaries/streaming`.

Opting a page out
-----------------

Compressing a response that holds a secret beside input an attacker controls lets the attacker learn the secret from the compressed sizes.
Django masks its CSRF token per response, and next.dj adds filler of random length to each compressed body, which adds noise to each observed size.
A gzip body carries it in the file name field of its header, as Django's ``GZipMiddleware`` does.
A body in any coding ends with a comment for HTML, XML, CSS, JavaScript, and event streams, or with blanks for JSON, see :ref:`ref-compression-breach`.
A page that renders a secret beside reflected input opts out.

.. code-block:: python
   :caption: statements/pages/statement/page.py

   from next import page

   page.compress(enabled=False)

``page.compress(enabled=False)`` keeps every response of the page plain, the full page, its stream, and its zone answers.
``page.compress(enabled=True)`` states the default.
The call binds its own page alone, and the last call of a module wins.
A value that is no bool or a call inside a function raises when the module runs, and a call outside a ``page.py`` declares nothing and is reported as ``next.E245``.
A plain Django view takes the ``no_compress`` decorator, and the ``EXCLUDE`` setting names a callable that keeps a response plain by its request.

The ``COMPRESSION`` scope
-------------------------

``NEXT_FRAMEWORK["COMPRESSION"]`` holds the settings.
``ENABLED`` switches everything on and off, and ``MIN_SIZE`` and ``CONTENT_TYPES`` choose what is compressed.
``BACKENDS`` lists the codecs, each entry with the ``LEVEL`` and ``WINDOW`` that tune it in its ``OPTIONS``, and ``ORDER`` ranks them.
``STREAMS``, ``SSE``, ``SSE_TOPICS``, ``PADDING``, and ``COALESCE`` shape the streams, and ``RESPECT_NO_TRANSFORM`` and ``EXCLUDE`` keep responses plain.
Every key, its default, and its allowed values are in :doc:`/content/ref/settings`, and the checks that report a wrong value are in :doc:`/content/ref/system-checks`.

A codec is a ``CompressionBackend``, so a project adds one as a ``BACKENDS`` entry with its dotted path in ``BACKEND``, see :doc:`/content/ref/compression`.

Behind a proxy
--------------

The application sets ``Content-Encoding`` and ``Vary``, and a proxy that finds them passes the body through.
One layer compresses, either the application or the proxy.
A proxy that compresses everything it forwards is the reason to set ``ENABLED`` to ``False``, see :doc:`/content/deployment/wsgi-asgi`.

.. seealso::

   :doc:`/content/howto/enable-response-compression` for the steps and the tests.
   :doc:`/content/ref/compression` for the codecs, the middleware, and the stream encoder.
   :doc:`boundaries/streaming` for how a streamed page decides to compress itself.
   :doc:`caching` for ``Cache-Control`` and the shared-page rules compression leaves alone.
