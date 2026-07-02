.. _ref-static:

Static Reference
================

Module Summary
--------------

``next.static`` exposes the asset discovery, the request-scoped collector, and the backend chain.
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

Scripts
~~~~~~~

See :doc:`/content/topics/static-assets/js-context` for the runtime script options and the ``NEXT_JS_OPTIONS`` keys.

.. automodule:: next.static.scripts
   :members:
   :exclude-members: csrf_header_name, csrf_payload, csrf_payload_for, CSRF_PAYLOAD_KEY

JS Context Serializer
~~~~~~~~~~~~~~~~~~~~~

.. automodule:: next.static.serializers
   :members:

Defaults
~~~~~~~~

.. automodule:: next.static.defaults
   :members:

Staticfiles Finder
~~~~~~~~~~~~~~~~~~

.. automodule:: next.static.finders
   :members:

``NextStaticFilesFinder`` is the Django staticfiles finder for co-located assets.
It maps assets such as ``template.css``, ``layout.js``, ``component.css``, and any registered stems to their source files under the ``next/`` staticfiles namespace.
It surfaces every such asset to ``collectstatic`` for production output and to ``{% static "next/..." %}`` lookups when ``DEBUG`` is true and the staticfiles app serves files itself.
The mapping is rebuilt on each lookup so assets added at runtime are picked up.

The finder is appended to ``STATICFILES_FINDERS`` automatically by ``NextFrameworkConfig.ready`` through ``next.apps.staticfiles.install``.
The install step is idempotent and skips the entry when it is already present.
You do not need to list it in ``STATICFILES_FINDERS`` yourself.
The dotted path is ``next.static.NextStaticFilesFinder``. You can confirm it is active by running ``manage.py findstatic next/some-component/component.css``.

Signals
-------

See :doc:`signals` and :doc:`/content/topics/static-assets/signals` for the static signals (``asset_registered``, ``collector_finalized``, ``html_injected``, ``backend_loaded``).

See Also
--------

.. seealso::

   :doc:`/content/topics/static-assets/index` for the topic subtree.
   :doc:`/content/internals/static-pipeline` for the internal flow.
   :doc:`/content/deployment/static-files` for production ``collectstatic`` configuration and the finder setup.
