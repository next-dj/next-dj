.. _ref-urls:

URLs Reference
==============

Module Summary
--------------

``next.urls`` exposes the router backends ``RouterBackend`` and ``FileRouterBackend``, the ``RouterFactory`` and ``RouterManager`` that build and own them, the ``URLPatternParser`` for bracket-segment parsing, the ``page_reverse`` and ``with_query`` reverse helpers, the ``get_multi_values`` query reader, the Django integration name ``app_name``, the parameter providers, and the dependency markers ``DUrl`` (captured path segments) and ``DQuery`` (query string parameters).

Public API
----------

Backends
~~~~~~~~

.. automodule:: next.urls.backends
   :members:

Manager
~~~~~~~

``urlpatterns`` is a ``list`` subclass that rebuilds the router and form-action patterns on each access.
A route added after import is therefore visible without a process restart.
``RouterManager`` owns the active backend list, and the ``router_manager`` singleton exposes ``reload()`` to rebuild it.

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
     - Supplies the ``HttpRequest`` object for any parameter annotated ``HttpRequest`` or ``HttpRequest | None``.
   * - ``UrlByAnnotationProvider``
     - Supplies a URL kwarg value for parameters annotated with ``DUrl[...]``.
   * - ``UrlKwargsProvider``
     - Supplies raw URL kwargs by parameter name when no annotation is present.
   * - ``QueryParamProvider``
     - Supplies ``request.GET`` values for parameters annotated with ``DQuery[...]``.

Use ``get_multi_values(request, name)`` to read a multi-value query string parameter directly without going through the resolver.

See :doc:`/content/internals/di-resolver` for the full provider registration sequence and the resolution order.

Signals
-------

The URL subsystem fires two signals.

``route_registered``.
   Sent by ``FileRouterBackend`` once per registered route, with the ``url_path`` and ``file_path`` keyword arguments.

``router_reloaded``.
   Sent by the router manager class after the router rebuilds, with no keyword arguments.

See :doc:`signals` and :doc:`/content/topics/signals` for the wider signal catalog.

See Also
--------

.. seealso::

   :doc:`/content/topics/file-router` for the topic guide.
   :doc:`/content/topics/url-reversing` for the reverse helpers.
   :doc:`/content/internals/url-router` for the dispatcher internals.
