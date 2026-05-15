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

.. autoclass:: next.pages.loaders.TemplateLoader
   :members:

.. autoclass:: next.pages.loaders.DjxTemplateLoader
   :members:

Processors
~~~~~~~~~~

.. automodule:: next.pages.processors
   :members:

Signals
-------

See :doc:`signals` and :doc:`/content/topics/signals` for the pages signals (``template_loaded``, ``context_registered``, ``page_rendered``).

See Also
--------

.. seealso::

   :doc:`/content/topics/pages` for the topic guide.
   :doc:`/content/internals/page-discovery` for the internal pipeline.
