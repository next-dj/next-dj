.. _misc-design-philosophy:

Design Philosophy
=================

This page captures the design principles behind next.dj.
Read it to understand why the framework picks one path over another.

.. contents::
   :local:
   :depth: 2

Stay Inside Django
------------------

next.dj is a library on top of Django, not a replacement.
Models, the ORM, admin, auth, migrations, and the staticfiles pipeline remain unchanged.
A Django developer can adopt next.dj for the routing and template layers without throwing away years of accumulated knowledge.

Filesystem as a Single Source of Truth
--------------------------------------

A directory layout describes the URL tree, the layout tree, the component registry, and the asset tree.
A reader does not switch between five settings files to learn the shape of a project.
A change in the file system propagates without manual wiring.

Convention With Explicit Names
------------------------------

The framework defines a small set of conventions (``page.py``, ``layout.djx``, ``template.djx``, ``component.djx``, ``COMPONENTS_DIR``, ``PAGES_DIR``).
Each convention has an explicit name that can be overridden through settings.
Magic class names and module level decorators that depend on the filename are absent.

Explicit Parameters
-------------------

Page modules, render functions, action handlers, and context functions declare what they need through ordinary Python parameters.
The resolver fills them.
A function that does not ask for the request never receives it, which keeps the signatures honest and the tests easy.

Composition Over Inheritance
----------------------------

Layouts compose by string substitution, not by Django ``{% extends %}``.
Components compose by name resolution, not by class hierarchies.
The framework prefers data over class inheritance whenever it makes sense.

Stable URLs
-----------

Every page gets a stable URL name in the ``next`` namespace.
Reorganising directories renames URLs, but templates that use ``{% url %}`` keep working because the name reflects the new path.

Small Public Surfaces
---------------------

Each subsystem exposes a narrow public API through its ``__init__.py``.
Deep imports work but are not part of the stability promise.
Project code that sticks to the public API survives upgrades unchanged.

Signals for Side Channels
-------------------------

Cross subsystem coordination uses signals.
A change in the route set, in the registered components, in the form actions, or in the asset registry fires a signal that an audit tool or a websocket subscriber can listen to.

No Lock In
----------

A project can drop next.dj at any time.
Page modules are plain Python.
Templates are plain Django templates.
The data lives in Django models.
Removing the dependency leaves the project intact.

Trade Offs
----------

The framework explicitly accepts a few trade offs.

Filesystem walks at startup.
   Discovery costs time during boot.
   Large projects can opt into ``LAZY_COMPONENT_MODULES``.

String composition of layouts.
   No Django template caching of the composed result.
   The framework recomputes the chain per render, which is acceptable for the average page.

Convention based naming.
   Directories must respect the naming rules.
   Renaming a captured directory changes the URL name.

These trade offs are the cost of keeping the developer model simple and the file router predictable.

See Also
--------

.. seealso::

   :doc:`/content/intro/overview` for the mental model.
   :doc:`/content/topics/extending` for the extension philosophy.
