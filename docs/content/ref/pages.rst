.. _ref-pages:

Pages reference
===============

Module summary
--------------

``next.pages`` exposes the ``Page`` coordinator and its ``page`` singleton, the ``@context`` decorator, the ``Context`` and ``ContextResult`` value objects, the ``PageModuleImportError`` raised by a broken ``page.py`` and the ``PageContextShapeError`` raised by a keyless ``@context`` answering no mapping, and the ``checks`` and ``signals`` submodules.
It also exposes the page metadata surface, the ``Metadata`` and ``ResolvedMetadata`` values, the ``MetadataDict`` and ``SiteMetadataDict`` input shapes, the ``Replace`` and ``RESET`` markers, the ``ld`` nodes, the ``MetadataRenderer`` contract with its ``HtmlMetadataRenderer``, and the four ``PageMetadata*`` errors, see :doc:`metadata`.
The ``CacheDict`` and ``HeadersDict`` shapes of the ``cache`` and ``headers`` a ``page.py`` declares complete the surface, see `Response policy`_ below.

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

Cross-area contract
~~~~~~~~~~~~~~~~~~~

Fourteen ``Page`` methods carry no leading underscore because other framework areas call them, not because application code should.
``composed_template_for``, ``build_render_context``, ``render_with_static_assets``, ``authorization_outcome``, ``has_template``, ``zone_bindings``, ``create_url_pattern``, ``render``, ``clear_template_caches``, ``static_metadata``, ``fold_metadata``, ``metadata_chain``, ``metadata_declaration``, and ``metadata_registrations`` serve ``next.forms``, ``next.partial``, ``next.urls``, ``next.testing``, ``next.seo``, the metadata checks, and the ``showmetadata`` command.
``next.forms`` and ``next.partial`` read the first six, ``next.urls`` builds every page pattern through ``create_url_pattern``, and ``next.testing`` calls ``render`` and ``clear_template_caches`` from its rendering and isolation helpers.
``next.partial`` folds the ``meta`` patch verb through ``fold_metadata``, ``next.seo`` and the response policy read ``static_metadata``, and the checks and ``showmetadata`` read the chain through the last three.
They follow the underscore rule of :doc:`/content/faq/general`, so they are safe from removal without notice.
They do not carry the application-facing stability of a Stable tier, and their signatures may drift as partial rendering evolves.
``register_template`` is called by ``composed_template_for`` on every cache miss and by no other area, so it serves application code seeding a composed body under the same underscore rule, again without the Stable tier's signature guarantee.

Manager
~~~~~~~

``next.pages.manager`` is a package.
Its ``__init__`` holds ``Page`` and the ``page`` singleton, ``templates`` holds the per-page template cache, and ``views`` holds the routed views and the ``URLPattern`` factory the file router mounts them under.

.. automodule:: next.pages.manager
   :members:
   :exclude-members: page, context, resolver

.. automodule:: next.pages.manager.templates
   :members:

Registry
~~~~~~~~

.. automodule:: next.pages.registry
   :members:
   :exclude-members: resolver

Loaders
~~~~~~~

``TemplateLoader`` is the abstract contract for sourcing template text from a ``page.py`` path.

.. autoclass:: next.pages.loaders.TemplateLoader
   :members:

``DjxTemplateLoader`` reads a sibling ``template.djx`` next to ``page.py``.
It is the only loader in the default ``TEMPLATE_LOADERS`` chain.

.. autoclass:: next.pages.loaders.DjxTemplateLoader
   :members:

``PythonTemplateLoader`` reads a ``template`` attribute defined inside ``page.py``.
It is not registered by default.
Add its dotted path to ``NEXT_FRAMEWORK["TEMPLATE_LOADERS"]`` to enable it.
The manager already consults ``module.template`` directly, so registering this loader changes nothing at render time and only affects how the ``next.W043`` conflict check reports the source.

.. autoclass:: next.pages.loaders.PythonTemplateLoader
   :members:

``LayoutTemplateLoader`` composes nested ``layout.djx`` wrappers around the page template, walking every ancestor directory upward from the page, bounded at 64 levels.
It is no ``TemplateLoader``, because the chain supplies a body and this wraps one, so it is never registered through ``TEMPLATE_LOADERS``.

.. autoclass:: next.pages.loaders.LayoutTemplateLoader
   :members:

``LayoutTemplateLoader`` keeps no cache of its own, and composition results live on ``Page``, where ``composed_template_for`` stores the composed source alongside the compiled ``Template``.
``Page.clear_template_caches`` drops every layer together, and :doc:`/content/internals/page-discovery` describes how ``DEBUG`` decides when the composition is revalidated.

Placeholder
~~~~~~~~~~~

``next.pages.placeholder`` owns the layout placeholder grammar that :doc:`/content/topics/layouts` teaches.
``PLACEHOLDER`` is the canonical ``{% template %}`` spelling, and ``PLACEHOLDER_OPEN`` and ``PLACEHOLDER_CLOSE`` are the paired ``{% #template %}`` and ``{% /template %}`` form that carries a fallback body.
Composition and the ``check_layout_templates`` check read the same scan from here, so ``next.W001`` and ``next.W078`` count exactly the placeholders composition would fill.

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
``settings.DEBUG`` and ``NEXT_FRAMEWORK["STRICT_LOADING"]`` decide how loudly that failure reaches the client.
See *Broken page modules* in :doc:`/content/topics/pages` for the loudness table and the blast radius.

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
``CacheDict`` is the dict form of ``cache`` and ``HeadersDict`` the mapping ``headers`` takes, both exported from ``next.pages``.
A shared ``cache`` applies to a ``GET`` or a ``HEAD`` alone and is sent with ``private`` when the response depends on the visitor, through a cookie, a session read, a CSRF cookie, a consent read, a CSP nonce, or an ``Authorization`` header on the request.
``headers`` may not name a caching header, ``Cache-Control``, ``CDN-Cache-Control``, ``Surrogate-Control``, ``Cloudflare-CDN-Cache-Control``, ``Expires``, ``Age``, or ``Vary``, which ``cache`` owns.
``SharedCookies`` is the cookie jar a shared response carries in place of Django's, so a cookie a middleware sets after the view, the session and CSRF cookies among them, still makes the response private, and a post-render callback settles a lazily rendered response the same way.
A zone response carries the ``headers`` of its page and ``private, no-store``.
A callable ``cache`` that raises or answers a wrong shape sends the page with ``private, no-store`` and logs once, raising under ``DEBUG`` or ``STRICT_LOADING``.
See :doc:`/content/topics/caching` for the rules.

.. autoclass:: next.pages.CacheDict
   :members:

.. autodata:: next.pages.HeadersDict
   :no-value:

.. autoclass:: next.pages.responses.CacheControl
   :members:

.. autoclass:: next.pages.responses.SharedCookies
   :members: take_private

``next.middleware.SharedCacheGuardMiddleware`` is the opt-in last line behind ``SharedCookies``, for a cookie that reaches a shared response past the page, see *The shared cache guard* in :doc:`/content/topics/caching`.

.. automodule:: next.middleware
   :members:

Ports
~~~~~

``next.pages.ports`` holds ``PageScanImpl``, which binds the page-tree scan to the ``PageScan`` port of :doc:`ports`.
The scan reads the router manager from ``next.discovery``, so discovery reaches the scan back through the port rather than through an import that would close the cycle.

Processors
~~~~~~~~~~

Context-processor discovery has no public callable of its own.
The merged list comes from ``OPTIONS.context_processors`` on each ``PAGE_BACKENDS`` entry followed by ``OPTIONS.context_processors`` on the first ``TEMPLATES`` entry, deduplicated by dotted path with the first occurrence kept, and it is memoised until either source setting changes.
See *Resolution order* in :doc:`/content/topics/context` for where the merged list sits among the other context sources.

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
They run through ``uv run python manage.py check``, except ``check_page_module_imports``, which is a deployment check and runs under ``manage.py check --deploy``.
The package splits by subject into ``contexts``, ``layouts``, ``loaders``, ``metadata``, ``modules``, ``processors``, ``responses``, ``structure``, and ``zones``, and importing the package registers every one of them.
``metadata`` is a package of its own, ``scope``, ``titles``, ``shape``, ``head``, ``ld``, ``links``, ``pages``, and ``templates``, and ``responses`` owns the ``cache``, ``headers``, ``CSRF_DELIVERY``, and ``CSRF_USE_SESSIONS`` checks, see :doc:`system-checks` for the codes each one owns.

The package exports thirty-one check callables.

- ``check_context_functions``.
- ``check_context_processor_signature``.
- ``check_context_reads_foreign_zone``.
- ``check_context_registration_files``.
- ``check_csrf_delivery``.
- ``check_csrf_in_session``.
- ``check_layout_templates``.
- ``check_metadata_callable_returns_mapping``.
- ``check_metadata_enum_values``.
- ``check_metadata_head_literals``.
- ``check_metadata_head_tags``.
- ``check_metadata_hreflang_patterns``.
- ``check_metadata_jsonld``.
- ``check_metadata_noindex_canonical``.
- ``check_metadata_parent_parameter``.
- ``check_metadata_registration_files``.
- ``check_metadata_settings_scope``.
- ``check_metadata_social_folds``.
- ``check_metadata_tag_rendered``.
- ``check_metadata_title_templates``.
- ``check_metadata_url_schemes``.
- ``check_page_functions``.
- ``check_page_metadata_shape``.
- ``check_page_module_imports``.
- ``check_page_response_declarations``.
- ``check_pages_structure``.
- ``check_request_in_context``.
- ``check_shared_page_responses``.
- ``check_single_keyless_context``.
- ``check_template_loaders``.
- ``check_unrouted_working_directory_pages``.

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
