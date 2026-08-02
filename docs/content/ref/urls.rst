.. _ref-urls:

URLs reference
==============

Module summary
--------------

``next.urls`` exposes the router backends ``RouterBackend`` and ``FileRouterBackend``.
It re-exports ``PageRoot`` from :doc:`utils`, the labelled page tree a backend reports from ``page_roots`` for the system checks to walk and for the development watcher to observe.
It also exposes the ``RouterFactory`` and ``RouterManager`` that build and own them.
The ``URLPatternParser`` for bracket-segment parsing is part of the public surface.
It also exposes the ``page_reverse``, ``page_reverse_lazy``, and ``with_query`` reverse helpers, the ``get_multi_values`` query reader, and the Django integration name ``app_name``.
The ``TrieURLResolver`` that dispatches URL resolution through a route trie completes the routing surface.
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

``urlpatterns`` is a list holding a single ``TrieURLResolver`` that wraps a lazy sequence of router and form-action patterns.
:func:`~django.urls.include` therefore mounts one resolver, and the pattern collection is deferred to the first URL resolution instead of running while the root URLconf imports.
Code that reads ``next.urls.urlpatterns`` directly observes that one-element list, not the individual page patterns.
The wrapped sequence caches the concatenated pattern list against a pair of version counters, one owned by ``RouterManager`` and one by the form-action manager.
``router_manager.reload()`` bumps the router counter, and registering or clearing form actions through ``form_action_manager`` bumps the forms counter, so the next access rebuilds the list exactly when something changed.

The counters are read after the pattern build, because expanding page modules can register form actions mid-build, so the cache stays valid for the post-registration state.
A registration that bypasses the manager and writes into a backend directly is not tracked by the counters and does not appear in the cached list.
The backends themselves are cached by ``router_manager`` and are only rebuilt when ``router_manager.reload()`` runs or when ``PAGE_BACKENDS`` changes.
A page added on disk after that first collection needs ``router_manager.reload()``, which rebuilds the backends and clears the Django resolver cache.
Within a backend both the per-application pattern lists and the patterns from the roots configured in ``DIRS`` are memoised after the first scan, and a settings reload recreates the backend with fresh caches.

Reverse-name population iterates the wrapped sequence with ``reversed()``, which it answers through an explicit ``__reversed__`` that builds the pattern list once per pass.
``RouterManager`` owns the active backend list, and the ``router_manager`` singleton exposes ``reload()`` to rebuild it.
``reload()`` logs and skips a backend entry whose construction raises ``ValueError``, ``TypeError``, ``KeyError``, or ``ImportError``.
Any other exception from a custom backend propagates and stops startup.
``backends`` reads the loaded list as a tuple without loading anything, which is how the system checks walk the routers they were handed.
``version`` is the cache token the lazy urlpatterns concat keys on, bumped by every ``reload()``, and a caller that derives its own cache from the router set reads it for the same purpose.

.. automodule:: next.urls.manager
   :members:

Resolver
~~~~~~~~

``TrieURLResolver`` subclasses :class:`~django.urls.URLResolver` and narrows each ``resolve()`` call to a handful of candidate patterns.
A route without parameters hits a dictionary keyed by the full route string, and a parameterised route is collected by a walk over a trie of path segments.
The candidates are then tried with the standard ``pattern.resolve()`` in their original list order, so overlapping routes keep Django's first-match-wins semantics and converters behave as in plain Django.
A miss falls back to the inherited linear scan, which raises the canonical ``Resolver404`` with a complete ``tried`` list and also covers patterns the trie cannot index, such as ones built with :func:`~django.urls.re_path`.
On a successful match ``ResolverMatch.tried`` lists only the candidates that were actually tried, not every pattern preceding the winner.

The internal route index is versioned by the same counters as the pattern concat, so a router reload or a late form-action registration rebuilds it before the next resolution.
The ``URL_RESOLVER`` setting names the resolver class, so pointing it at ``django.urls.resolvers.URLResolver`` or a custom subclass replaces the trie dispatch.
See :doc:`/content/internals/url-router` for the algorithm walk-through.

.. automodule:: next.urls.resolver
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

Reverse helpers
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

Parameter providers
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

``check_url_patterns``.
   Collects patterns from every configured tree, application pages and root ``DIRS`` alike.
   Fails with :ref:`next.E015 <ref-system-checks>` when two file routes convert to exactly the same Django path string.
   Fails with :ref:`next.E028 <ref-system-checks>` when a route repeats a captured parameter name, listing every conflicting name.
   Reports :ref:`next.E016 <ref-system-checks>` when pattern collection from a router raises.

``check_reverse_name_collisions``.
   Fails with :ref:`next.E039 <ref-system-checks>` when two distinct routes collapse to the same reverse URL name.
   It reuses the same pattern collection but leaves collection errors to ``check_url_patterns``, so :ref:`next.E016 <ref-system-checks>` surfaces only once.

.. automodule:: next.urls.checks
   :members:
   :no-index:

See also
--------

.. seealso::

   :doc:`/content/topics/file-router` for the topic guide.
   :doc:`/content/topics/url-reversing` for the reverse helpers.
   :doc:`/content/internals/url-router` for the dispatcher internals.
