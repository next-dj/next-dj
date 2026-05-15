.. _ref-urls:

URLs Reference
==============

Module Summary
--------------

``next.urls`` exposes the file router backend, the dispatcher, the URL reverse helpers, and the dependency injection markers for URL and query string parameters.

Public API
----------

Backends
~~~~~~~~

.. automodule:: next.urls.backends
   :members:

Manager
~~~~~~~

.. automodule:: next.urls.manager
   :members:

Parser
~~~~~~

.. automodule:: next.urls.parser
   :members:

Dispatcher
~~~~~~~~~~

.. automodule:: next.urls.dispatcher
   :members:

Reverse Helpers
~~~~~~~~~~~~~~~

.. autofunction:: next.urls.reverse.page_reverse

.. autofunction:: next.urls.reverse.with_query

Markers
~~~~~~~

.. automodule:: next.urls.markers
   :members:

Signals
-------

See :doc:`signals` and :doc:`/content/topics/signals` for the URL signals (``route_registered``, ``router_reloaded``).

See Also
--------

.. seealso::

   :doc:`/content/topics/file-router` for the topic guide.
   :doc:`/content/topics/url-reversing` for the reverse helpers.
   :doc:`/content/internals/url-router` for the dispatcher internals.
