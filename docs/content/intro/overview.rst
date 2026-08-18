.. _intro-overview:

Overview
========

next.dj is a framework built on Django that turns the filesystem into your URL router, layout tree, and component registry.
It extends a regular Django project while leaving the ORM, admin, auth, and migrations to Django.

This page describes the mental model.
Read it once before the tutorial, then refer back when the layout of a real project surprises you.

Who this is for
---------------

A Django project that grows a modern interactive frontend has historically pushed that frontend out of Django.
The team adds React or Vue, then an API layer to feed it, a build toolchain to ship it, and a second copy of the application state so the two halves agree.
next.dj exists to remove the reasons for that move, so a Django project stays enough for the whole application.
It targets the interaction patterns that drove the split rather than the data layer, which Django already serves well.

Three profiles map onto that goal.

A team maintaining a server-rendered Django site.
   The wiring shrinks.
   The URL configuration, the view layer that exists only to render a template, and the per-form redirect plumbing move into the directory tree.

A team running Django beside a separate single-page frontend.
   The second stack becomes optional.
   Partial rendering updates a named slice of a page from the server, so an interaction no longer needs a JSON endpoint plus a client-side copy of the state that renders it.

A team that wants component structure without a JavaScript build step.
   A component is a folder holding a template with optional Python, CSS, and JS beside it, and the framework collects those assets for the pages that use them.
   The requirements in :doc:`install` stay Python, Django, and an ASGI or WSGI server.

What next.dj adds
-----------------

next.dj layers six things on top of a regular Django project.
Each one starts from work a Django team does by hand today and names the mechanism that removes it.

File router.
   A new page costs a ``path()`` entry, a view, and a route name to reverse.
   Every directory under a configured page root becomes a URL instead, and a ``page.py`` turns it into a navigable page.
   A bracketed segment such as ``[slug]`` becomes a captured URL parameter.
   See :doc:`/content/topics/file-router`.

Layouts and context.
   A shared page envelope costs an ``{% extends %}`` line in every template and a repeated ``get_context_data`` on every view.
   A ``layout.djx`` wraps every page under its directory instead, and layouts nest down the tree.
   A ``@context`` decorator publishes named values into the template scope, optionally inherited by every descendant page.
   See :doc:`/content/topics/layouts` and :doc:`/content/topics/context`.

Dependency injection.
   A Django view receives the request and the URL kwargs and fetches everything else itself, which repeats the same lookup in every view that needs it.
   Context functions, action handlers, and providers declare what they need as ordinary parameters instead, and the resolver fills them from the request, the URL, the query string, or a registered provider.
   Markers such as ``DUrl`` and ``Depends`` name the source in the annotation.
   See :doc:`/content/topics/dependency-injection`.

Components.
   A reusable fragment costs an ``{% include %}`` plus a separate decision about where its CSS and JS live and how they reach the page.
   A folder under the configured components root becomes a reusable template fragment with optional Python, CSS, and JS files instead.
   The framework discovers components by name, renders them through the ``{% component %}`` tag, and collects their assets for the pages that use them.
   See :doc:`/content/topics/components`.

Form actions.
   A form costs a URL entry, a view, CSRF handling, and a redirect on success before it accepts a single POST.
   Subclassing ``next.forms.Form`` or ``next.forms.ModelForm`` registers the form under a ``snake_case`` name instead, rendered by ``{% form "name" %}`` and validated into its ``on_valid`` method.
   Plain functions with no form can also register as actions with ``@action("name")``.
   See :doc:`/content/topics/forms/overview`.

Partial rendering.
   Updating part of a page costs a JSON endpoint, a client-side template, and a second copy of the state that renders it.
   A ``{% zone %}`` block names a slice of a page the server can re-render on its own instead, and a form, filter, or link targets that zone.
   Every interaction degrades to a full page cycle when JavaScript is off.
   See :doc:`/content/topics/partial-rendering/index`.

.. _intro-overview-django-unchanged:

What next.dj does not replace
-----------------------------

The ORM, migrations, admin, auth, and middleware stay the same as in a stock Django project.
next.dj adds the ``NEXT_FRAMEWORK`` dict, includes ``next.urls`` for the file router, and resolves ``.djx`` through ``DjxTemplateLoader``.
Standard ``.html`` templates in other apps keep rendering, and they gain the framework tags, because next.dj registers its template tag libraries as Django builtins.

One parsing change does reach every template the process loads.
The framework reinstalls Django's template tag pattern with the ``re.DOTALL`` flag, so a ``{% ... %}`` token may span several lines.
A template that relies on a newline ending a tag needs adjusting before adopting next.dj, and :doc:`/content/ref/template-tags` states the rule.

For the design principles behind that split, read :doc:`/content/misc/design-philosophy`.

The nouns *page*, *layout*, *component*, *action*, and *context function* appear on every documentation page.
:doc:`/content/misc/glossary` defines each one.

A minimal project
-----------------

Once installed, the smallest next.dj project is a ``page.py`` plus a ``template.djx`` under an app's ``pages/`` directory such as ``notes/pages/``.
It also needs the ``NEXT_FRAMEWORK`` block in ``config/settings.py`` and a one-line ``include("next.urls")`` in ``config/urls.py``.
:doc:`install` shows the full three-file shape with each block spelled out.
Every new directory under ``pages/`` then adds another page without touching the URL configuration.

When to read the tutorial
-------------------------

If you have used Django before and want to feel the framework, jump to :doc:`tutorial01`.
The six tutorial parts build a small Notes application that exercises every core subsystem.
The first four parts wire up routing, layouts, components, and forms.
The fifth adds tests and the development workflow, and the sixth makes the Notes index update in place with partial rendering.

.. seealso::

   :doc:`install` for environment setup.
   :doc:`whatsnext` for topic hubs after the tutorial.
   :doc:`/content/topics/index` for in-depth topic guides.
   :doc:`/content/ref/index` for the API reference.
