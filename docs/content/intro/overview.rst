.. _intro-overview:

Overview
========

next.dj is a framework built on Django that turns the filesystem into your URL router, layout tree, and component registry.
It extends a regular Django project while leaving the ORM, admin, auth, and migrations to Django.

This page describes the mental model.
Read it once before the tutorial, then refer back when the layout of a real project surprises you.

Why it exists
-------------

A Django project that grows a modern interactive frontend has historically pushed that frontend out of Django.
The team adds React or Vue, then an API layer to feed it, a build toolchain to ship it, and a second copy of the application state so the two halves agree.
next.dj exists to remove the reasons for that move, so a Django project stays enough for the whole application.
It targets the interaction patterns that drove the split rather than the data layer, which Django already serves well.
The requirements in :doc:`install` stay Python, Django, and an ASGI or WSGI server, with no JavaScript build step.

What next.dj adds
-----------------

next.dj layers eight things on top of a regular Django project.
Each one starts from work a Django team does by hand today and names the mechanism that removes it.

File router.
   A new page costs a ``path()`` entry, a view, and a route name to reverse.
   Every directory under a configured page root becomes a URL instead, and a ``page.py`` turns it into a navigable page.
   A bracketed segment such as ``[slug]`` becomes a captured URL parameter.
   A ``(name)/`` directory groups pages without touching the URL, and a ``(.)name/`` directory opens a page as a modal over another, see :ref:`topics-file-router-route-groups` and :ref:`topics-file-router-intercepts`.
   Resolving a request follows the depth of the path it asks for rather than the number of pages the project holds.
   See :doc:`/content/topics/file-router`.

Layouts and context.
   A shared page envelope costs an ``{% extends %}`` line in every template and a repeated ``get_context_data`` on every view.
   A ``layout.djx`` wraps every page under its directory instead, and layouts nest down the tree.
   A ``{% layout root %}`` marker in a layout gives a section a document shell of its own, see :ref:`topics-layouts-root`.
   A ``@context`` decorator publishes named values into the template scope, optionally inherited by every descendant page.
   A ``page.py`` declares the login and the permissions its page requires, and with ``inherit=True`` every page below it, and the framework checks them on the page, its actions, and its zones.
   See :doc:`/content/topics/layouts`, :doc:`/content/topics/context`, and :doc:`/content/topics/access`.

Dependency injection.
   A Django view receives the request and the URL kwargs and fetches everything else itself, which repeats the same lookup in every view that needs it.
   Context functions, action handlers, and providers declare what they need as ordinary parameters instead, and the resolver fills them from the request, the URL, the query string, or a registered provider.
   Annotation markers such as ``DUrl`` name the source in the type position, and ``Depends(...)`` names it as the parameter default.
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
   A ``{% zone %}`` block names a slice of a page the server re-renders on its own, and a form, filter, or link targets that zone.
   With JavaScript off the same markup is an ordinary form or anchor, so the page falls back to a full cycle.
   Zones can also update from a realtime topic, see :doc:`/content/topics/realtime`.
   See :doc:`/content/topics/partial-rendering/index`.

Co-located assets.
   A stylesheet or a script for one page or component costs a static file path, a tag repeated in every template that needs it, and the discipline to remove the tag when the markup goes.
   A file whose stem matches the ``template.djx``, ``layout.djx``, or ``component.djx`` beside it is discovered as that owner's asset instead, so ``component.css`` and ``component.js`` belong to the component that owns them.
   The ``{% collect_styles %}`` and ``{% collect_scripts %}`` tags mark the collector slots in the layout where the collected assets of the rendered page land.
   A file that no page or component owns, such as a compiled bundle, is named in a tag and resolved through Django staticfiles, which is where ``{% static %}`` reads from as well.
   See :doc:`/content/topics/static-assets/index`.

Metadata and SEO.
   The head of a page costs a block in every template and a sitemap and robots view kept by hand.
   A ``metadata`` declaration on a page or layout builds the title, description, and social tags, and the site publishes a sitemap and a robots file from the same sources instead.
   See :doc:`/content/topics/seo/index`.

.. _intro-overview-django-unchanged:

What next.dj does not replace
-----------------------------

The ORM, migrations, admin, auth, and middleware stay the same as in a stock Django project.
next.dj adds the ``NEXT_FRAMEWORK`` dict, includes ``next.urls`` for the file router, and resolves ``.djx`` through ``DjxTemplateLoader``.
Standard ``.html`` templates in other apps keep rendering, and they gain the framework tags, because next.dj registers its template tag libraries as Django builtins.

One parsing change reaches the lexer every template in the process shares.
The framework adds a line-spanning branch to Django's template tag pattern at startup, and that branch matches only next.dj's own block tags, so one of them may span several lines.
Every other tag keeps its stock behaviour, a newline inside it still ends it, and ``{{ ... }}`` and ``{# ... #}`` are untouched, so an existing template needs no adjustment before adopting next.dj.
:doc:`/content/ref/template-tags` states the rule.

The nouns *page*, *layout*, *component*, *action*, and *context function* carry a specific meaning throughout this manual, and :doc:`/content/misc/glossary` defines each one.
:doc:`from-django` maps each Django idiom this page describes onto the shape that replaces it.

What the model costs
--------------------

Every mechanism trades something away.
:doc:`/content/misc/design-philosophy` states each cost beside its principle and :doc:`limitations` lists the boundaries.

A minimal project
-----------------

Once installed, the smallest next.dj project is a ``page.py`` plus a ``template.djx`` under an app's ``pages/`` directory such as ``notes/pages/``.
It also needs a one-line ``include("next.urls")`` in ``config/urls.py``.
Settings are optional, see :doc:`install`.
Every new directory under ``pages/`` then adds another page without touching the URL configuration.

.. seealso::

   :doc:`from-django` for the Django idiom behind each mechanism above.
   :doc:`install` for environment setup.
   :doc:`whatsnext` for topic hubs after the tutorial.
   :doc:`/content/topics/index` for in-depth topic guides.
   :doc:`/content/ref/index` for the API reference.
