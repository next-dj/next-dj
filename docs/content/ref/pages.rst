.. _ref-pages:

Pages Reference
===============

Module Summary
--------------

``next.pages`` exposes the page module API, the ``@context`` decorator, and the layout composition helpers.

Public API
----------

.. autoclass:: next.pages.Page
   :members:

.. autoclass:: next.pages.Context
   :members:

.. autoclass:: next.pages.ContextResult
   :members:

.. autofunction:: next.pages.context

.. autodata:: next.pages.page

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

``DjxTemplateLoader`` reads a sibling ``template.djx`` next to ``page.py``. It is the only loader in the default ``TEMPLATE_LOADERS`` chain.

.. autoclass:: next.pages.loaders.DjxTemplateLoader
   :members:

``PythonTemplateLoader`` reads a ``template`` attribute defined inside ``page.py``.
It is not registered by default.
Add its dotted path to ``NEXT_FRAMEWORK["TEMPLATE_LOADERS"]`` to enable it.
The manager already consults ``module.template`` directly, so registering this loader changes nothing at render time and only affects how the ``next.W043`` conflict check reports the source.

.. autoclass:: next.pages.loaders.PythonTemplateLoader
   :members:

``LayoutTemplateLoader`` composes nested ``layout.djx`` wrappers around the page template, walking the directory chain from the page up to the page root.
It runs on a dedicated path and is not registered through ``TEMPLATE_LOADERS``.

.. autoclass:: next.pages.loaders.LayoutTemplateLoader
   :members:

``LayoutManager`` caches the composed layout string per page path so repeated renders skip recomposition.

.. autoclass:: next.pages.loaders.LayoutManager
   :members:

Processors
~~~~~~~~~~

.. automodule:: next.pages.processors
   :members:

System Checks
~~~~~~~~~~~~~

``next.pages.checks`` registers the Django system checks for the pages subsystem.
They run through ``uv run python manage.py check``.

The module exports seven check callables: ``check_request_in_context``, ``check_layout_templates``, ``check_template_loaders``, ``check_pages_structure``, ``check_page_functions``, ``check_context_functions``, and ``check_context_processor_signature``.

See :doc:`system-checks` for each check identifier, its condition, and the full autodoc of ``next.pages.checks``.

Signals
-------

See :doc:`signals` and :doc:`/content/topics/signals` for the pages signals (``template_loaded``, ``context_registered``, ``page_rendered``).

See Also
--------

.. seealso::

   :doc:`/content/topics/pages` for the topic guide.
   :doc:`/content/internals/page-discovery` for the internal pipeline.
