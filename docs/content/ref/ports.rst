.. _ref-ports:

Ports reference
===============

Module summary
--------------

``next.ports`` holds the narrow protocols one subsystem calls another through.
Each port is a pair of a ``Protocol`` that states the method contract the caller depends on and a slot object that holds the one implementation composed at startup.
The caller imports the slot instead of the implementing subsystem, so the two areas stay decoupled while the call still lands on real code.
Each slot is bound once, in ``NextFrameworkConfig.ready()``, and nothing rebinds it afterwards, which is what separates it from the settings-driven backend managers in :doc:`backends` that rebuild themselves on a settings reload.

``PortSlot`` is the shared holder every port uses.
It starts unbound and raises ``RuntimeError`` naming the missing binding when read too early, and each port subclasses it so the message names its own subject.

``PartialShaper`` shapes page and form responses for partial requests.
``PartialIntentView`` is the read-only view of a parsed partial request that travels between its methods, so a shape method never re-reads the request headers.
``next.pages`` and ``next.forms`` read ``partial_shaper_slot`` on the request path, first to ask whether a request is partial at all and then to shape the response when it is.
Neither subsystem imports ``next.partial``.

``RouterAccess`` builds router backends and router managers.
``next.urls`` routes to pages and so imports ``next.pages``, which leaves the page watcher and the system checks needing routers from the other direction.
They read ``router_access_slot`` instead, at watch time and at check time.

``StaticAssets`` is the static-manager surface one page render calls, a collector, page asset discovery, and placeholder injection.
``next.static`` reads page trees and page modules and so imports ``next.pages``, so the render path reads ``static_assets_slot`` rather than importing the static manager back.
The slot holds the lazy default handle, so a settings reload that drops the wrapped manager still reaches every later render.

``next.apps`` binds all three in ``NextFrameworkConfig.ready()``.

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
