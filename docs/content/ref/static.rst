.. _ref-static:

Static Reference
================

Module Summary
--------------

``next.static`` exposes the asset discovery, the request scoped collector, the backend chain, the kind registry, and the JS context serializer.

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

``NextScriptBuilder`` produces the three HTML fragments that wire ``window.Next`` into a page.
The preload hint is injected before ``</head>``, the blocking ``<script>`` tag loads the compiled ``next.min.js`` runtime, and the inline init script passes the serialized JS context to ``Next._init``.
``NextScriptBuilder.from_options`` reads the ``NEXT_JS_OPTIONS`` dict and accepts the keys ``preload_template``, ``script_tag_template``, ``init_template``, and ``policy``.

``ScriptInjectionPolicy`` is the enum that decides whether the static manager emits those fragments.

``ScriptInjectionPolicy.AUTO`` (string value ``"auto"``) is the default.
   The static manager injects the preload hint, the ``<script>`` tag, and the ``Next._init`` call into every rendered page.

``ScriptInjectionPolicy.DISABLED`` (string value ``"disabled"``).
   Injection is skipped entirely. Use it for pages that do not need ``window.Next``, such as a raw API response rendered through the page machinery.

``ScriptInjectionPolicy.MANUAL`` (string value ``"manual"``).
   Automatic injection is skipped, but the builder still produces the fragments on request so a template can emit the tags itself.

The policy is configured through ``NEXT_JS_OPTIONS["policy"]``, given either as a ``ScriptInjectionPolicy`` member or as one of the string values above.
Any other value raises ``ValueError`` when the builder is constructed.

.. automodule:: next.static.scripts
   :members:

JS Context Serializer
~~~~~~~~~~~~~~~~~~~~~

``JsContextSerializer`` is a runtime-checkable protocol with a single method, ``dumps(value) -> str``, which returns JSON text for one value bound for ``window.Next.context``.
``StaticCollector.add_js_context`` and ``NextScriptBuilder.init_script`` delegate encoding to it.

The framework ships two implementations.

``JsonJsContextSerializer``.
   The process-wide default. It encodes with Django's ``DjangoJSONEncoder`` and compact separators so the inline init payload stays small.

``PydanticJsContextSerializer``.
   Unwraps pydantic ``BaseModel`` instances through ``model_dump`` before encoding, and falls back to ``DjangoJSONEncoder`` for other types. It requires the ``pydantic`` package and raises ``ImportError`` at construction when pydantic is absent.

``resolve_serializer`` reads ``NEXT_FRAMEWORK["JS_CONTEXT_SERIALIZER"]`` on every call.
When the setting is ``None`` it returns the shared ``JsonJsContextSerializer``.
Otherwise it imports the dotted path, instantiates the class with no arguments, and verifies the instance satisfies the protocol, raising ``TypeError`` when it does not.
A custom serializer is selected by pointing ``JS_CONTEXT_SERIALIZER`` at any class that implements ``dumps``.

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

``NextStaticFilesFinder`` is the Django staticfiles finder that maps co-located assets (``template.css``, ``layout.js``, ``component.css``, and any registered stems) to their source files under the ``next/`` staticfiles namespace.
It surfaces every such asset to ``collectstatic`` and to ``{% static "next/..." %}`` lookups during development.
The mapping is rebuilt on each lookup so assets added at runtime are picked up.

The finder is appended to ``STATICFILES_FINDERS`` automatically by ``NextFrameworkConfig.ready`` through ``next.apps.staticfiles.install``, which is idempotent and skips the entry when it is already present.
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
