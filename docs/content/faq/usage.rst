.. _faq-usage:

Usage questions
===============

This page answers questions that come up while building a project with next.dj.

.. contents::
   :local:
   :depth: 2

Where the step by step recipes live
-----------------------------------

:doc:`/content/howto/index` groups the recipes by task, and each one states a problem, gives one minimal solution, and shows how to verify it.
Adding a page, publishing context to a template, sharing context across a page tree, reading query parameters, customising the rendered asset tags, rendering a form, testing a page that posts to an action, and splitting a page tree across applications each have a guide there.
:doc:`/content/topics/index` carries the concept behind every recipe.
The entries on this page answer what no guide covers on its own.

How do I update part of a page without a reload
-----------------------------------------------

Wrap the slice in ``{% zone "name" %}`` and name the zone through the ``zone`` argument of the form tag, written ``{% form "action_name" zone="name" %}``.
The tag compiles that argument into the ``data-next-target`` attribute itself and reserves every ``data-next-`` name, so passing ``data-next-target`` to the tag raises ``TemplateSyntaxError`` while the template is parsed.
A hand-written link carries ``data-next-target="name"`` as a plain HTML attribute, because no tag stands between it and the document.
The server re-renders only that zone and the client runtime swaps it in place.
See :doc:`/content/topics/forms/templates` for the tag arguments, :doc:`/content/intro/tutorial06` for the walkthrough, and :doc:`/content/topics/partial-rendering/index` for the full model.

How do I run the development server
-----------------------------------

Run ``uv run python manage.py runserver``.
The autoreloader picks up new and changed page directories without a restart.

How do I deploy in production
-----------------------------

Serve the project through a WSGI or ASGI server and collect static files the same way as any Django project.
See :doc:`/content/deployment/index` for the framework-specific checklist.

Can I run Django REST Framework alongside next.dj
--------------------------------------------------

Yes, and the two do not interact.
A DRF view is an ordinary Django view reached through an ordinary URLconf entry, so it coexists with file-routed pages the same way the admin does.
Mount the API above ``include("next.urls")`` in ``config/urls.py`` and the API paths resolve before the router ever sees them.

.. code-block:: python
   :caption: config/urls.py

   from django.urls import include, path

   urlpatterns = [
       path("api/", include("api.urls")),
       path("", include("next.urls")),
   ]

next.dj contributes no middleware, no authentication layer, and no renderer, so the DRF request cycle is untouched.
The framework registers its template tags as Django builtins, which changes nothing for the browsable API because those templates never call them.

JSON APIs for mobile clients and third-party consumers are out of scope for next.dj.
The framework renders HTML for a browser, and it offers no serializer layer, no schema generation, and no content negotiation.
A ``render`` function may return a :class:`~django.http.JsonResponse` for a small internal endpoint, described under *Common patterns* in :doc:`/content/topics/pages`, but that is a convenience rather than an API framework.

See :doc:`/content/howto/integrate-django-admin` for the same mounting pattern applied to the admin, and *Coexisting with plain Django views* in :doc:`/content/topics/pages` for the general boundary.

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

   from next.forms import CharField, Form

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
   :doc:`/content/security/overview` for the guard model behind the form, zone, and stream surfaces.
