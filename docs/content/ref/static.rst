.. _ref-static:

Static reference
================

Module summary
--------------

``next.static`` exposes the asset discovery, the request-scoped collector, and the configured static backends.
It also exposes the kind and placeholder registries, the ``next.min.js`` script builder, the staticfiles finder, and the JS context serializer.

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

.. automodule:: next.static.backends
   :members:

Assets
~~~~~~

.. automodule:: next.static.assets
   :members:

Manager
~~~~~~~

.. automodule:: next.static.manager
   :members:

``default_manager`` is the process-wide static manager handle exported from ``next.static``.
It builds its wrapped ``StaticManager`` lazily on first access.
``reset_default_manager`` drops that wrapped instance so the next access rebuilds it, which keeps the manager consistent when ``NEXT_FRAMEWORK`` changes under ``override_settings``.

Injection
~~~~~~~~~

.. automodule:: next.static.inject
   :members:

``StaticManager.inject`` delegates to a ``PlaceholderInjector`` bound to the manager, which reads the active backend, the URL rewrite, and the script builder through it.

Scripts
~~~~~~~

See :doc:`/content/topics/static-assets/js-context` for the runtime script options and the ``NEXT_JS_OPTIONS`` keys.

.. automodule:: next.static.scripts
   :members:
   :exclude-members: csrf_header_name, csrf_payload, csrf_payload_for

The init payload reserves the ``$csrf`` and ``$dev`` keys for the framework, so an automatically injected payload drops a project key of either name before it reaches ``window.Next.context``.
See :doc:`/content/topics/static-assets/js-context` for the ownership rule and the ``next.W075`` check that reports a collision.

JS context serializer
~~~~~~~~~~~~~~~~~~~~~

.. automodule:: next.static.serializers
   :members:

Defaults
~~~~~~~~

.. automodule:: next.static.defaults
   :members:

Staticfiles finder
~~~~~~~~~~~~~~~~~~

.. automodule:: next.static.finders
   :members:

``NextStaticFilesFinder`` is the Django staticfiles finder for co-located assets.
It maps assets such as ``template.css``, ``layout.js``, ``component.css``, and any registered stems to their source files under the ``next/`` staticfiles namespace.
It surfaces every such asset to ``collectstatic`` for production output, to ``manage.py findstatic``, and to the staticfiles view that serves files directly while ``DEBUG`` is true.
Asset URLs themselves come from ``staticfiles_storage.url``, not from the finder.
Staticfiles asks the finder once per referenced asset, so the mapping is held rather than walked again for every lookup.
It is rebuilt when a stem or kind registration changes which filenames count, when the page or component trees the routers report change, and, while ``DEBUG`` is true, when the mtime of any directory inside those trees moves.
That last check is what picks up an asset added at runtime, and it is skipped when ``DEBUG`` is false, where only a reconfiguration moves what the walk finds.

The finder is appended to ``STATICFILES_FINDERS`` automatically by ``NextFrameworkConfig.ready`` through ``next.apps.staticfiles.install``.
The install step is idempotent and skips the entry when it is already present.
You do not need to list it in ``STATICFILES_FINDERS`` yourself.
The dotted path is ``next.static.NextStaticFilesFinder``.
Confirm it is active by running ``manage.py findstatic next/components/note_card.css``.

Replacing the finder means adding one rather than swapping one out.
``next.apps.staticfiles.install`` appends the framework entry whenever it is absent, and its ``setting_changed`` receiver appends it again after an override rewrites the list, so the shipped finder cannot be configured away.
A project that needs a different mapping subclasses ``NextStaticFilesFinder``, overrides ``find`` or ``list``, and lists the subclass in ``STATICFILES_FINDERS`` ahead of the framework entry.
Staticfiles consults the finders in list order and the first match answers a lookup, so the subclass decides every path it claims while ``collectstatic`` still collects from both.

Signals
-------

See :doc:`signals` and :doc:`/content/topics/static-assets/signals` for the static signals (``asset_registered``, ``collector_finalized``, ``html_injected``, ``backend_loaded``).

See also
--------

.. seealso::

   :doc:`/content/topics/static-assets/index` for the topic subtree.
   :doc:`/content/internals/static-pipeline` for the internal flow.
   :doc:`/content/deployment/static-files` for production ``collectstatic`` configuration and the finder setup.
