.. _misc-roadmap:

Roadmap
=======

This page collects the boundaries the framework states about itself, grouped by theme.
Each boundary names where the current model stops and what a project reaches for instead.
The page carries no dates and no release names, and an entry here is a statement of the present shape rather than a commitment to change it.

.. contents::
   :local:
   :depth: 1

Streaming and real time
-----------------------

A zone renders standalone over the full page context in one synchronous pass, so a slow zone holds the response instead of streaming a placeholder and a later patch.
The streaming transport is Server-Sent Events in one direction from server to client, so client-to-server push over a persistent socket sits outside the model.
A ``poll=`` zone runs on a client timer with a floor of one second rather than on a server push, and a hidden tab holds no timers.
See :doc:`/content/topics/partial-rendering/limitations` for all three statements and :doc:`/content/topics/partial-rendering/sse` for the transport that exists today.

The patch protocol stays closed
-------------------------------

The server authors every operation a patch carries and the client never invents one, which :doc:`/content/topics/partial-rendering/extending` states as a deliberate choice rather than a gap.
``PARTIAL_BACKENDS`` activates its first entry and ignores the rest, so multi-backend selection is outside the model and a longer list earns the ``next.W071`` warning at ``manage.py check``.
A project that needs a different wire format subclasses ``PartialProtocolBackend`` and installs it as the single entry.
See :doc:`/content/topics/partial-rendering/limitations` for the boundary and :doc:`/content/topics/partial-rendering/extending` for the seams that open the protocol without forking the runtime.

Assets that arrive through a patch
----------------------------------

A ``<script>`` inside patch HTML never runs, because the applier strips it before the markup reaches the document, and behaviour binds through the mount idioms instead.
An asset the runtime inserts from an envelope carries a fixed attribute set, so an attribute a project adds to its tag templates reaches the browser on a full render and not on a patch.
Subresource Integrity is the case that matters most, and a deployment that relies on it treats an asset that first arrives through an envelope as outside that guarantee.
See :doc:`/content/topics/partial-rendering/limitations` for the full statement and :doc:`/content/security/static-assets` for the backend-side recipe.

Client framework adapters
-------------------------

The runtime ships the two events and the one attribute a Vue or React island needs, and :doc:`/content/topics/partial-rendering/framework-islands` prints the whole adapter for both.
It ships no compiled adapter, because a plugin that imports a framework cannot live in the page-wide bundle and would pull a framework release into the support matrix.
An island therefore stays project code rather than framework code.

Extension distribution and tooling
----------------------------------

The project ships no plugin registry, and :doc:`/content/faq/general` points at the five extension mechanisms in :doc:`/content/topics/extending` instead.
A customisation travels as an ordinary Python package.
The framework also adds no command line interface of its own, because ``manage.py`` and the framework system checks cover the operational surface.

Translation of page modules
---------------------------

The framework adds no separate translation mechanism for ``page.py`` files beyond the ordinary Django template translation tags.
See :doc:`/content/faq/usage` for the statement and the Django tags it points at.

Startup discovery cost
----------------------

Discovery walks the file system during boot, which :doc:`design-philosophy` names as an accepted trade-off rather than a solved problem.
A large project opts into ``LAZY_COMPONENT_MODULES`` so a ``component.py`` is imported on the first render that resolves the component instead of during startup.
See :doc:`/content/deployment/settings` for the production reading of that flag.

Where a boundary gets discussed
-------------------------------

A question about any boundary above belongs in the repository :repo:`issue tracker <issues>` rather than in this page.
:doc:`/content/contributing/index` routes a contributor to the code and documentation workflows, and :doc:`project-status` states what a project can rely on while a boundary stands.

See also
--------

.. seealso::

   :doc:`project-status` for the public API surface and the supported versions.
   :doc:`design-philosophy` for the reasoning behind the decisions these boundaries follow from.
   :doc:`/content/topics/partial-rendering/limitations` for the partial model in full.
