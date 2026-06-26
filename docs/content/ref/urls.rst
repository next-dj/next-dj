.. _ref-urls:

URLs Reference
==============

Module Summary
--------------

``next.urls`` exposes the router backends ``RouterBackend`` and ``FileRouterBackend``.
It also exposes the ``RouterFactory`` and ``RouterManager`` that build and own them.
The ``URLPatternParser`` for bracket-segment parsing is part of the public surface.
It also exposes the ``page_reverse``, ``page_reverse_lazy``, and ``with_query`` reverse helpers, the ``get_multi_values`` query reader, and the Django integration name ``app_name``.
The parameter providers and the dependency markers ``DUrl`` (captured path segments) and ``DQuery`` (query string parameters) round out the public surface.

Public API
----------

Backends
~~~~~~~~

Every page view the file router generates carries a ``next_page_path`` attribute naming the page source, including the synthesised ``page.py`` location of a virtual ``template.djx`` route.
The form dispatcher reads it when it resolves a posted origin URL back to the page that re-renders after a validation failure.

.. automodule:: next.urls.backends
   :members:

Manager
~~~~~~~

``urlpatterns`` is a ``list`` subclass that recollects router and form-action patterns from the active backends on each access.
The backends themselves are cached by ``router_manager`` and are only rebuilt when ``router_manager.reload()`` runs or when ``PAGE_BACKENDS`` changes.
A route added after import is therefore visible without a process restart, but each access still iterates the cached backend list rather than walking the page tree again.
The ``list`` subclass overrides ``__reversed__`` so Django's resolver observes the recollected patterns rather than the empty internal buffer of the ``list`` base.
Django's resolver iterates ``reversed(urlpatterns)``, so the override feeds it the fresh patterns.
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

.. py:function:: next.urls.reverse.page_reverse_lazy(path_template="", *, namespace=app_name, **kwargs)

   Lazy variant of ``page_reverse``, the way :func:`~django.urls.reverse_lazy` pairs with :func:`~django.urls.reverse`.
   The URL resolves when the value is first coerced to ``str``,
   which makes it safe in positions evaluated at class-definition time,
   before the URLconf is ready,
   such as ``Meta.success_url`` on a form class.

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
     - Supplies a URL kwarg value by parameter name, coercing the raw string to the parameter annotation when one is present.
       ``DUrl``-annotated parameters are claimed by ``UrlByAnnotationProvider`` first.
   * - ``QueryParamProvider``
     - Supplies ``request.GET`` values for parameters annotated with ``DQuery[...]``.

See :doc:`/content/internals/di-resolver` for the full provider registration sequence and the resolution order.

``DUrl`` and ``DQuery`` both accept ``str``, ``int``, ``bool``, ``float``, ``UUID``, ``Decimal``, ``date``, and ``datetime``.
``DQuery`` additionally accepts ``list[T]`` for any of those scalars.

The following table is the canonical coercion reference.
A value that fails to parse falls back to the raw captured string rather than raising.

.. list-table::
   :header-rows: 1
   :widths: 25 35 40

   * - Annotation type
     - Accepted wire values
     - Result
   * - ``str``
     - Any captured string.
     - Returned unchanged.
   * - ``int``
     - Decimal digit string.
     - ``int(value)``.
   * - ``float``
     - Decimal float string.
     - ``float(value)``.
   * - ``bool``
     - ``"1"``, ``"true"``, ``"yes"`` map to ``True``, anything else to ``False``.
     - Boolean.
   * - ``UUID``
     - Canonical UUID string, or an already parsed :class:`~uuid.UUID`.
     - :class:`~uuid.UUID` instance.
   * - ``Decimal``
     - Numeric string parseable by :class:`~decimal.Decimal`.
     - :class:`~decimal.Decimal` instance.
   * - ``date``
     - ISO 8601 date accepted by :meth:`date.fromisoformat <datetime.date.fromisoformat>`.
     - :class:`~datetime.date` instance.
   * - ``datetime``
     - ISO 8601 datetime accepted by :meth:`datetime.fromisoformat <datetime.datetime.fromisoformat>`.
     - :class:`~datetime.datetime` instance.

See :doc:`/content/topics/dependency-injection` and :doc:`/content/topics/file-router` for the narrative coverage of each marker.

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
   Validates the ``NEXT_FRAMEWORK['PAGE_BACKENDS']`` structure, the ``BACKEND`` path, and per-backend ``DIRS``/``APP_DIRS``/``PAGES_DIR``/``OPTIONS`` keys.

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
