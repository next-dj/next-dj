.. _misc-design-philosophy:

Design philosophy
=================

Read this page when a framework decision surprises you and you want to understand the reasoning behind it.
Each section names the alternative the framework rejected and the cost it accepts in exchange.
For a practical description of what next.dj adds to Django, see :doc:`/content/intro/overview`.

.. contents::
   :local:
   :depth: 2

Stay inside Django
------------------

The rejected alternative is a framework that owns the whole stack and treats Django as one replaceable detail underneath it.
next.dj keeps ordinary Django concerns in place instead, and :ref:`intro-overview-django-unchanged` states the split.
The cost is that the framework inherits Django's constraints, including its template language, its request cycle, and its release cadence.
The gain is that an existing project keeps its models, its migrations, its admin, and the working knowledge of the team that runs it.

Filesystem as a single source of truth
--------------------------------------

The rejected alternative is a central registry, whether a URL configuration module, a component registry call, or an asset manifest.
A directory layout describes the URL tree, the layout tree, the component registry, and the asset tree, so a reader does not open five settings files to learn the shape of a project.
The cost is that a rename is a behaviour change.
Moving a directory changes the URL, the URL name, and the layout chain at the same time, and no static check finds a ``{% url %}`` call left behind.

Convention with explicit names
------------------------------

The rejected alternative is naming by inference, where a class name or a module name decides what a file becomes.
The framework defines a small set of file conventions instead (``page.py``, ``layout.djx``, ``template.djx``, ``component.djx``), and settings such as ``PAGES_DIR`` and ``COMPONENTS_DIR`` override where those conventions apply.
The cost is verbosity, because a page directory carries two files where a single decorated function would do.
The gain is that the meaning of a file is readable from its name without running the project.

Layouts compose by string substitution
--------------------------------------

The rejected alternative is Django template inheritance.
A page rendered through ``{% extends %}`` names its parent in its own first line, which puts the layout chain back into the templates and defeats the filesystem rule the rest of the framework keeps.
next.dj composes instead.
The loader walks ancestor directories, reads each ``layout.djx``, and substitutes the page body into the single ``{% block template %}{% endblock template %}`` slot the layout declares.

The cost is one slot per layout and no override across the chain.
A layout offers exactly one placeholder, the substitution fills it once, and a layout that declares no placeholder contributes nothing to the composed template.
Composition produces one flat template rather than a parent and a child, so a page cannot override a named region of an ancestor the way ``{% block sidebar %}`` would, and there is no ``{{ block.super }}`` to call.
A shared region that varies per page is expressed through ``@context`` and a component rather than through a block override.
See :doc:`/content/topics/layouts` for the discovery rules.

Dependency injection resolves by name
-------------------------------------

The rejected alternative is resolution by type alone, where the annotation is the only thing that identifies what a parameter receives.
Type-based resolution needs a distinct type per value, which forces a wrapper class around every published context key and every captured URL segment.
next.dj resolves by name first and by marker second.
A parameter whose name matches a published context key receives that value, and a parameter annotated ``DUrl[...]`` or ``DQuery[...]``, or defaulted to ``Depends(...)``, names its source explicitly.

The cost is that a name is a contract no tool checks.
Renaming a context key, or renaming the parameter that reads it, breaks the link silently, because a parameter no provider handles resolves to its default and to ``None`` when it has none.
The by-name provider also runs ahead of the URL and query providers, so publishing a context key shadows a captured URL segment carrying the same name.
Prefer ``Depends("name")`` and ``DUrl["segment"]`` wherever the binding has to survive a rename, and read :doc:`/content/topics/dependency-injection` for the full provider order.

The patch protocol is closed
----------------------------

The rejected alternative is an open protocol, where markup carries a selector and a swap strategy and the client executes whatever the response describes.
next.dj closes the protocol.
The server authors every verb, the client applies only verbs it already knows, and selectors and swap strategies never cross the wire.
The built-in set is fixed at ``morph``, ``replace``, ``inner``, ``append``, ``prepend``, ``remove``, ``refresh``, ``context``, ``event``, ``toast``, ``layer.open``, ``layer.close``, ``url``, and ``visit``.

The cost is that a new behaviour is a two-sided change.
A custom verb is registered on the server with ``register_patch_op`` and supplied on the client with ``Next.partial.defineOp``, so a template author cannot express a new DOM operation from markup alone.
The gain is that a response cannot ask a page to do anything the application never named, an unregistered verb raises instead of reaching the browser, and ``next.E066`` reports the mismatch at ``manage.py check``.
See :doc:`/content/topics/partial-rendering/extending` for the three seams and :doc:`/content/topics/partial-rendering/limitations` for what the closed set does not cover.

Stable URLs
-----------

The rejected alternative is a hand-written route name per view, which lets a name stay put while the path moves.
Every page gets a stable URL name derived from its directory path instead, so a reader can reverse it without consulting a URL configuration file.
The cost lands on refactoring.
Moving a directory changes both the URL and its name, and every ``{% url %}`` call has to be updated to match.

Forms and form dispatch
-----------------------

The rejected alternative is the Django default, where a form needs a URL entry, a view, and an explicit registration step before it accepts a POST.
A form action is wired the same way a page is instead.
The class is the unit of registration, file scope decides its reach, and ``__init_subclass__`` registers it the moment Python runs the ``class`` statement.
Every action posts to one endpoint, ``/_next/form/<uid>/``, where the ``uid`` is a stable short id derived from the action's scope and name, so moving a form between pages never changes the URL configuration.

The cost is that registration happens on import and reaches further than a single view would.
An action is live as soon as its module is imported, and the endpoint accepts a POST from any visitor until the form declares a guard.
:doc:`/content/security/overview` states where the identity check belongs and which guard keys enforce it.

Small public surfaces
---------------------

The rejected alternative is treating every importable module as public, which turns each internal rename into a breaking change.
Each subsystem exposes a narrow public API through its ``__init__.py`` instead.
Importing deeper modules may work at runtime, yet anything not listed in :doc:`/content/ref/index` or :doc:`/content/faq/general` as stable is not part of the documented contract for application code.
The cost is that some real capability sits behind an undocumented name until a release promotes it, so keep application imports to symbols the reference documents.

Signals for side channels
-------------------------

The rejected alternative is a plugin registry with declared hook points, which fixes in advance what an extension is allowed to observe.
Cross subsystem coordination uses Django signals instead.
A change in the route set, in the registered components, in the form actions, or in the asset registry fires a signal that an audit tool or a websocket subscriber can listen to.
The cost is that a signal carries no ordering guarantee and no failure contract, so a receiver that raises affects the sender.

Lock-in stops at the UI layer
-----------------------------

The heading names a boundary rather than claiming that no lock-in exists.
Data lives in Django models, migrations and the admin are untouched, and ordinary ``.html`` templates keep rendering.
The routed UI layer is the part that depends on next.dj, covering page modules, layout composition, ``@context`` callables, form actions, and the framework template tags.
Removing the framework therefore costs a view for every ``page.py``, a ``path()`` entry for every routed directory, a ``get_context_data`` for every ``@context`` callable, and a view plus a URL entry for every registered form action.
Nothing below that layer moves.

Trade-offs
----------

Filesystem walks at startup.
   Discovery reads the page, component, and asset trees during boot.
   Large projects can opt into ``LAZY_COMPONENT_MODULES``.

String composition of layouts.
   The composed template is cached and compiled per page and invalidated by source mtime.
   A body produced by a ``render`` function bypasses that cache and is recomposed per render.

Convention based naming.
   Directories must respect the naming rules.
   Renaming a captured directory changes the URL name.

Name-based resolution.
   A parameter bound by name has no static checker behind it.
   A rename on either side yields ``None`` rather than an error.

Template parsing changes process wide.
   The framework reinstalls Django's tag pattern with ``re.DOTALL``, so a ``{% ... %}`` token may span lines in every template the process loads.
   A template that relies on a newline ending a tag needs adjusting first.

Two-sided protocol extensions.
   A custom patch verb is registered on the server and defined on the client.
   Neither half is useful without the other.

Unauthenticated action endpoints.
   A registered form action accepts a POST from any visitor until the form declares a guard.
   The framework makes registration free and leaves authorization explicit.

These trade-offs are the cost of keeping the developer model simple and the file router predictable.

See also
--------

.. seealso::

   :doc:`/content/intro/overview` for the mental model.
   :doc:`/content/topics/extending` for the extension philosophy.
