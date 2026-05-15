.. _misc-glossary:

Glossary
========

Terms used throughout the next.dj documentation.

.. glossary::

   action
      A Python callable registered with ``@action("name")``.
      Receives a stable URL and dispatches form submissions.

   asset
      A file registered with the static pipeline.
      Has a stem, an extension, and an owner (page, layout, or component).

   asset kind
      A combination of extension and renderer that decides how an asset becomes HTML.
      Bundled kinds are ``css``, ``js``, and ``module``.

   backend
      A subsystem implementation registered through ``NEXT_FRAMEWORK``.
      Used for the router, the components backend, the static backend chain, and the form action chain.

   bucket
      A named collection slot inside the static collector.
      ``{% collect_styles %}`` emits the ``styles`` bucket, ``{% collect_scripts %}`` emits the ``scripts`` bucket.

   collector
      The request scoped object that accumulates assets touched by the current render.

   component
      A reusable template fragment under the components root.
      Has a template and optional Python module.

   context function
      A Python callable decorated with ``@context("key")`` that publishes a value to the template scope.

   DI marker
      A type annotation or default argument that asks the resolver for a specific source of data.
      Includes ``DUrl``, ``DQuery``, ``DForm``, ``Depends``, and ``Context``.

   discovery
      The filesystem walk that builds the asset registry, the components registry, and the page registry at startup.

   dispatch
      The pipeline that turns a form submission into a handler invocation.

   FormSpec
      A frozen dataclass that describes a form layout.
      Used to render forms in custom templates.

   inherit context
      The flag on ``@context`` that publishes a value to descendant pages, not only to the layout that declares it.

   layout
      A ``layout.djx`` file in an ancestor directory.
      Wraps every descendant page.

   manager
      The singleton orchestrator for one subsystem.
      Examples include ``page``, ``components_manager``, ``router_manager``, ``form_action_manager``.

   origin page
      The page that rendered a form.
      Identified through the hidden ``_next_form_page`` field at dispatch time.

   page
      A directory under the page root with a ``page.py``, or a virtual route with only a ``template.djx``.

   page root
      A directory that the router walks for page discovery.
      Comes from ``APP_DIRS`` and ``DIRS`` in ``DEFAULT_PAGE_BACKENDS``.

   provider
      A class that produces a value for a parameter.
      Implements ``can_handle`` and ``resolve``.

   request cache
      The dependency cache that lives on the request between context functions, components, and form re-render.

   re-render
      The dispatch path that re-renders the origin page after a failed form validation.

   resolver
      The singleton ``DependencyResolver`` that fills parameters from providers.

   route name
      The URL name in the ``next`` namespace, computed from the directory path.
      Default format ``next:page_<segments>``.

   signal
      A Django signal emitted by one subsystem.
      Subscribers react without subclassing.

   slot
      A named area inside a component template filled with caller content through the block form of ``{% component %}``.

   stem
      The filename without the extension.
      Recognised stems for components are ``component``, for layouts ``layout``, for pages ``template``.

   strategy
      A swappable algorithm such as ``DedupStrategy`` for static deduplication.

   UID
      The 16 character hash of an action name that becomes part of the dispatch URL.

   virtual route
      A directory with only ``template.djx`` and no ``page.py``.
      Renders directly without invoking Python.

See Also
--------

.. seealso::

   :doc:`/content/intro/overview` for the introductory mental model.
   :doc:`/content/internals/overview` for the subsystem map.
