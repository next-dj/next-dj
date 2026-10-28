.. _misc-glossary:

Glossary
========

Terms used throughout the next.dj documentation.

.. glossary::
   :sorted:

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

   asset name
      An asset reference carrying no scheme, no host, no query, and no fragment, whose path is relative.
      The pipeline resolves it through Django staticfiles, while every other reference reaches the document as written.
      See :doc:`/content/topics/static-assets/name-resolution`.

   backend
      A subsystem implementation named by a dotted path in ``NEXT_FRAMEWORK``.
      Used for the router, the components backend, the static backend chain, the form action chain, the form wizard drafts, the partial protocol, the sitemap sections, the consent store, the realtime broker, and the ticket binding.

   broker
      The realtime backend that numbers, stores, and fans out topic messages, ``MemoryBroker`` by default and ``RedisBroker`` across processes.
      Selected through ``NEXT_FRAMEWORK["REALTIME"]["BROKER"]``, see :ref:`topics-realtime`.

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

   ComponentFileWidget
      A ``ComponentWidget`` for a ``FileField`` or ``ImageField`` that reads its value from the uploaded files and makes the form multipart.
      A stored file reaches the component as ``value``, and an in-flight upload never does.

   context function
      A Python callable decorated with ``@context("key")`` that publishes a value to the template scope.
      Also written ``@context`` callable.

   declaring page.py
      A ``page.py`` with no body that declares access with ``inherit=True`` for the pages below its directory and routes no URL of its own.
      See :ref:`topics-access-declaring-page`.

   DI marker
      A typed annotation or default-value object that asks the resolver for a specific source of data.
      An annotation marker is written in the type position, for example ``param: DUrl[int]``, and covers ``DUrl``, ``DQuery``, and ``DForm``.
      A default-value marker is written as the parameter default, for example ``param: str = Context()``, and covers ``Context`` and ``Depends``.
      ``DObject`` is an annotation marker as well.

   DObject
      The annotation that injects the model row whose primary key a URL kwarg holds, read through ``visible_to(scope)``, and names the row of an access declaration as ``obj=DObject[Model]``.
      See :ref:`topics-di-dobject`.

   discovery
      The filesystem walk that builds the asset registry, the components registry, and the page registry at startup.

   dispatch
      The pipeline that turns a form submission into a handler invocation.
      A failed validation skips the handler and re-renders the origin page instead.

   dynamic permission hook
      A per-request access check declared on a form, ``check_permissions`` for the view level and ``has_object_permission`` for the bound instance.
      Both resolve through the dependency injector and return ``PermissionOutcome``, the ``bool | HttpResponse | None`` alias.
      They run as an additive layer after the static :term:`guard`.
      See :ref:`topics-forms-actions-dynamic-guards`.

   FormSpec
      A frozen dataclass that describes a form layout.
      Used to render forms in custom templates.

   FormWizard
      A multi-step form that routes a sequence of step forms across requests.
      Steps are declared as ``(name, FormClass)`` tuples in ``Meta.steps``, and ``done`` runs after the final step validates.

   form wizard backend
      The draft-persistence contract for a ``FormWizard``, a ``FormWizardBackend`` subclass that stores each step's cleaned data between requests.
      Selected through ``NEXT_FRAMEWORK["FORM_WIZARD_BACKEND"]``, with the bundled ``SessionFormWizardBackend`` as the default and ``CacheFormWizardBackend`` as the cache-backed alternative.

   framework island
      A Vue or React root mounted into one element of an otherwise server-rendered page.
      An adapter mounts it through ``Next.partial.onMount`` or the ``next:mounted`` event and unmounts it on ``next:removed``, so a partial update never leaks its listeners.
      See :doc:`/content/topics/partial-rendering/framework-islands`.

   guard
      The access requirement declared on an action through ``Meta.login_required`` and ``Meta.permission_required``, or the matching ``@action`` keywords.
      Stored as an ``ActionGuard`` on the registry metadata and enforced before the form is built.
      See :ref:`topics-forms-actions-guards`.

   inherit_context
      The ``inherit_context=True`` flag on ``@context`` in ``page.py``.
      Publishes the context value to every descendant page under that directory, not only to the page that declares it.

   intercepting route
      A directory named with a ``(.)``, ``(..)`` or ``(...)`` marker and a segment, whose page answers a zone request of the page it targets.
      The router never routes it.
      See :ref:`topics-file-router-intercepts`.

   layer
      A server-initiated overlay stacked above the current page, opened by the ``layer.open`` patch verb or a ``data-next-layer`` link.
      The client runtime hosts each layer in its own ``<dialog>`` element and closes the top layer on accept, dismissal, or Back.

   layout
      A ``layout.djx`` file in an ancestor directory.
      Wraps every descendant page.
      It holds markup alone, and the access of its directory and every page below it lives in the ``page.py`` of the directory, under ``inherit=True``.

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
      Examples include ``page``, ``components_manager``, ``router_manager``, and ``next.forms.manager.form_action_manager``.

   morph
      The default patch verb.
      Reconciles the target element in place instead of replacing it, and ``data-next-key`` lets it reuse the node a keyed child already owns.

   origin page
      The page that rendered a form.
      Identified at dispatch time by resolving the hidden ``_next_form_origin`` URL against the URLconf, which ignores the query string the field carries.

   outcome
      The ``ActionOutcome`` dataclass a form action backend produces from a dispatch, shaped into the HTTP response by ``shape_response``.
      See :doc:`/content/topics/forms/backends`.

   page
      A directory under the page root with a ``page.py``, or a virtual route with only a ``template.djx``.

   page metadata
      The title, the description, and the rest of the head a page declares through a ``metadata`` dict or a ``@page.metadata`` callable in ``page.py``, deep-merged down the page tree, resolved against the request, and rendered by ``{% metadata %}``.
      Only the pages the file router serves carry it.
      Distinct from the registry metadata of an action, which is the guard and the dispatch data stored on a registration.
      See :doc:`/content/topics/seo/metadata`.

   page root
      A directory that the router walks for page discovery.
      Comes from ``APP_DIRS`` and ``DIRS`` in ``PAGE_BACKENDS``.
      A ``sitemap.py``, ``robots.py``, ``robots.txt``, or ``scripts.py`` at its top serves the tree's crawler documents or declares its third-party scripts.

   robots source
      The one file a site serves ``/robots.txt`` from, a ``robots.py`` declaring ``RobotsRule`` groups or a static ``robots.txt`` served as written.
      A site has exactly one, and ``manage.py check`` reports a second.
      See :doc:`/content/topics/seo/robots`.

   sitemap section
      One Django sitemap served at ``/sitemap-<section>.xml``, the static routes and items of a page root, a route its items move with ``section=``, or a section a sitemap backend adds.
      ``/sitemap.xml`` is the section itself while there is one, and an index of every section otherwise.
      See :doc:`/content/topics/seo/sitemaps`.

   multi-project layout
      Multiple Django applications or explicit ``DIRS`` entries each contributing page trees while optionally sharing component directories through ``COMPONENT_BACKENDS``.
      See :doc:`/content/topics/multi-project`.

   partial request
      A request the client runtime marks with the ``X-Next-Request`` header.
      ``is_partial_request`` detects it on the server, so a handler can answer with a patch envelope instead of a redirect.

   patch
      One addressed DOM operation inside a patch envelope.
      Carries a verb such as ``morph`` or ``remove``, a target selector, and optional HTML.

   patch envelope
      The wire object of a partial response.
      Carries the asset version, the ordered patch ops, the co-located assets of the rendered targets, and the machine-readable form meta.

   stream frame
      One message of a realtime stream, such as ``next-hello``, ``next-patches``, ``next-skip``, or ``next-end``.
      A ``next-patches`` frame carries ``topic``, ``epoch``, ``seq``, and ``server_time`` beside the patch envelope, and SSE and WebSocket carry the same frames in their own wire framing.
      See :doc:`/content/topics/realtime`.

   epoch
      The random token a broker draws whenever the counter of a topic starts again, after a restart, an evicted key, or a flushed cache.
      A ``seq`` is unique only within its epoch, and a new epoch tells the client to resync, see :ref:`topics-realtime`.

   presence
      The live count or member list of the streams open on a topic, which a broker that implements ``PresenceBroker`` maintains.
      See :ref:`topics-realtime`.

   replay
      The bounded best-effort catch-up of the messages a reconnecting stream missed, which the broker keeps per topic.
      See :ref:`topics-realtime`.

   ticket
      A signed token that carries the grant of a stream from the guarded page to the WebSocket handshake.
      It is short-lived under ``NEXT_FRAMEWORK["REALTIME"]["TICKET_MAX_AGE"]``, see :ref:`topics-realtime`.

   ticket binding
      The rule that ties a ticket to the identity of the browser, a ``TicketBinding`` subclass named by ``NEXT_FRAMEWORK["REALTIME"]["TICKET_BINDING"]``.
      ``SessionBinding`` is the default, see :ref:`topics-realtime`.

   transport
      The connection a realtime stream travels over, SSE or WebSocket, which the page negotiates from ``NEXT_FRAMEWORK["REALTIME"]["TRANSPORTS"]``.
      Both carry the same frames, and SSE takes over when negotiation or the socket fails, see :ref:`topics-realtime`.

   tunnel
      The path that carries a form action over the open WebSocket for an action declared with ``transport="any"``.
      See :ref:`topics-realtime-tunnel`.

   upstream event
      A small event a page sends to the server over its WebSocket, delivered to a handler registered with ``@on``.
      See :ref:`topics-realtime-upstream`.

   realtime topic
      A named broker channel to which an application publishes safe patch operations or events.
      A topic notification is ephemeral and not a durable event log.
      See :doc:`/content/topics/realtime`.

   sequence number
      The per-topic ``seq`` value used to detect duplicate or missed realtime messages.
      It is unique within an ``epoch``, the random token a broker draws whenever a topic counter starts again.
      A gap or an epoch change causes the client to re-fetch its bound zones over guarded HTTP.

   resync
      The guarded HTTP zone request that restores current state after a realtime gap or reconnect.
      The server renders the response for each subscriber and its own cookies.

   provider
      A class that produces a value for a parameter.
      Implements ``can_handle`` and ``resolve``.

   request cache
      The dependency cache a form dispatch attaches to the request, shared by its handler and by the context functions, metadata callables, and components of the re-render.

   re-render
      The dispatch path that re-renders the origin page after a failed form validation.

   resolver
      The singleton ``DependencyResolver`` that fills parameters from providers.

   route name
      The Django URL name string inside the ``next`` namespace (``app_name`` on ``next.urls``).
      Values come from ``NEXT_FRAMEWORK["URL_NAME_TEMPLATE"]``, default ``"page_{name}"``, where ``{name}`` is derived from the normalized filesystem path.
      Reverse from templates as ``{% url 'next:page_notes_int_id' id=note.id %}`` for a ``notes/[int:id]`` directory or from Python through :doc:`/content/topics/url-reversing`.

   route group
      A directory named with an identifier in parentheses, such as ``(auth)``.
      It adds nothing to the URL or the URL name and scopes layouts, access, context, and components below it.
      See :ref:`topics-file-router-route-groups`.

   scope
      The keying rule for an action name, ``page`` or ``shared``.
      A page-scoped action is keyed to the absolute path of its declaring ``page.py`` or ``component.py``, while a shared action is keyed to its dotted module name and is reachable project-wide.
      The resulting key is the scope key that the UID hashes together with the action name.
      See :doc:`/content/topics/forms/actions`.

   scope dependency
      The named dependency a project registers with ``@resolver.dependency("scope")``, which ``DObject`` passes to ``visible_to`` on the default manager of a model.
      See :ref:`topics-di-dobject`.

   signal
      A Django signal emitted by one subsystem.
      Subscribers react without subclassing.

   JS context serializer
      Implementations of ``next.static.JsContextSerializer`` that encode values for ``window.Next.context``.
      Not the frozen form specs in ``next.forms.serializers``.

   slot
      A named area inside a component template filled with caller content through the block form ``{% #component "name" %}`` … ``{% /component %}``, see :doc:`/content/topics/components`.
      The bare word means this mechanism and no other.
      The static collector's named collections are :term:`collector slots <collector slot>`, and the ``{% template %}`` opening a layout leaves for the page body is a layout placeholder, see :doc:`/content/topics/layouts`.

   stem
      The filename without the extension.
      The recognised stem is ``component`` for a component file, ``layout`` for a layout file, and ``template`` for a page template file.
      No ``page`` stem is registered by default.
      A project adds one under the ``template`` role, see :doc:`/content/topics/static-assets/custom-stems`.

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

   zone
      A named slice of a page template wrapped in ``{% zone %}``.
      The server re-renders the slice standalone, and patches address it by its name.

   zone batch
      The tuple of zone names one partial request asks the server to render, carried in the ``X-Next-Zone`` header.
      A full page render carries no batch, which is what tells a page callable that every zone is about to render, see :doc:`/content/topics/context`.

   root layout
      A ``layout.djx`` that opens with the ``{% layout root %}`` marker.
      Composition stops at it, so no layout further out wraps the pages below it, see :doc:`/content/topics/layouts`.

   layout placeholder
      The ``{% template %}`` opening a ``layout.djx`` leaves for the wrapped body.
      Composition fills the first one a layout carries, and a layout that carries none is dropped whole, see :doc:`/content/topics/layouts`.

   consent category
      A name in ``NEXT_FRAMEWORK["CONSENT"]["CATEGORIES"]`` a script or a ``{% #consented %}`` block waits for, ``necessary`` always granted and every other one denied until the visitor chooses.
      See :doc:`/content/topics/scripts/consent`.

   RESET
      The metadata marker that drops an inherited value at any depth, ``Replace()`` with no value.
      See :doc:`/content/topics/seo/merge`.

   shared page
      A page whose ``cache`` lets a shared cache such as a CDN keep its response, through ``public`` or ``s_maxage``, so its HTML carries no CSRF token and shows no visitor's consent.
      A render that depends on one visitor anyway, through a cookie, the session, a CSP nonce, or an ``Authorization`` header, is sent with ``Cache-Control: private``.
      See :doc:`/content/topics/caching`.

   site indexability
      The one answer of ``NEXT_FRAMEWORK["SITE"]["INDEXABLE"]`` every robots meta, ``X-Robots-Tag``, sitemap, and robots file follows, ``not DEBUG`` by default.
      See :doc:`/content/topics/seo/site`.

See also
--------

.. seealso::

   :doc:`/content/intro/overview` for the introductory mental model.
   :doc:`/content/internals/overview` for the subsystem map.
