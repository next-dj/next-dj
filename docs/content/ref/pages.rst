.. _ref-pages:

Pages reference
===============

Module summary
--------------

``next.pages`` exposes the ``Page`` coordinator and its ``page`` singleton, which also carries the access declarations of a ``page.py``, the ``@context`` decorator, the ``Context`` and ``ContextResult`` value objects, the ``PageModuleImportError`` raised by a broken ``page.py`` and the ``PageContextShapeError`` raised by a keyless ``@context`` answering no mapping, and the ``checks`` and ``signals`` submodules.
The ``TemplateLoader`` contract a custom body source subclasses is exported as well, see `Loaders`_ below.
It also exposes the page metadata names a page module uses most, the ``ResolvedMetadata`` value, the ``MetadataDict`` input shape, the ``Replace`` and ``RESET`` markers, the ``ld`` nodes, the ``MetadataRenderer`` contract with its ``HtmlMetadataRenderer``, and the four ``PageMetadata*`` errors, see :doc:`metadata`.
The ``CacheDict`` and ``HeadersDict`` shapes of the ``cache`` and ``headers`` a ``page.py`` declares, and the ``CacheControl`` value a ``cache`` normalises to, complete the surface, see `Response policy`_ below.

Public API
----------

.. autoclass:: next.pages.Page
   :members:

.. autoclass:: next.pages.Context
   :members:

.. autoclass:: next.pages.ContextResult
   :members:

.. autofunction:: next.pages.context

.. note::

   The ``serialize`` and ``serializer`` keyword arguments opt a value into the JS context.
   See the topic guide for details.

.. autodata:: next.pages.page
   :no-value:

   The process-wide ``Page`` coordinator that every router-built view renders through.
   ``Page`` takes ``login_required``, ``permission_required``, and ``public`` from ``PageDeclarations``, documented with ``next.pages.access`` below.
   It takes ``atomic``, ``defer``, ``compress``, and ``router`` from ``DeclaringCalls``, typed methods that hand each call to its registered declaration, see `Declaration registry`_.

Access declarations
~~~~~~~~~~~~~~~~~~~

``page.login_required(*, inherit=False)``, ``page.permission_required(perm, *, obj=None, inherit=False)``, and ``page.public(*, inherit=False)`` declare the access of the page of the ``page.py`` that calls them, and with ``inherit=True`` of every page below its directory as well, see :doc:`/content/topics/access`.
``perm`` is a permission name or a tuple of names, and ``obj`` is ``DObject[Model]``, whose row loads through ``visible_to(scope)`` by the URL kwarg named after the ``model_name`` of the model.
Any other ``perm``, and an ``obj`` that is a string, a bare model, or a ``DObject`` with a parent, raises ``TypeError`` at the call.
``public()`` lifts what every ``page.py`` above requires and the default login, for its own page or under ``inherit=True`` for the pages below, and a requirement declared below binds again.
A public rule sets the ``login_required`` attribute of the page view to ``False``, which ``LoginRequiredMiddleware`` reads.
A view answers a refusal with a redirect to the login page for an anonymous user, :exc:`~django.core.exceptions.PermissionDenied` for a missing permission, a 404 for a row the scope does not see, and the import failure, a 500, for a chain that holds a broken file, see :ref:`topics-permissions-tree`.

``next.pages.access`` reads the rule of each page once per reload of a file in its chain.
``page_access(file_path)`` returns a ``PageAccess`` with the joined ``rule`` and the first ``broken`` file, and ``owner_access(file_path)`` the rule of the page in the directory of any file of a tree, which an action answers to.
The joined ``rule`` holds the requirements in the order the chain declared them and ``sources`` naming the files of the requirements, or the file that opened a public rule.
``enforce(rule, broken, request, url_kwargs, dep_cache, *, undeclared, gate)`` is the one check a page view, a zone morph, and an action dispatch run, and ``refusal(file_path, request, url_kwargs, dep_cache)`` runs it for one page.
An ``AccessGate`` carries the ``lookup`` builder and the ``next_url`` a login returns to.
``url_lookup(request, url_kwargs, dep_cache)`` builds the ``lookup`` of a page, which loads the row an ``obj=DObject[Model]`` names from the same cache a ``DObject`` parameter reads.
``default_login(rule)`` tells whether the default login asks a login of a rule, true only for a rule without requirements that is not public while ``LOGIN_REQUIRED`` or ``LoginRequiredMiddleware`` turns it on, and ``anonymous_admitted(access)`` tells whether an anonymous request passes the rule of a page, which the sitemap reads to leave closed pages out.
``routed(file_path)`` tells whether a ``page.py`` serves a URL, the same answer the router gives, and ``routed_pages(pages)`` keeps only such files of a walk of ``(trail, page_file)`` pairs, so the URL checks and the SEO discovery treat no file without a body as a route.
Both read ``has_body(file_path, module)`` of ``next.pages.loaders``, the one criterion of a body, a ``render`` function, a ``template`` string, or a registered loader match, where a ``layout.djx`` counts for none.

.. automodule:: next.pages.access
   :exclude-members: refusal

Transaction declaration
~~~~~~~~~~~~~~~~~~~~~~~

``page.transaction(*, atomic, aliases=None)`` at the top of a ``page.py`` decides whether its page runs inside a request transaction, whatever the method, and wins over ``TRANSACTIONS["SAFE"]``, ``TRANSACTIONS["UNSAFE"]``, and ``TRANSACTIONS["RULE"]``, see ``TRANSACTIONS`` in :doc:`settings`.
``page.transaction(atomic=True)`` wraps every request of the page, a ``GET`` included, ``page.transaction(atomic="writes")`` wraps it from right before its first write, and ``page.transaction(atomic=False)`` leaves every request of it, a ``POST`` included, in autocommit.
``aliases`` of ``None`` takes ``TRANSACTIONS["ALIASES"]``, ``"all"`` takes every alias ``DATABASES`` declares, and a list or a tuple names the aliases to wrap.
The declaration binds its own page alone, the pages below its directory follow their own ``page.py``, and a full page, a zone, a boundary, and a navigation request of the page all read it.
The form actions the page declares do not read it and take their own choice through ``@action(atomic=...)`` or ``Meta.atomic``, see :doc:`forms`.
The last call of a module wins, and an edited ``page.py`` that drops the call stops applying it once the module loads again.
A mode other than ``True``, ``False``, or ``"writes"``, or an ``aliases`` value of another shape, raises :exc:`~django.core.exceptions.ImproperlyConfigured` when the module runs, naming the file and the values it takes, a call inside a function raises ``DeclarationOutsideModuleError``, and a call outside a ``page.py`` is reported by ``next.E240``.

Boundary declaration
~~~~~~~~~~~~~~~~~~~~

``page.defer(*, timeout=None, parallel=True)`` at the top of a ``page.py`` bounds the job of the page boundary a ``loading.djx`` above the page declares, and ``parallel=False`` keeps that job on the request thread.
The last call of a module wins, a value outside its shape or a call inside a function raises when the module runs, and a call outside a ``page.py`` declares nothing and is reported by ``next.E222``.
See :doc:`defer` and :doc:`/content/topics/boundaries/loading`.

Every page view is marked with :func:`~django.db.transaction.non_atomic_requests` on every alias, so ``ATOMIC_REQUESTS`` never wraps it, settles the access check of the page before it opens its transaction, and closes the transaction once the response object exists.
An exception leaving the view rolls the transaction back, and a returned response commits it whatever its status.

.. automodule:: next.pages.transactions
   :members:

Router declaration
~~~~~~~~~~~~~~~~~~

``page.router(*, navigate=True, prefetch=True, snapshot=True)`` at the top of a ``page.py`` declares how the client router treats its page.
``navigate=False`` answers every navigation with a hard load, ``prefetch=False`` refuses a prefetch with ``204`` before any render, and ``snapshot=False`` keeps the page out of the history snapshots.
The last call of a module wins, a flag that is no bool or a call inside a function raises when the module runs, and a call outside a ``page.py`` declares nothing and is reported by ``next.E238``.
See :doc:`router` and :doc:`/content/topics/router/index`.

Compression declaration
~~~~~~~~~~~~~~~~~~~~~~~

``page.compress(*, enabled)`` at the top of a ``page.py`` keeps every response of its page plain for ``False`` and states the default for ``True``, a page that renders a secret beside reflected input opting out against BREACH.
The last call of a module wins, a value that is no bool, a flag passed by position, or a call inside a function raises when the module runs, and a call outside a ``page.py`` declares nothing and is reported by ``next.E245``.
The choice reaches the response through the ``compress`` field of ``ResponsePolicy``, see :doc:`compression`.

.. code-block:: python
   :caption: pages/statement/page.py

   from next import page

   page.compress(enabled=False)

Declaration registry
~~~~~~~~~~~~~~~~~~~~

A ``PageDeclaration`` of ``next.pages.declarations`` describes one ``page.<name>()`` call, its ``name``, the ``parse`` function that turns its arguments into a value or raises ``ValueError`` naming the problem, the ``default`` a page without the call reads, and an ``Inherit`` rule.
``Inherit.OWN`` binds the declaring page alone, ``Inherit.NEAREST`` gives every page the value of the closest ``page.py`` at or above it that makes the call, and ``Inherit.MERGE`` folds the values of every such ``page.py``, the root first, through the ``merge`` function the declaration takes.
``page_declarations``, a ``PageDeclarationRegistry``, holds every registered declaration by name, and ``register_declaration(declaration)`` adds one, which the app that owns it calls in ``AppConfig.ready()``.
A declaration holds the last call of each ``page.py``, a later call winning, and a reload runs the module in a new namespace, so a call the edit removed stops applying.
A call off the top level of its module raises ``DeclarationOutsideModuleError``, one with a value outside its shape raises ``ImproperlyConfigured`` naming the file, and one made outside a ``page.py`` is kept apart for the check of the owning area.
``declaration.of(file_path)`` reads the value of a page once per module load, and ``reset_page_registrations()`` drops every call through ``page_declarations.reset()``.
The registry carries a ``version`` that bumps on every change, which the plans of the page views key on.

next.dj registers four declarations.

.. list-table::
   :header-rows: 1

   * - Call
     - Described in
     - Checked in
     - Inheritance
   * - ``page.transaction()``
     - ``next.pages.transactions.transaction_declaration``
     - ``next.transactions.checks`` (``next.E240``)
     - Own page
   * - ``page.compress()``
     - ``next.pages.compression.compress_declaration``
     - ``next.compression.checks`` (``next.E245``)
     - Own page
   * - ``page.defer()``
     - ``next.pages.defer.defer_declaration``
     - ``next.defer.checks.declarations`` (``next.E222``)
     - Own page
   * - ``page.router()``
     - ``next.router.declarations.router_declaration``
     - ``next.router.checks.pages`` (``next.E238``)
     - Own page

A project adds its own call the same way, see :doc:`/content/howto/add-a-page-declaration`.

.. automodule:: next.pages.declarations
   :members: PageDeclaration, PageDeclarationRegistry, Inherit, register_declaration

Cross-area contract
~~~~~~~~~~~~~~~~~~~

Fourteen ``Page`` methods and the ``contexts`` property carry no leading underscore because other framework areas call them, not because application code should.
``composed_template_for``, ``build_render_context``, ``render_with_static_assets``, ``authorization_outcome``, ``has_template``, ``zone_bindings``, ``create_url_pattern``, ``render``, ``clear_template_caches``, ``static_metadata``, ``fold_metadata``, ``metadata_chain``, ``metadata_declaration``, and ``metadata_registrations`` serve ``next.forms``, ``next.partial``, ``next.urls``, ``next.testing``, ``next.seo``, the metadata checks, and the ``showmetadata`` command.
``contexts`` hands the registry of every ``@context`` to the checks, the run order of a page included.
``next.forms`` and ``next.partial`` read the first six, ``next.urls`` builds every page pattern through ``create_url_pattern``, and ``next.testing`` calls ``render`` and ``clear_template_caches`` from its rendering and isolation helpers.
``next.partial`` folds the ``meta`` patch verb through ``fold_metadata``, ``next.seo`` and the response policy read ``static_metadata``, and the checks and ``showmetadata`` read the chain through the last three.
They follow the underscore rule of :doc:`/content/faq/general`, so they are safe from removal without notice.
They do not carry the application-facing stability of a Stable tier, and their signatures may drift as partial rendering evolves.
``register_template`` is called by ``composed_template_for`` on every cache miss and by no other area, so it serves application code seeding a composed body under the same underscore rule, again without the Stable tier's signature guarantee.
``resolve_page_body`` and ``render_composed`` carry no underscore because the routed views of ``next.views.pages`` call them, and ``reset_metadata`` because ``reset_metadata_registry`` and the test isolation helpers reset the registry through it.
They follow the same underscore rule without the Stable tier's signature guarantee.

Manager
~~~~~~~

``next.pages.manager`` is a package.
Its ``__init__`` holds ``Page`` and the ``page`` singleton, assembled from one mixin per concern.
``containment`` holds how a page contains a failure of its callables, ``render`` how it builds its render context, ``gating`` how a page with a ``loading.djx`` builds its shell context, ``bodies`` how it resolves its body, and ``registrations`` what a ``page.py`` registers on it, its context and its metadata.
``templates`` holds the per-page template cache, and ``patterns`` holds the ``URLPattern`` factory the file router mounts the routed views of ``next.views`` through.
``Page`` and ``page`` are documented once, under `Public API`_, with the names ``next.pages`` exports.

.. automodule:: next.pages.manager
   :members:
   :exclude-members: Page, page, context, layout, resolver, Built

.. automodule:: next.pages.manager.render
   :members: Built

.. automodule:: next.pages.manager.templates
   :members:

Page boundaries
~~~~~~~~~~~~~~~

``next.pages.jobs`` renders the boundary of a page.
``page_boundary(source, context, ident=None)`` returns the error boundary of an ``error.djx``, ``contain_page(source, tag, context, render)`` runs a render under it, and ``PageJob`` renders a deferred page boundary as a job, its loading UI, its content, and its errors, see :doc:`defer`.

.. automodule:: next.pages.jobs
   :members: PageErrorUI, PageJob, page_boundary, contain_page

Context graph
~~~~~~~~~~~~~

``next.pages.graph`` is the package that runs the context callables of a page along a dependency graph.
``compile_graph`` layers each step one below the last it reads.
``collect`` runs the steps of a ``RunOrder`` one after another and ``acollect`` overlaps the independent ones on the event loop, while ``run_phase`` and ``arun_phase`` run one phase of a render that a boundary splits.
``merged`` folds the results in run order, a keyless callable spreading only the names still free.
A context variable or an asgiref ``Local`` a callable writes reaches the later callables and the caller in declaration order.
The sync callables of an overlapped layer hand their writes back through the segment runner, so the caller sees them even when the runner runs the callables in a copy of the context.
The language, the URLconf and the script prefix keep what the last callable that changed them set, even when a later one switched one and restored it, and ``register_local_accessor`` registers a ``LocalAccessor``, an object with ``read()`` and ``write(value)``, that gives a value of the project the same treatment.
A layer whose callables wrote nothing reads none of these values.
``Blame`` names the inherited group a failed run is blamed on.

.. automodule:: next.pages.graph
   :members: ContextGraph, RunOrder, Step, Phase, Blame, compile_graph, collect, acollect, run_phase, arun_phase, merged, blame, blaming, callable_keys, closure, keyless_result, keyless_shape_error, register_local_accessor, LocalAccessor

Registry
~~~~~~~~

.. automodule:: next.pages.registry
   :members:
   :exclude-members: resolver

Loaders
~~~~~~~

``TemplateLoader`` is the abstract contract for sourcing template text from a ``page.py`` path.

.. autoclass:: next.pages.TemplateLoader
   :members:

``DjxTemplateLoader`` reads a sibling ``template.djx`` next to ``page.py``.
It is the only loader in the default ``TEMPLATE_LOADERS`` chain.

.. autoclass:: next.pages.loaders.DjxTemplateLoader
   :members:

``PythonTemplateLoader`` reads a ``template`` attribute defined inside ``page.py``.
It is not registered by default, and the manager already consults ``module.template`` directly, so registering it changes nothing at render time.
Registered, it counts the ``template`` attribute a second time, so ``next.W043`` warns on every ``page.py`` that sets a ``template`` string, and a project leaves it out of ``TEMPLATE_LOADERS``.

.. autoclass:: next.pages.loaders.PythonTemplateLoader
   :members:

``in_page_tree(directory)`` tells whether a directory lies inside a tree some router serves.
``AncestorStamps.begin(file_path)`` walks the ``page.py`` files over a page inside its page tree, root first, and the walk stops at the tree root, so a tree that a ``pages()`` include places under a prefix inherits nothing from its host.

.. autofunction:: next.pages.loaders.in_page_tree

``LayoutTemplateLoader`` composes nested ``layout.djx`` wrappers around the page template, walking every ancestor directory upward from the page, bounded at 64 levels, and then the ``layout.djx`` files of the ``DIRS`` roots.
The walk ends after the first layout that opens with the ``{% layout root %}`` marker, the ``DIRS`` roots included.
It is no ``TemplateLoader``, because the chain supplies a body and this wraps one, so it is never registered through ``TEMPLATE_LOADERS``.

.. autoclass:: next.pages.loaders.LayoutTemplateLoader
   :members:

``layout_chain(file_path, *, cut=True, resolved=False)`` returns the layouts that wrap a page file, nearest first, as resolved paths.
The chain ends after the first root layout and carries the ``DIRS`` roots after the ancestors.
``cut=False`` keeps the whole ancestry.
The walk climbs the path as written, so a page in a symlinked folder keeps the layouts of the tree that routes it, and each layout comes back resolved.
``resolved=True`` says no symlink sits on the path, which skips the check for one.
A ``layout.djx`` that is a directory and not a file is no layout.
Composition, the static discovery, and the checks all read this one function, so they agree on which layouts a page has.
The function is not exported from the top-level ``next`` package.

.. autofunction:: next.pages.loaders.layout_chain

``LayoutTemplateLoader`` keeps no cache of its own, and composition results live on ``Page``, where ``composed_template_for`` stores the composed source alongside the compiled ``Template``.
The layout texts and the root answers that ``layout_chain`` reads are held by mtime, and ``forget_layout_texts`` drops them.
``Page.clear_template_caches`` drops every layer together and calls it, and :doc:`/content/internals/page-discovery` describes how ``DEBUG`` decides when the composition is revalidated.

Placeholder
~~~~~~~~~~~

``next.pages.placeholder`` owns the layout placeholder grammar that :doc:`/content/topics/layouts` teaches.
``PLACEHOLDER`` is the canonical ``{% template %}`` spelling, and ``PLACEHOLDER_OPEN`` and ``PLACEHOLDER_CLOSE`` are the paired ``{% #template %}`` and ``{% /template %}`` form that carries a fallback body.
Composition and the ``check_layout_templates`` check read the same scan from here, so ``next.W001`` and ``next.W078`` count exactly the placeholders composition would fill.
``ROOT_MARKER`` is the canonical ``{% layout root %}`` spelling.
``scan_root(content)`` returns a ``RootScan`` for a layout source, whose ``is_root`` tells whether the layout is a root and whose ``markers`` holds the bounds of every marker the lexer reaches.
A layout is a root when exactly one marker stands after nothing but ``{% load %}`` tags, ``{% comment %}`` blocks, ``{# #}`` comments, ASCII whitespace, and a leading byte order mark.
The scan serves composition and ``next.E168`` alike, so a marker that is misplaced or repeated is never half honored.
``opens_html_document(content)`` tells whether a source writes an ``<html`` tag in text outside a ``{% comment %}``, which is what ``next.W133`` reads.

.. automodule:: next.pages.placeholder
   :members:

Module reads
~~~~~~~~~~~~

``read_module_string_lists`` executes a page-tree module and returns the named module-level string lists it declares, or ``None`` when the file does not load.
The static discovery layer reads the ``styles`` and ``scripts`` lists of a ``page.py`` or a ``component.py`` through it.
Anything but a list or tuple of non-empty strings reads as an empty list, so the caller never type-checks what a user module bound to the name.

.. autofunction:: next.pages.loaders.read_module_string_lists

Scan
~~~~

``next.pages.scan`` walks the routed page tree for the system checks.
``iter_existing_scanned_pages`` yields each existing ``page.py`` once across routers.
``iter_existing_scanned_page_pairs`` yields the route of each ``page.py`` beside its path, for a check that reports a page by its URL.
``iter_serialized_page_context_keys`` yields the ``page.py`` path and key of every keyed ``serialize=True`` context function.

.. automodule:: next.pages.scan
   :members:

Import failures
~~~~~~~~~~~~~~~

A ``page.py`` whose body raises while importing is a broken module, as distinct from a file the loader cannot read at all.
The recorded failure re-raises on the request path as ``PageModuleImportError``, which carries the original exception as ``__cause__`` and the offending path as ``file_path``.
The request answers 500 on every setting, and ``settings.DEBUG`` decides between the technical and the generic error page.
See *Broken page modules* in :doc:`/content/topics/pages` for the blast radius.

.. autoclass:: next.pages.PageModuleImportError
   :members:

Context shape
~~~~~~~~~~~~~

A keyless ``@context`` contributes its whole return value to the page context, which the merge reads as a mapping.
A callable that answers something else raises ``PageContextShapeError``, a ``TypeError`` subclass carrying the ``context_name`` of the callable and the ``file_path`` of the page it was building.
Without it the merge would fail inside ``dict.update`` and name neither.
The ``next.E029`` check reports the same mistake statically, from the return annotation, so a callable annotated ``-> dict`` and answering otherwise is what reaches the runtime error.

.. autoclass:: next.pages.PageContextShapeError
   :members:

Metadata
~~~~~~~~

``next.pages.metadata`` merges ``DEFAULTS`` and every ``metadata`` dict or ``@page.metadata`` callable of a page and its ancestors into one ``Metadata`` value, resolves it per response, and renders it through the class ``NEXT_FRAMEWORK["METADATA"]["RENDERER"]`` names.
:doc:`metadata` is the reference for the dicts, the value objects, ``Replace`` and ``RESET``, the renderer contract, the ``ld`` nodes, and the four ``PageMetadata*`` errors, and :doc:`/content/topics/seo/metadata` the guide.

Response policy
~~~~~~~~~~~~~~~

``next.pages.responses`` reads the ``cache`` and ``headers`` a ``page.py`` declares and stamps them, with the ``X-Robots-Tag`` the metadata calls for, on the page response.
``CacheDict`` is the dict form of ``cache``, ``HeadersDict`` the mapping ``headers`` takes, and ``CacheControl`` the normalised directives a ``SitemapBackend.cache_control()`` returns, all three exported from ``next.pages``.
A shared ``cache`` applies to a ``GET`` or a ``HEAD`` alone and is sent with ``private`` when the response depends on the visitor, through a cookie, a session read, a CSRF cookie, a consent read, a CSP nonce, or an ``Authorization`` header on the request.
``headers`` may not name a caching header, ``Cache-Control``, ``CDN-Cache-Control``, ``Surrogate-Control``, ``Cloudflare-CDN-Cache-Control``, ``Expires``, ``Age``, or ``Vary``, which ``cache`` owns.
``SharedCookies`` is the cookie jar a shared response carries in place of Django's, so a cookie a middleware sets after the view, the session and CSRF cookies among them, still makes the response private, and a post-render callback settles a lazily rendered response the same way.
A zone response carries the ``headers`` of its page and ``private, no-store``.
A callable ``cache`` runs once ``render()`` has resolved the body, skipped when the response ``render()`` returns is no success or names its own ``Cache-Control``, and one that raises or answers a wrong shape sends the page with ``private, no-store``.
See :doc:`/content/topics/caching` for the rules.

.. autoclass:: next.pages.CacheDict
   :members:

.. autodata:: next.pages.HeadersDict
   :no-value:

.. autoclass:: next.pages.CacheControl
   :members:

.. autoclass:: next.pages.responses.SharedCookies
   :members: take_private

``next.middleware.SharedCacheGuardMiddleware`` is the opt-in last line behind ``SharedCookies``, for a cookie that reaches a shared response past the page, see *The shared cache guard* in :doc:`/content/topics/caching`.

.. automodule:: next.middleware
   :members:

Ports
~~~~~

``next.pages.ports`` holds ``PageScanImpl``, which binds the page-tree scan to the ``PageScan`` port of :doc:`ports`.
The scan reads the route manager from ``next.discovery``, so discovery reaches the scan back through the port rather than through an import that would close the cycle.

Processors
~~~~~~~~~~

Context-processor discovery has no public callable of its own.
The merged list comes from ``OPTIONS.context_processors`` on each ``PAGE_BACKENDS`` entry followed by ``OPTIONS.context_processors`` on the first ``DjangoTemplates`` entry of ``TEMPLATES``, the engine every page compiles on, deduplicated by dotted path with the first occurrence kept, and it is memoised until either source setting changes.
See *Resolution order* in :doc:`/content/topics/context` for where the merged list sits among the other context sources.
A page renders on a context that carries its request, as a ``RequestContext`` does with its processors already run, so ``{% url %}`` reverses into ``request.current_app``.

Visits
~~~~~~

``next.pages.visits`` holds ``visit_request``, which copies a live request and restates it as a GET of one page URL.
``authorization_outcome`` asks a page through that copy, so a ``render()`` reading the method, the path, or the query string answers an out-of-band caller as it answers a visit.
The live request is never modified, and the user, the session, and every other attribute a middleware attached come through untouched.
The copy leaves out the dependency cache of a form dispatch, and ``authorization_outcome`` resolves ``render()`` with a fresh cache, so a guard never reads a value the dispatch resolved.
See *Render paths and what each one runs* in :doc:`/content/internals/request-lifecycle` for the callers.

System checks
~~~~~~~~~~~~~

``next.pages.checks`` registers the Django system checks for the pages subsystem.
They run through ``uv run python manage.py check``.
The package splits by subject into ``access``, ``branches``, ``composed``, ``contexts``, ``groups``, ``intercepts``, ``layouts``, ``loaders``, ``metadata``, ``modules``, ``processors``, ``responses``, ``structure``, ``walk``, and ``zones``, and importing the package registers every one of them.
``metadata`` is a package of its own, ``scope``, ``titles``, ``crumbs``, ``shape``, ``head``, ``ld``, ``links``, ``pages``, and ``templates``, and ``responses`` owns the ``cache``, ``headers``, ``CSRF_DELIVERY``, and ``CSRF_USE_SESSIONS`` checks, see :doc:`system-checks` for the codes each one owns.
``access`` owns the checks of the access declarations, ``next.E163`` and ``next.E164``, while ``next.E162`` for an ``obj=DObject[Model]`` lives with the ``DObject`` checks in ``next.deps.checks``.
Its ``passes_down(file_path)`` tells whether a ``page.py`` passes access with ``inherit=True`` or context with ``inherit_context=True`` to the pages below, and ``declares_for_itself(file_path)`` whether it declares access only a page of its own would take.
``modules`` builds on them with ``load_page(file_path)``, one load of a ``page.py`` and whether it routes, which every body check reads instead of loading the file again.
Its ``faulty(file_path, loaded)`` tells whether ``next.E012``, ``next.E013``, or ``next.E166`` names a ``page.py``, so ``next.W128`` leaves to that error the branch it names and still reports every other empty branch around it.
``next.E012``, ``next.E166``, and ``next.W128`` read the directories that declare actions through the one ``ActionOwners`` port of :doc:`ports`.
``branches`` finds the directories with no page and no action for ``next.W128``, which ``next.forms.checks.actions`` registers, since only the forms area knows the actions.

See :doc:`system-checks` for each check identifier, its condition, and the full autodoc of ``next.pages.checks``.

Signals
-------

See :doc:`signals` and :doc:`/content/topics/signals` for the pages signals (``template_loaded``, ``context_registered``, ``metadata_registered``, ``page_rendered``).

See also
--------

.. seealso::

   :doc:`/content/topics/pages` for the topic guide.
   :doc:`/content/topics/seo/metadata` for the metadata guide.
   :doc:`/content/internals/page-discovery` for the internal pipeline.
