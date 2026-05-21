.. _ref-urls:

URLs Reference
==============

Module Summary
--------------

``next.urls`` exposes the router backends ``RouterBackend`` and ``FileRouterBackend``.
It also exposes the ``RouterFactory`` and ``RouterManager`` that build and own them.
The ``URLPatternParser`` for bracket-segment parsing is part of the public surface.
It also exposes the ``page_reverse`` and ``with_query`` reverse helpers, the ``get_multi_values`` query reader, and the Django integration name ``app_name``.
The parameter providers and the dependency markers ``DUrl`` (captured path segments) and ``DQuery`` (query string parameters) round out the public surface.

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

.. admonition:: Deep import path

   The names in ``next.urls.dispatcher`` are not re-exported from ``next.urls``.
   Import them through the submodule path when a custom backend or test needs to call them directly.

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

See :doc:`/content/internals/di-resolver` for the full provider registration sequence and the resolution order.

``DUrl`` and ``DQuery`` both accept ``str``, ``int``, ``bool``, ``float``, ``UUID``, ``Decimal``, ``date``, and ``datetime``.
``DQuery`` additionally accepts ``list[T]`` for any of those scalars.
See :doc:`/content/topics/dependency-injection` and :doc:`/content/topics/file-router` for the full coercion reference.

Signals
-------

The URL subsystem fires two signals.

``route_registered``.
   Sent by ``FileRouterBackend`` once per registered route, including virtual ``template.djx`` routes, with the ``url_path`` and ``file_path`` keyword arguments.

``router_reloaded``.
   Sent by the router manager class after the router rebuilds, with no keyword arguments.
   The sender is the ``RouterManager`` class.

See :doc:`signals` and :doc:`/content/topics/signals` for the wider signal catalog.

Checks
------

``next.urls.checks`` registers Django system checks that validate the URL configuration at startup.

``check_next_pages_configuration``.
   Validates the ``NEXT_FRAMEWORK['DEFAULT_PAGE_BACKENDS']`` structure, the ``BACKEND`` path, and per-backend ``DIRS``/``APP_DIRS``/``PAGES_DIR``/``OPTIONS`` keys.

``check_duplicate_url_parameters``.
   Fails with :ref:`next.E028 <ref-system-checks>` when one route repeats a captured parameter name.

``check_url_patterns``.
   Fails with :ref:`next.E015 <ref-system-checks>` when two file routes resolve to the same Django path string.

.. automodule:: next.urls.checks
   :members:
   :no-index:

See Also
--------

.. seealso::

   :doc:`/content/topics/file-router` for the topic guide.
   :doc:`/content/topics/url-reversing` for the reverse helpers.
   :doc:`/content/internals/url-router` for the dispatcher internals.
