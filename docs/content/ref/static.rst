.. _ref-static:

Static reference
================

Module summary
--------------

``next.static`` exposes the asset discovery, the request-scoped collector, and the configured static backends.
It also exposes the kind and placeholder registries, the ``next.min.js`` script builder, the two staticfiles finders, and the JS context serializer.
``next.static.runtime`` holds the script builder and the init payload keys, and ``next.static.nonce`` the CSP nonce every tag carries.
``next.static.scripts``, its earlier name, still resolves every name with a ``DeprecationWarning``, the two CSRF helpers from ``next.csrf``.
``static_name`` covers the reference shape rule, and ``StaticAssetNotFoundError`` and ``StaticAssetTraversalError`` name the two references the pipeline refuses, see :doc:`/content/topics/static-assets/name-resolution`.

Public API
----------

Collector
~~~~~~~~~

.. automodule:: next.static.collector
   :members:

Discovery
~~~~~~~~~

.. automodule:: next.static.discovery
   :members:

Backends
~~~~~~~~

Every renderer takes the URL and the ``request`` and ``nonce`` keywords, ``(self, url, *, request=None, nonce=None)``, and writes the nonce onto the tag it returns whenever one is set.
A registered kind whose renderer on the rendering backend lacks either keyword would fail every page that holds such an asset, so ``manage.py check`` reports it as an error.

.. automodule:: next.static.backends
   :members:

Assets
~~~~~~

.. automodule:: next.static.assets
   :members:

``static_name`` is what the default backend resolves through, and it is exported so a custom backend reads a reference the way core does.
It returns the normalised name a reference holds, ``None`` for a reference that is already a URL, and raises ``StaticAssetTraversalError`` for a name that climbs above the staticfiles root.

Errors
~~~~~~

.. automodule:: next.static.errors
   :members:

``StaticAssetNotFoundError`` subclasses ``RuntimeError``, and both ``register_file`` and ``resolve_url`` raise it, so a co-located file and an authored name fail on the same terms.
``StaticAssetTraversalError`` subclasses Django's ``SuspiciousFileOperation``, itself a :exc:`~django.core.exceptions.SuspiciousOperation`, so a reference leaving the static tree answers HTTP 400 instead of rendering a URL outside it.

Manager
~~~~~~~

.. automodule:: next.static.manager
   :members:

``default_manager`` is the process-wide static manager handle exported from ``next.static``.
It builds its wrapped ``StaticManager`` lazily on first access.
``reset_default_manager`` drops that wrapped instance so the next access rebuilds it, which keeps the manager consistent when ``NEXT_FRAMEWORK`` changes under ``override_settings``.
``get_static_manager`` returns the live ``StaticManager`` instance behind the lazy ``default_manager`` handle, and ``next.testing.patching`` uses it to patch a backend directly in tests.
``collect_component_assets`` is the entry point that folds a component's co-located assets into a caller-supplied collector, and ``next.templatetags.components`` and ``next.forms.widgets`` both call it.

Ports
~~~~~

.. automodule:: next.static.ports
   :members:

``StaticAssetsImpl`` binds the collector, the page and component discovery, and the injection entry points to the ``StaticAssets`` port of :doc:`ports`.
Every method reads the manager when it is called, so a render path that holds the port sees whatever the current settings built, and ``AppConfig.ready`` composes the binding once.

Injection
~~~~~~~~~

.. automodule:: next.static.inject
   :members:

``StaticManager.inject`` delegates to a ``PlaceholderInjector`` bound to the manager, which reads the active backend, the URL rewrite, and the script builder through it.

Runtime
~~~~~~~

See :doc:`/content/topics/static-assets/js-context` for the runtime script options and the ``NEXT_JS_OPTIONS`` keys.
Every builder method takes a ``nonce`` keyword, and the three templates take ``{nonce_attr}`` beside ``{url}`` or ``{payload}``.

.. automodule:: next.static.runtime
   :members:
   :exclude-members: csrf_payload_for

The init payload reserves ``$csrf``, ``$dev``, ``$chunks``, ``$scripts``, and ``$consent`` for the framework, so an automatically injected payload drops a project key of any of these names before it reaches ``window.Next.context``.
``$chunks`` names ``next.scripts.min.js``, the second bundle the finder serves beside ``next.min.js``, and ``$scripts`` and ``$consent`` feed it, see :doc:`client-extras`.
Under ``DEBUG`` it also names ``next.dev.min.js``, the diagnostics bundle the runtime loads only when ``$dev`` is true, so a production payload carries neither key.
The CSRF payload and the header name are built by ``next.csrf``, see :doc:`site`.
See :doc:`/content/topics/static-assets/js-context` for the ownership rule and the ``next.W075`` check that reports a collision.

Nonce
~~~~~

``resolve_nonce(request)`` answers the nonce of one render, read once per request while ``CSP_NONCE`` is ``True``, and marks the render personal once a nonce is minted, so no shared cache keeps it.
``request_nonce`` reads django-csp's ``request.csp_nonce`` or Django's own ``get_nonce``, and ``nonce_active()`` answers whether ``CSP_NONCE`` is on and one of the two middlewares is installed.
The injector hands the nonce to the script builder and to every backend renderer as the ``nonce`` keyword, see :doc:`/content/security/csp-and-nonce`.

.. automodule:: next.static.nonce
   :members:

JS context serializer
~~~~~~~~~~~~~~~~~~~~~

.. automodule:: next.static.serializers
   :members:

Defaults
~~~~~~~~

.. automodule:: next.static.defaults
   :members:

Staticfiles finders
~~~~~~~~~~~~~~~~~~~

.. automodule:: next.static.finders
   :members:

``NextStaticFilesFinder`` is the Django staticfiles finder for co-located assets.
It maps assets such as ``template.css``, ``layout.js``, ``component.css``, and any registered stems to their source files under the ``next/`` staticfiles namespace.
It surfaces every such asset to ``collectstatic`` for production output, to ``manage.py findstatic``, and to the staticfiles view that serves files directly while ``DEBUG`` is true.
Asset URLs themselves come from ``staticfiles_storage.url``, not from the finder.

Staticfiles asks the finder once per referenced asset, so the mapping is held rather than walked again for every lookup.
It is rebuilt when a stem or kind registration changes which filenames count, when the page or component trees the routers report change, and, while ``DEBUG`` is true, when the mtime of any directory inside those trees moves.
That last check is what picks up an asset added at runtime, and it is skipped when ``DEBUG`` is false, where only a reconfiguration moves what the walk finds.
The ``next.min.js``, ``next.scripts.min.js``, and ``next.dev.min.js`` bundles and their sourcemaps sit outside that held mapping and are stat'd on each lookup, so a checkout that builds the runtime while the server runs serves it without a restart.

``NextAppDirectoriesFinder`` replaces Django's ``AppDirectoriesFinder`` in the same list.
The framework ships its runtime bundle inside ``next/static``, which is also the ``next.static`` Python package, so the stock finder treats every framework module as an app static file and ``collectstatic`` copies them into ``STATIC_ROOT``.
The subclass drops the framework app from the scan, and ``NextStaticFilesFinder`` serves ``next/next.min.js``, ``next/next.scripts.min.js``, ``next/next.dev.min.js``, and their sourcemaps instead.
A project with its own app-directories finder subclasses ``NextAppDirectoriesFinder`` rather than Django's class, and ``manage.py check`` refuses one that does not as ``next.E083``.

The finder is appended to ``STATICFILES_FINDERS`` automatically by ``NextFrameworkConfig.ready`` through ``next.apps.staticfiles.install``.
The install step is idempotent and skips the entry when it is already present.
You do not need to list it in ``STATICFILES_FINDERS`` yourself.
The dotted path is ``next.static.NextStaticFilesFinder``.
Confirm it is active by running ``manage.py findstatic next/components/note_card.css``.

Replacing the finder means adding one rather than swapping one out.
``next.apps.staticfiles.install`` appends the framework entry whenever it is absent, and its ``setting_changed`` receiver appends it again after an override rewrites the list, so the shipped finder cannot be configured away.
A project that needs a different mapping subclasses ``NextStaticFilesFinder``, overrides ``find`` or ``list``, and lists the subclass in ``STATICFILES_FINDERS`` ahead of the framework entry.
An overriding ``find`` answers ``[]`` on a miss whatever ``find_all`` says, because ``django.contrib.staticfiles.finders.find`` wraps any other answer in a list and reads a returned ``None`` as one more match, which turns a missing file into a 500 from the staticfiles view rather than a 404.
Staticfiles consults the finders in list order and the first match answers a lookup, so the subclass decides every path it claims while ``collectstatic`` still collects from both.

Signals
-------

See :doc:`signals` and :doc:`/content/topics/static-assets/signals` for the static signals (``asset_registered``, ``collector_finalized``, ``html_injected``, ``static_backend_loaded``).

See also
--------

.. seealso::

   :doc:`/content/topics/static-assets/index` for the topic subtree.
   :doc:`/content/internals/static-pipeline` for the internal flow.
   :doc:`/content/deployment/static-files` for production ``collectstatic`` configuration and the finder setup.
