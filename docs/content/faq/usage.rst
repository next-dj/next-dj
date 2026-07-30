.. _faq-usage:

Usage questions
===============

This page answers questions that come up while building a project with next.dj.

.. contents::
   :local:
   :depth: 2

How do I add a page
-------------------

Create a directory under the page root and add a ``page.py`` plus a ``template.djx`` inside it.
See :doc:`/content/howto/add-a-page` for a recipe.

How do I pass data to the template
----------------------------------

Use ``@context("key")`` inside ``page.py`` to publish a value.
The template renders the value as ``{{ key }}``.
See :doc:`/content/topics/context`.

How do I share data across pages
--------------------------------

Declare the context in a ``page.py`` that sits in a directory above the consuming page, and pass ``inherit_context=True`` to the decorator.
See :doc:`/content/howto/share-context-across-pages`.

How do I capture URL parameters
-------------------------------

Name the directory ``[param]`` for a string or ``[type:param]`` for a typed value.
Inside the page module annotate the parameter with ``DUrl[T]``.
See :doc:`/content/topics/file-router`.

How do I render a form
----------------------

Subclass ``next.forms.Form`` or ``next.forms.ModelForm`` and render the form with ``{% form "name" %}``.
The action name is derived automatically from the class name in ``snake_case``.
See :doc:`/content/intro/tutorial04`.

How do I update part of a page without a reload
-----------------------------------------------

Wrap the slice in ``{% zone "name" %}`` and point a form or a link at it with ``data-next-target="name"``.
The server re-renders only that zone and the client runtime swaps it in place.
See :doc:`/content/intro/tutorial06` for the walkthrough and :doc:`/content/topics/partial-rendering/index` for the full model.

How do I customise the static output
------------------------------------

Subclass a static backend, register its dotted path in ``STATIC_BACKENDS``, and override how tags or asset URLs are produced.
See :doc:`/content/howto/write-a-static-backend`.

How do I test a page
--------------------

Use ``NextClient`` from ``next.testing``.
See :doc:`/content/topics/testing`.

How do I run the development server
-----------------------------------

Run ``uv run python manage.py runserver``.
The autoreloader picks up new and changed page directories without a restart.

How do I deploy in production
-----------------------------

Serve the project through a WSGI or ASGI server and collect static files the same way as any Django project.
See :doc:`/content/deployment/index` for the framework-specific checklist.

How do I integrate Django admin
-------------------------------

Mount ``admin.site.urls`` above ``include("next.urls")`` in ``config/urls.py``.
See :doc:`/content/howto/integrate-django-admin`.

How do I split routes across applications
-----------------------------------------

Use ``APP_DIRS=True`` so every application contributes its own page tree.
Use ``DIRS`` to add project level page roots.
See :doc:`/content/topics/file-router`.

How do I share components across projects
-----------------------------------------

Place the shared components in one folder and reference it through ``COMPONENT_BACKENDS["DIRS"]``.
See :doc:`/content/howto/share-components-across-projects`.

How do I add context processors to pages
----------------------------------------

Add a ``context_processors`` list to the ``OPTIONS`` dict of the relevant page backend entry.
The list merges with the processors from the first ``TEMPLATES`` entry in Django settings.
Duplicates are dropped.
See :doc:`/content/topics/context` for the merge order and a full settings example.

How do I keep query parameters after a form action redirect
-----------------------------------------------------------

Build the redirect URL from the form's ``cleaned_data`` inside the action handler.
The ``{% form %}`` tag posts to the framework's action endpoint, so ``request.GET`` is empty on the POST side.
Reconstruct the query string from the validated fields instead.

.. code-block:: python
   :caption: notes/pages/search/page.py

   from django.http import HttpRequest, HttpResponseRedirect
   from django.urls import reverse
   from next.forms import Form, CharField

   class SearchForm(Form):
       q = CharField(required=False)

       def on_valid(self, request: HttpRequest) -> HttpResponseRedirect:
           q = self.cleaned_data.get("q", "")
           base = reverse("next:page_")
           return HttpResponseRedirect(f"{base}?q={q}" if q else base)

The form registers as ``search_form`` automatically.
Use ``{% form "search_form" %}`` in the template.

For filter forms with no side effects, use ``<form method="get">`` directly and skip the form action altogether.
The ``DQuery`` marker then reads every filter from the query string on the GET request without a round-trip through the action endpoint.

Can a form action return a custom HTTP status code
--------------------------------------------------

Return any ``HttpResponseBase`` subclass.

.. code-block:: python
   :caption: notes/forms.py

   from django.http import HttpRequest, HttpResponse
   from notes.models import Note
   from next.forms import ModelForm

   class NoteForm(ModelForm):
       class Meta:
           model = Note
           fields = ("title", "body")

       def on_valid(self, request: HttpRequest) -> HttpResponse:
           self.save()
           return HttpResponse(status=204)

Common choices are ``HttpResponse(status=204)`` for no-content responses, ``HttpResponse(status=201)`` for created resources, and ``HttpResponseRedirect(url, status=303)`` for POST-redirect-GET flows.

How do I translate URLs or templates
------------------------------------

Internationalisation stays on Django's stack.
Configure ``LocaleMiddleware``, translation files, and ``i18n_patterns`` (or your preferred URL prefix strategy) the same way as in a stock Django project.
File routes resolve under whatever locale-aware prefix Django exposes.
next.dj does not ship a separate translation mechanism for ``page.py`` files beyond ordinary Django template translation tags.

See Django's :doc:`translation overview <django:topics/i18n/index>`.

See also
--------

.. seealso::

   :doc:`/content/howto/index` for recipes.
   :doc:`/content/topics/index` for in depth guides.
