.. _misc-glossary:

Glossary
========

Terms used throughout the next.dj documentation.

.. glossary::

   action
      A registered entry point for a form POST.
      Form classes register automatically through ``__init_subclass__`` with a ``snake_case`` name derived from the class name.
      Form-less functions register through ``@action("name")``.
      The framework derives a stable dispatch URL from the action name.

   asset
      A file registered with the static pipeline.
      Has a stem, an extension, and an owner (page, layout, or component).

   asset kind
      A combination of extension and renderer that decides how an asset becomes HTML.
      Bundled kinds are ``css``, ``js``, and ``module``.

   backend
      A subsystem implementation registered through ``NEXT_FRAMEWORK``.
      Used for the router, the components backend, the static backend chain, and the form action chain.

   collector slot
      A named collection inside the static collector.
      ``{% collect_styles %}`` emits the ``styles`` slot, ``{% collect_scripts %}`` emits the ``scripts`` slot.

   collector
      The request scoped object that accumulates assets touched by the current render.

   component
      A reusable template fragment under the components root.
      Has a template and optional Python module.

   ComponentWidget
      A form widget that renders a field through a registered next.dj component instead of a Django widget template.
      One field maps to one component, and the component owns the markup.

   context function
      A Python callable decorated with ``@context("key")`` that publishes a value to the template scope.

   DI marker
      A typed annotation or default-value object that asks the resolver for a specific source of data.
      An annotation marker is written in the type position, for example ``param: DUrl[int]``, and covers ``DUrl``, ``DQuery``, and ``DForm``.
      A default-value marker is written as the parameter default, for example ``param: str = Context()``, and covers ``Context`` and ``Depends``.

   discovery
      The filesystem walk that builds the asset registry, the components registry, and the page registry at startup.

   dispatch
      The pipeline that turns a form submission into a handler invocation.
      A failed validation skips the handler and re-renders the origin page instead.

   FormSpec
      A frozen dataclass that describes a form layout.
      Used to render forms in custom templates.

   FormWizard
      A multi-step form that routes a sequence of step forms across requests.
      Steps are declared as ``(name, FormClass)`` tuples in ``Meta.steps``, and ``done`` runs after the final step validates.

   form wizard backend
      The draft-persistence contract for a ``FormWizard``, a ``FormWizardBackend`` subclass that stores each step's cleaned data between requests.
      Selected through ``NEXT_FRAMEWORK["FORM_WIZARD_BACKEND"]``, with the bundled ``CacheFormWizardBackend`` as the default.

   inherit_context
      The ``inherit_context=True`` flag on ``@context`` in ``page.py``.
      Publishes the context value to every descendant page under that directory, not only to the page that declares it.

   layout
      A ``layout.djx`` file in an ancestor directory.
      Wraps every descendant page.

   JS context policy
      Algorithm class that resolves duplicate serialised keys for ``window.Next.context``.
      Selected through ``JS_CONTEXT_POLICY`` inside static backend ``OPTIONS``, see :doc:`/content/topics/static-assets/js-context`.

   NextScriptBuilder
      Constructs the ``next.min.js`` tag, preload link, and ``Next._init`` shell.
      Controlled through ``NEXT_FRAMEWORK["NEXT_JS_OPTIONS"]``.

   ScriptInjectionPolicy
      Controls whether the framework injects the runtime bundle automatically (``AUTO``), skips injection (``DISABLED``), or leaves placement to your templates (``MANUAL``).

   manager
      The singleton orchestrator for one subsystem.
      Examples include ``page``, ``components_manager``, ``router_manager``, ``form_action_manager``.
      ``page`` is the outlier without a ``_manager`` suffix because the rendering manager is exposed as a decorator-style facade rather than a named singleton.

   origin page
      The page that rendered a form.
      Identified at dispatch time by resolving the hidden ``_next_form_origin`` URL path against the URLconf.

   page
      A directory under the page root with a ``page.py``, or a virtual route with only a ``template.djx``.

   page root
      A directory that the router walks for page discovery.
      Comes from ``APP_DIRS`` and ``DIRS`` in ``PAGE_BACKENDS``.

   multi-project layout
      Multiple Django applications or explicit ``DIRS`` entries each contributing page trees while optionally sharing component directories through ``COMPONENT_BACKENDS``.
      See :doc:`/content/topics/multi-project`.

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
      The Django URL name string inside the ``next`` namespace (``app_name`` on ``next.urls``).
      Values come from ``NEXT_FRAMEWORK["URL_NAME_TEMPLATE"]``, default ``"page_{name}"``, where ``{name}`` is derived from the normalized filesystem path.
      Reverse from templates as ``{% url 'next:page_notes_id' id=note.id %}`` or from Python through :doc:`/content/topics/url-reversing`.

   signal
      A Django signal emitted by one subsystem.
      Subscribers react without subclassing.

   JS context serializer
      Implementations of ``next.static.JsContextSerializer`` that encode values for ``window.Next.context``.
      Distinct from frozen form specs in ``next.forms.serializers``.

   slot
      A named area inside a component template filled with caller content through the block form of ``{% component %}``.

   stem
      The filename without the extension.
      The recognised stem is ``component`` for a component file, ``layout`` for a layout file, and ``template`` for a page template file.
      There is no ``page`` stem.

   template loader
      A ``TemplateLoader`` subclass registered through ``NEXT_FRAMEWORK["TEMPLATE_LOADERS"]`` that supplies template text for a ``page.py`` path.
      See :doc:`/content/howto/add-a-custom-template-loader`.

   strategy
      A swappable algorithm such as ``DedupStrategy`` for static deduplication.

   UID
      The 16 character hash of an action's scope key and name that becomes part of the dispatch URL.

   virtual route
      A directory with only ``template.djx`` and no ``page.py``.
      No Python module is invoked for the route itself, but ancestor ``layout.djx`` files still wrap it and co-located static assets are still collected.

See Also
--------

.. seealso::

   :doc:`/content/intro/overview` for the introductory mental model.
   :doc:`/content/internals/overview` for the subsystem map.
