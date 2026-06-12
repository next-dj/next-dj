.. _topics-forms-overview:

Forms Overview
==============

A Django form usually costs a URL entry, a view, manual CSRF handling, and a redirect-on-success per action before it accepts a single POST.
The forms subsystem removes that wiring.
Declaring a subclass of ``next.forms.Form`` or ``next.forms.ModelForm`` is enough to make that form reachable by name in any template in the project, with a POST endpoint, CSRF, and re-render-on-failure already attached.
No decorator, no manual registry call, and no URL wiring is required.

.. contents::
   :local:
   :depth: 2

Auto-Registration
-----------------

Every subclass of ``next.forms.BaseForm`` or ``next.forms.BaseModelForm`` registers itself through the ``__init_subclass__`` hook the moment Python executes the ``class`` statement.

The framework derives the action name from the class name by converting ``CamelCase`` to ``snake_case``:
``ArticleEditForm`` becomes ``article_edit_form``, ``ContactForm`` becomes ``contact_form``.

The framework also records which file the class was declared in and uses that to decide its scope.
:doc:`actions` is the canonical reference for name derivation and scope.

.. code-block:: python
   :caption: page.py — auto-registered as ``article_edit_form``

   import next.forms
   from django.http import HttpRequest
   from next.forms import redirect_to_origin

   class ArticleEditForm(next.forms.ModelForm):
       class Meta:
           model = Article
           fields = ["title", "body"]

       def on_valid(self, request: HttpRequest):
           self.save()
           return redirect_to_origin(request)

Why a Stable URL
----------------

The framework hashes the action's scope key and name into a single POST endpoint at ``/_next/form/<uid>/``.
The URL is derived deterministically from the class, so a form needs no URL wiring and no per-page route.
The same form can be embedded on any page that renders its tag and every copy submits to the same endpoint.

File Scope (Anchor Files)
-------------------------

A single global registry would force every form name to be unique across the whole project, so two teams could not both ship a ``NoteForm`` without coordinating names.
File scope removes that coupling.
When a form class is declared in ``page.py`` or ``component.py``, its scope is ``page``.
A page-scoped form is keyed to its definition file, so two different pages may each declare an ``ArticleEditForm`` without collision.
The ``{% form "article_edit_form" %}`` tag on a given page resolves the form registered in that page's own file first.

Shared Scope
------------

A form class declared in any other file (``forms.py``, ``models.py``, a module imported by ``AppConfig.ready``, etc.) receives ``shared`` scope.
A shared form has a project-wide name and is reachable from any template.

.. code-block:: python
   :caption: app/forms.py — auto-registered as ``contact_form`` (shared)

   import next.forms
   from next.forms import redirect_to_origin

   class ContactForm(next.forms.Form):
       email = next.forms.EmailField()

       def on_valid(self, request):
           send_email(self.cleaned_data["email"])
           return redirect_to_origin(request)

Autodiscover
------------

``NextFrameworkConfig.ready`` calls ``autodiscover_forms()`` once on startup.
It imports the ``forms`` submodule of every installed app so shared forms declared in ``app/forms.py`` register before the first request arrives.
Set ``NEXT_FRAMEWORK["FORM_AUTODISCOVER"] = False`` to disable the automatic import.

Override Scope
--------------

Set ``Meta.scope`` to pin the form to a specific scope regardless of file name.

.. code-block:: python
   :caption: forcing page scope from forms.py

   class LoginForm(next.forms.Form):
       class Meta:
           scope = "page"

       username = next.forms.CharField()
       password = next.forms.CharField(widget=next.forms.PasswordInput)

Valid values are ``"page"`` and ``"shared"``.
Any other value triggers the ``next.E047`` system check and the form is not registered.

Handling Submissions
--------------------

Override ``on_valid`` to run code after the form passes validation.
The method receives at least ``request`` and may declare any parameter the dependency injector knows how to resolve.

.. code-block:: python

   def on_valid(self, request):
       self.save()
       return redirect_to_origin(request)

The default implementation redirects to the origin page, and a ModelForm saves first.
See :doc:`actions` for the exact default behaviour and return contract.

``get_initial`` prepopulates the form before the first render.
Declare it as a ``classmethod`` with the same DI-friendly signature.

.. code-block:: python

   @classmethod
   def get_initial(cls, request, note_id: int | None = None):
       if note_id is None:
           return {}
       return Note.objects.get(pk=note_id)

The framework calls ``get_initial`` through the dependency injector, never application code.
``request`` is supplied automatically, a parameter named after a URL segment is filled from the URL, and the rest resolve through providers.
See :doc:`actions` for the full signature rules.

Shared Dependency Cache
-----------------------

``get_initial``, the handler, and the re-render share one per-request dependency cache.
An expensive provider such as a tenant lookup or a permission check runs once per request, even when validation fails and the page re-renders.
See :doc:`validation-rerender` for the cache mechanics and the access path.

Form-Less Actions
-----------------

Use ``@action`` to register a plain function when no form fields are needed.
A logout button or a delete confirmation is a typical case.
The name is optional — a bare ``@action`` registers the function under its own name.

.. code-block:: python
   :caption: page.py

   from django.http import HttpRequest
   from next.forms import action, redirect_to_origin
   from next.urls import DUrl

   @action("delete_article")
   def delete_article(article_id: DUrl["id", int], request: HttpRequest):
       Article.objects.filter(pk=article_id).delete()
       return redirect_to_origin(request)

The template tag works the same way, but ``form`` is ``None`` inside the block because there is no form class.

Template Usage
--------------

The ``{% form "name" %}`` block tag renders the ``<form>`` element, injects the CSRF token, and publishes ``form`` inside the block body.

.. code-block:: jinja
   :caption: template.djx

   {% form "article_edit_form" %}
     {{ form.title }}
     {{ form.body }}
     <button type="submit">Save</button>
   {% endform %}

See :doc:`templates` for the full tag reference.

See Also
--------

.. seealso::

   :doc:`actions` for auto-registration details, name derivation, and system checks.
   :doc:`templates` for the ``{% form %}`` tag.
   :doc:`validation-rerender` for the re-render pipeline.
   :doc:`backends` for swapping the dispatch backend.
