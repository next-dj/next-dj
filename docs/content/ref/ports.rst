.. _ref-ports:

Ports reference
===============

Module summary
--------------

``next.ports`` holds the narrow protocols one subsystem calls another through.
Each port is a pair of a ``Protocol`` that states the method contract the caller depends on and a slot object that holds the one implementation composed at startup.
The caller imports the slot instead of the implementing subsystem, so the two areas stay decoupled while the call still lands on real code.
A slot binds once and never rebinds, which is what separates it from the settings-driven backend managers in :doc:`backends`.

``PartialShaper`` is the port the framework ships.
``PartialIntentView`` is the read-only view of a parsed partial request that travels between its methods, so a shape method never re-reads the request headers.
``PartialShaperSlot`` starts unbound and raises ``RuntimeError`` when read before the binding, and ``partial_shaper_slot`` is the single instance the framework uses.

``next.apps`` binds the implementation from ``next.partial`` as the last step of ``NextFrameworkConfig.ready()``.
``next.pages`` and ``next.forms`` read the slot on the request path, first to ask whether a request is partial at all and then to shape the response when it is.
Neither subsystem imports ``next.partial``.

Public API
----------

.. automodule:: next.ports
   :members:

See also
--------

.. seealso::

   :doc:`apps` for the startup step that binds the slot.
   :doc:`partial` for the subsystem that implements ``PartialShaper``.
   :doc:`/content/internals/overview` for where ``next.ports`` sits in the subsystem dependency graph.
