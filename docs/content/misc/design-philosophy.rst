.. _misc-design-philosophy:

Design philosophy
=================

Read this page when a framework decision surprises you and you want to understand the reasoning behind it.
For a practical description of what next.dj adds to Django, see :doc:`/content/intro/overview`.

.. contents::
   :local:
   :depth: 2

Stay inside Django
------------------

Ordinary Django concerns stay in place.
See :ref:`intro-overview-django-unchanged` in :doc:`/content/intro/overview` for the split.
This page explains why routing, layouts, components, assets, and form dispatch sit on that base and what trade-offs follow.

Filesystem as a single source of truth
--------------------------------------

A directory layout describes the URL tree, the layout tree, the component registry, and the asset tree.
A reader does not switch between five settings files to learn the shape of a project.
A change in the file system propagates without manual wiring.

Convention with explicit names
------------------------------

The framework defines a small set of conventions (``page.py``, ``layout.djx``, ``template.djx``, ``component.djx``, ``COMPONENTS_DIR``, ``PAGES_DIR``).
Each convention has an explicit name that can be overridden through settings.
Magic class names and module level decorators that depend on the filename are absent.

Explicit parameters
-------------------

Page modules, render functions, action handlers, and context functions declare what they need through ordinary Python parameters.
The resolver fills them.
A function that does not ask for the request never receives it, which keeps the signatures honest and the tests easy.

Composition over inheritance
----------------------------

Layouts compose by string substitution, not by Django ``{% extends %}``.
Components compose by name resolution, not by class hierarchies.

Stable URLs
-----------

Every page gets a stable URL name derived from its directory path.
The name is predictable, so a reader can reverse it without consulting a URL configuration file.
Moving a directory changes both the URL and its name, and ``{% url %}`` calls must be updated to match.

Forms and form dispatch
-----------------------

A form action is wired the same way a page is.
The class is the unit of registration, file scope decides its reach, and ``__init_subclass__`` registers it the moment Python runs the ``class`` statement, so a form needs no URL entry, no view, and no decorator.
This follows the same filesystem-as-source-of-truth rule the rest of the framework keeps.

Every action posts to one endpoint, ``/_next/form/<uid>/``, where the ``uid`` is a stable short id derived from the action's scope and name, so moving a form between pages never changes the URL configuration.
The dispatcher resolves the uid back to the registered action and runs the validation and re-render pipeline.

Small public surfaces
---------------------

Each subsystem exposes a narrow public API through its ``__init__.py``.
Importing deeper modules may work at runtime, yet anything not listed in :doc:`/content/ref/index` or :doc:`/content/faq/general` as stable is not part of the documented contract for application code.
Keep application imports to the documented top-level ``next.*`` symbols.

Signals for side channels
-------------------------

Cross subsystem coordination uses signals.
A change in the route set, in the registered components, in the form actions, or in the asset registry fires a signal that an audit tool or a websocket subscriber can listen to.

No lock in
----------

The data lives in Django models and standard Django templates keep working.
Page modules, layout composition, and framework template tags depend on next.dj, so removing it means rewriting the routed UI layer, not the data layer.

Trade offs
----------

Filesystem walks at startup.
   Discovery costs time during boot.
   Large projects can opt into ``LAZY_COMPONENT_MODULES``.

String composition of layouts.
   The composed template is cached and compiled per page and invalidated by source mtime.
   A body produced by a ``render`` function bypasses that cache and is recomposed per render.

Convention based naming.
   Directories must respect the naming rules.
   Renaming a captured directory changes the URL name.

These trade offs are the cost of keeping the developer model simple and the file router predictable.

See also
--------

.. seealso::

   :doc:`/content/intro/overview` for the mental model.
   :doc:`/content/topics/extending` for the extension philosophy.
