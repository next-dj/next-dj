.. _ref-pages:

Pages reference
===============

Module summary
--------------

``next.pages`` exposes the ``Page`` coordinator and its ``page`` singleton, the ``@context`` decorator, the ``Context`` and ``ContextResult`` value objects, the ``PageModuleImportError`` raised by a broken ``page.py``, and the ``checks`` and ``signals`` submodules.

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

Cross-area contract
~~~~~~~~~~~~~~~~~~~

Four ``Page`` methods carry no leading underscore because other framework areas call them, not because application code should.
``composed_template_for``, ``build_render_context``, ``render_with_static_assets``, and ``authorization_outcome`` serve ``next.forms`` and ``next.partial``.
They follow the underscore rule of :doc:`/content/faq/general`, so they are safe from removal without notice.
They do not carry the application-facing stability of a Stable tier, and their signatures may drift as partial rendering evolves.

Manager
~~~~~~~

.. automodule:: next.pages.manager
   :members:
   :exclude-members: page, context, resolver

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
It runs on a dedicated path and is not registered through ``TEMPLATE_LOADERS``.

.. autoclass:: next.pages.loaders.LayoutTemplateLoader
   :members:

``LayoutTemplateLoader`` keeps no cache of its own.
Composition results live on ``Page``, where ``composed_template_for`` stores the composed source alongside the compiled ``Template``, so a warm render parses nothing and opens no template file.
It still stats every source file behind the page to detect a change.
Both layers are dropped together once a ``template.djx`` or ``layout.djx`` changes on disk.
A page whose body comes from a module-level ``render()`` in ``page.py`` bypasses that cache and recomposes the layout chain on every request.
``Page.clear_template_caches`` drops both layers and the mtime snapshots together, for a caller rewriting a page or a layout in place inside one process.

Module reads
~~~~~~~~~~~~

``read_module_string_lists`` executes a page-tree module and returns the named module-level string lists it declares, or ``None`` when the file does not load.
The static discovery layer reads the ``styles`` and ``scripts`` lists of a ``page.py`` or a ``component.py`` through it.
Anything but a list or tuple of non-empty strings reads as an empty list, so the caller never type-checks what a user module bound to the name.

.. autofunction:: next.pages.loaders.read_module_string_lists

Scan
~~~~

``next.pages.scan`` walks the routed page tree for the system checks.
``iter_existing_scanned_pages`` yields each existing ``page.py`` once across routers, and ``iter_serialized_page_context_keys`` yields the ``page.py`` path and key of every keyed ``serialize=True`` context callable.

.. automodule:: next.pages.scan
   :members:

Import failures
~~~~~~~~~~~~~~~

A ``page.py`` the loader cannot read, an ``OSError`` or a module spec that does not build, counts as a legitimately absent module and the page contributes no body without a log record.
A ``page.py`` whose body raises any exception during execution is a broken module, and ``logger.exception`` records the traceback on every load attempt.
``ImportError``, ``AttributeError``, and ``SyntaxError`` are common examples, not a closed list.
On the request path the recorded failure re-raises as ``PageModuleImportError`` when ``settings.DEBUG`` or ``NEXT_FRAMEWORK["STRICT_LOADING"]`` is set.
Under ``DEBUG`` the standard technical 500 page points at the failing line.
Under ``STRICT_LOADING`` without ``DEBUG`` the client receives a generic 500 while the traceback stays in the server log.
With both flags off the request answers 404, and the failure is visible only in the log record.
An out-of-band zone morph of a broken foreign page raises ``PageModuleImportError`` in every mode, because the request that a 404 would answer belongs to another URL.
The failure is scoped to the broken page.
Sibling pages keep their URL patterns and keep serving in every mode, because the error surfaces at the view rather than while the urlconf is built.
The recorded error is keyed by file mtime, so saving a fixed ``page.py`` clears it without a restart.
``manage.py check`` reports the same failure as ``next.E017``, naming the exception type and message.

.. autoclass:: next.pages.loaders.PageModuleImportError
   :members:

The message reads ``<path> failed to import``, and the original exception travels as ``__cause__``.

Processors
~~~~~~~~~~

.. automodule:: next.pages.processors
   :members:

System checks
~~~~~~~~~~~~~

``next.pages.checks`` registers the Django system checks for the pages subsystem.
They run through ``uv run python manage.py check``.

The module exports eleven check callables.

- ``check_context_functions``.
- ``check_context_processor_signature``.
- ``check_context_registration_files``.
- ``check_layout_templates``.
- ``check_page_functions``.
- ``check_page_module_imports``.
- ``check_pages_structure``.
- ``check_request_in_context``.
- ``check_single_keyless_context``.
- ``check_template_loaders``.
- ``check_unrouted_working_directory_pages``.

See :doc:`system-checks` for each check identifier, its condition, and the full autodoc of ``next.pages.checks``.

Signals
-------

See :doc:`signals` and :doc:`/content/topics/signals` for the pages signals (``template_loaded``, ``context_registered``, ``page_rendered``).

See also
--------

.. seealso::

   :doc:`/content/topics/pages` for the topic guide.
   :doc:`/content/internals/page-discovery` for the internal pipeline.
