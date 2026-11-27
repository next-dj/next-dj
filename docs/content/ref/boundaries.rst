.. _ref-boundaries:

Boundaries reference
====================

Module summary
--------------

``next.boundaries`` is the boundary unit every area that draws a boundary shares, the deferred engine, the components, and the pages alike.
It holds the options a boundary declares, the error a failed boundary settles as, the deferred call a component tag hands over, the wire ids of a response, and the inline error boundary with its ``boundary_failed`` signal.
The package exports ``contain``, ``boundary_failed``, ``ErrorKind``, and ``BoundaryOptionError``, and the rest imports by the full path of its module.
:doc:`/content/topics/boundaries/index` is the guide, and the ``BOUNDARIES`` scope is in :doc:`settings`.

``next.boundaries.options``
   ``DeferMode``, ``WrapperTag``, ``BoundaryOptions``, and the options a component tag reserves.
``next.boundaries.failures``
   ``ErrorKind``, ``BoundaryFailure``, and what an error boundary contains and shows.
``next.boundaries.calls``
   ``DeferredCall``, the ``BoundarySite`` it sits at, and ``stable_key``, the key of a call site.
``next.boundaries.ids``
   The wire ids of one response and the render session that mints them.
``next.boundaries.errors``
   ``BoundaryOptionError``.

Options
-------

.. autoclass:: next.boundaries.options.DeferMode
   :no-special-members:

.. autoclass:: next.boundaries.options.WrapperTag
   :no-special-members:

.. autoclass:: next.boundaries.options.BoundaryOptions
   :members: parse

.. autoexception:: next.boundaries.BoundaryOptionError

``BoundaryOptions.parse`` raises ``BoundaryOptionError`` for an option outside its shape, and a misfit ``timeout`` carries the :exc:`~next.errors.DurationError` of ``parse_duration`` as its cause.
``RESERVED_OPTIONS`` holds ``defer``, ``timeout``, ``reload``, and ``delay``, the keywords a component never takes as a prop.
A component tag reads ``defer`` and ``timeout`` as boundary options, and ``reload`` and ``delay`` belong to ``{% zone %}`` alone, so a component tag that writes either is a template syntax error.
A duration option takes a number of seconds or a ``"2s"`` or ``"500ms"`` literal up to ``MAX_DURATION`` of ``next.utils``, one day in seconds.

Failures
--------

.. autoclass:: next.boundaries.ErrorKind
   :no-special-members:

.. autoclass:: next.boundaries.failures.BoundaryFailure
   :members: of

.. autoclass:: next.boundaries.failures.DebugBoundaryFailure

Error boundaries
----------------

``contain(boundary, render, error_ui)`` returns what ``render`` returns, or what ``error_ui`` renders when ``render`` raises an exception a boundary contains, and every page and component boundary goes through it.
It is the sender of ``boundary_failed``, see :doc:`signals`.
``NEXT_FRAMEWORK["BOUNDARIES"]`` decides whether a boundary contains at all, how it reports a failure, which kinds it shows, and how it guards the writes of its subtree, see :doc:`settings` and :doc:`transactions`.

.. autofunction:: next.boundaries.contain

See also
--------

.. seealso::

   :doc:`/content/topics/boundaries/index` for the guide.
   :doc:`defer` for the deferred engine that streams and reloads boundaries.
   :doc:`transactions` for the savepoint an error boundary guards its subtree with.
