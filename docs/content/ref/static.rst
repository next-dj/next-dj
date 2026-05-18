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

Scripts
~~~~~~~

``NextScriptBuilder.from_options`` reads the ``NEXT_JS_OPTIONS`` dict and accepts the keys ``preload_template``, ``script_tag_template``, ``init_template``, and ``policy``.

The ``ScriptInjectionPolicy`` members carry the string values ``"auto"``, ``"disabled"``, and ``"manual"``.
The policy is configured through ``NEXT_JS_OPTIONS["policy"]``, given either as a ``ScriptInjectionPolicy`` member or as one of those string values.
Any other value raises ``ValueError`` when the builder is constructed.

.. automodule:: next.static.scripts
   :members:

JS Context Serializer
~~~~~~~~~~~~~~~~~~~~~

``StaticCollector.add_js_context`` and ``NextScriptBuilder.init_script`` delegate value encoding to the configured ``JsContextSerializer``.
``resolve_serializer`` reads ``NEXT_FRAMEWORK["JS_CONTEXT_SERIALIZER"]`` on every call and verifies the resolved instance satisfies the protocol.

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
It surfaces every such asset to ``collectstatic`` and to ``{% static "next/..." %}`` lookups during development.
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
