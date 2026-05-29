.. _faq-usage:

Usage Questions
===============

This page answers questions that come up while building a project with next.dj.

.. contents::
   :local:
   :depth: 2

How Do I Add a Page
-------------------

Create a directory under the page root and add a ``page.py`` plus a ``template.djx`` inside it.
See :doc:`/content/howto/add-a-page` for a recipe.

How Do I Pass Data to the Template
----------------------------------

Use ``@context("key")`` inside ``page.py`` to publish a value.
The template renders the value as ``{{ key }}``.
See :doc:`/content/topics/context`.

How Do I Share Data Across Pages
--------------------------------

Declare the context in a ``page.py`` that sits in a directory above the consuming page, and pass ``inherit_context=True`` to the decorator.
See :doc:`/content/howto/share-context-across-pages`.

How Do I Capture URL Parameters
-------------------------------

Name the directory ``[param]`` for a string or ``[type:param]`` for a typed value.
Inside the page module annotate the parameter with ``DUrl[T]``.
See :doc:`/content/topics/file-router`.

How Do I Render a Form
----------------------

Subclass ``next.forms.Form`` or ``next.forms.ModelForm`` and render the form with ``{% form "name" %}``.
The action name is derived automatically from the class name in ``snake_case``.
See :doc:`/content/intro/tutorial04`.

How Do I Customise the Static Output
------------------------------------

Subclass a static backend, register its dotted path in ``DEFAULT_STATIC_BACKENDS``, and override how tags or asset URLs are produced.
See :doc:`/content/howto/write-a-static-backend`.

How Do I Test a Page
--------------------

Use ``NextClient`` from ``next.testing.client``.
See :doc:`/content/topics/testing`.

How Do I Run the Development Server
-----------------------------------

Run ``uv run python manage.py runserver``.
The autoreloader picks up new and changed page directories without a restart.

How Do I Deploy in Production
-----------------------------

Serve the project through a WSGI or ASGI server and collect static files the same way as any Django project.
See :doc:`/content/deployment/index` for the framework-specific checklist.

How Do I Integrate Django Admin
-------------------------------

Mount ``admin.site.urls`` above ``include("next.urls")`` in ``config/urls.py``.
See :doc:`/content/howto/integrate-django-admin`.

How Do I Split Routes Across Applications
-----------------------------------------

Two strategies.
Use ``APP_DIRS=True`` so every application contributes its own page tree.
Use ``DIRS`` to add project level page roots.
See :doc:`/content/topics/file-router`.

How Do I Share Components Across Projects
-----------------------------------------

Place the shared components in one folder and reference it through ``DEFAULT_COMPONENT_BACKENDS["DIRS"]``.
See :doc:`/content/howto/share-components-across-projects`.

How Do I Add Context Processors to Pages
----------------------------------------

Add a ``context_processors`` list to the ``OPTIONS`` dict of the relevant page backend entry.
The list merges with the processors from the first ``TEMPLATES`` entry in Django settings.
Duplicates are dropped.
See :doc:`/content/topics/context` for the merge order and a full settings example.

How Do I Keep Query Parameters After a Form Action Redirect
-----------------------------------------------------------

Build the redirect URL from the form's ``cleaned_data`` inside the action handler.
The ``{% form %}`` tag posts to the framework's action endpoint, so ``request.GET`` is empty on the POST side.
Reconstruct the query string from the validated fields instead.

.. code-block:: python
   :caption: notes/pages/search/page.py

   from django.http import HttpRequest, HttpResponseRedirect
   from django.urls import reverse
   from next.forms import Form, CharField, redirect_to_origin

   class SearchForm(Form):
       q = CharField(required=False)

       def on_valid(self, request: HttpRequest) -> HttpResponseRedirect:
           q = self.cleaned_data.get("q", "")
           base = reverse("next:page_notes")
           return HttpResponseRedirect(f"{base}?q={q}" if q else base)

The form registers as ``search_form`` automatically.
Use ``{% form "search_form" %}`` in the template.

For filter forms with no side effects, use ``<form method="get">`` directly and skip the form action altogether.
The ``DQuery`` marker then reads every filter from the query string on the GET request without a round-trip through the action endpoint.

Can a Form Action Return a Custom HTTP Status Code
--------------------------------------------------

Return any ``HttpResponseBase`` subclass.

.. code-block:: python
   :caption: notes/pages/page.py

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

How Do I Translate URLs or Templates
------------------------------------

Internationalisation stays on Django's stack.
Configure ``LocaleMiddleware``, translation files, and ``i18n_patterns`` (or your preferred URL prefix strategy) the same way as in a stock Django project.
File routes resolve under whatever locale-aware prefix Django exposes.
next.dj does not ship a separate translation mechanism for ``page.py`` files beyond ordinary Django template translation tags.

See Django's :doc:`translation overview <django:topics/i18n/index>`.

See Also
--------

.. seealso::

   :doc:`/content/howto/index` for recipes.
   :doc:`/content/topics/index` for in depth guides.
