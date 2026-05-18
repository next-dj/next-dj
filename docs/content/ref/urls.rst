.. _ref-urls:

URLs Reference
==============

Module Summary
--------------

``next.urls`` exposes the file router backend, the dispatcher, URL reverse helpers, and dependency markers ``DUrl`` (captured path segments) and ``DQuery`` (query string parameters).

Public API
----------

Backends
~~~~~~~~

.. automodule:: next.urls.backends
   :members:

Manager
~~~~~~~

.. automodule:: next.urls.manager
   :members:

Parser
~~~~~~

.. automodule:: next.urls.parser
   :members:

Dispatcher
~~~~~~~~~~

.. automodule:: next.urls.dispatcher
   :members:

Reverse Helpers
~~~~~~~~~~~~~~~

.. autofunction:: next.urls.reverse.page_reverse

.. autofunction:: next.urls.reverse.with_query

Markers
~~~~~~~

.. automodule:: next.urls.markers
   :members:

Parameter Providers
~~~~~~~~~~~~~~~~~~~

The following provider classes are registered with the ``next.deps`` resolver at startup.
They are exported from ``next.urls`` for introspection and for authors writing custom providers that delegate to them.

.. list-table::
   :header-rows: 1
   :widths: 35 65

   * - Provider
     - What it supplies
   * - ``HttpRequestProvider``
     - Supplies the ``HttpRequest`` object for any parameter annotated or named ``request``.
   * - ``UrlByAnnotationProvider``
     - Supplies a URL kwarg value for parameters annotated with ``DUrl[...]``.
   * - ``UrlKwargsProvider``
     - Supplies raw URL kwargs by parameter name when no annotation is present.
   * - ``QueryParamProvider``
     - Supplies ``request.GET`` values for parameters annotated with ``DQuery[...]``.

Use ``get_multi_values(request, key)`` to read a multi-value query string parameter directly without going through the resolver.

See :doc:`/content/internals/di-resolver` for the full provider registration sequence and the resolution order.

Signals
-------

See :doc:`signals` and :doc:`/content/topics/signals` for the URL signals (``route_registered``, ``router_reloaded``).

See Also
--------

.. seealso::

   :doc:`/content/topics/file-router` for the topic guide.
   :doc:`/content/topics/url-reversing` for the reverse helpers.
   :doc:`/content/internals/url-router` for the dispatcher internals.
