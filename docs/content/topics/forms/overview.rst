.. _topics-forms-overview:

Forms overview
==============

A Django form usually costs a URL entry, a view, manual CSRF handling, and a redirect-on-success per action before it accepts a single POST.
The forms subsystem removes that wiring.
Declaring a subclass of ``next.forms.Form`` or ``next.forms.ModelForm`` is enough to make that form reachable by name from every template its scope covers, with a POST endpoint, CSRF, and re-render-on-failure already attached.
No decorator, no manual registry call, and no URL wiring is required.

.. contents::
   :local:
   :depth: 2

Auto-registration
-----------------

Every subclass of ``next.forms.BaseForm`` or ``next.forms.BaseModelForm`` registers itself through the ``__init_subclass__`` hook the moment Python executes the ``class`` statement.

The framework derives the action name from the class name by converting ``CamelCase`` to ``snake_case``.
``ArticleEditForm`` becomes ``article_edit_form``, ``ContactForm`` becomes ``contact_form``.

The framework also records which file the class was declared in and uses that to decide its scope.
:doc:`actions` is the canonical reference for name derivation and scope.

.. code-block:: python
   :caption: page.py — auto-registered as ``article_edit_form``

   from django.http import HttpRequest

   import next.forms
   from next.forms import redirect_to_origin

   class ArticleEditForm(next.forms.ModelForm):
       class Meta:
           model = Article
           fields = ["title", "body"]

       def on_valid(self, request: HttpRequest):
           self.save()
           return redirect_to_origin(request)

Why a stable URL
----------------

The framework hashes the action's scope key and name into a single POST endpoint at ``/_next/form/<uid>/``.
The URL is derived deterministically from the class, so a form needs no URL wiring and no per-page route.
The same form can be embedded on any page that renders its tag and every copy submits to the same endpoint.

Scope
-----

A form declared in ``page.py`` or ``component.py`` is page-scoped and keyed to its file, so two pages may each declare an ``ArticleEditForm`` without collision.
A form declared in any other file is shared, carries a project-wide name, and is reachable from any template.

.. code-block:: python
   :caption: app/forms.py — auto-registered as ``contact_form`` (shared)

   from django.http import HttpRequest

   import next.forms
   from next.forms import redirect_to_origin

   class ContactForm(next.forms.Form):
       email = next.forms.EmailField()

       def on_valid(self, request: HttpRequest):
           send_email(self.cleaned_data["email"])
           return redirect_to_origin(request)

Set ``Meta.scope`` to ``"page"`` or ``"shared"`` to pin the scope regardless of file name.
See :doc:`actions` for the anchor-file rule, the ``Meta.scope`` override, and the ``next.E047`` check.

Autodiscover
------------

``NextFrameworkConfig.ready`` calls ``autodiscover_forms()`` once on startup.
It imports the ``forms`` submodule of every installed app so shared forms declared in ``app/forms.py`` register before the first request arrives.
Set ``NEXT_FRAMEWORK["FORM_AUTODISCOVER"] = False`` to disable the automatic import.

Handling submissions
--------------------

Override ``on_valid`` to run code after the form passes validation.
The framework injects the current request only into a parameter annotated ``HttpRequest``, an unannotated ``request`` parameter resolves to ``None``.
The method may declare any other parameter the dependency injector knows how to resolve.

.. code-block:: python

   from django.http import HttpRequest

   def on_valid(self, request: HttpRequest):
       self.save()
       return redirect_to_origin(request)

The default implementation redirects to the origin page, and a ModelForm saves first.
See :doc:`actions` for the exact default behaviour and return contract.

``Meta.success_url`` and ``Meta.success_message`` declare the redirect target and a flash message without writing ``on_valid`` by hand.
The default ``on_valid`` reads both, so a save-and-redirect form can skip the method entirely.

``get_initial`` prepopulates the form before the first render.
Declare it as a ``classmethod`` with the same DI-friendly signature.

.. code-block:: python

   from django.http import HttpRequest

   @classmethod
   def get_initial(cls, request: HttpRequest, note_id: int | None = None):
       if note_id is None:
           return {}
       return Note.objects.get(pk=note_id)

The framework calls ``get_initial`` through the dependency injector, never application code.
The framework supplies ``request`` to any parameter annotated ``HttpRequest``, an unannotated ``request`` parameter resolves to ``None``.
A parameter named after a URL segment is filled from the URL, and the rest resolve through providers.
See :doc:`actions` for the full signature rules.

.. _topics-forms-overview-cbv-map:

Coming from Django class-based views
------------------------------------

A ``FormView`` splits its behaviour across overridden methods and mixins.
next.dj keeps the same set of decisions and moves them onto the form class itself, either as a ``Meta`` key or as a method the dependency injector calls.

.. list-table::
   :header-rows: 1
   :widths: 30 40 30

   * - Django class-based view
     - next.dj
     - Detail
   * - ``form_valid(form)``
     - ``on_valid``, a method on the form class
     - :doc:`actions`
   * - ``form_invalid(form)``
     - Nothing to write, the dispatcher re-renders the origin page with the bound form and its errors
     - :doc:`validation-rerender`
   * - ``success_url``
     - ``Meta.success_url``, which the default ``on_valid`` follows
     - :ref:`topics-forms-actions-success`
   * - :class:`~django.contrib.messages.views.SuccessMessageMixin` with ``success_message``
     - ``Meta.success_message``, interpolated over ``cleaned_data``
     - :ref:`topics-forms-actions-success`
   * - :class:`~django.contrib.auth.mixins.LoginRequiredMixin`
     - ``Meta.login_required = True``
     - :ref:`topics-forms-actions-guards`
   * - :class:`~django.contrib.auth.mixins.PermissionRequiredMixin` with ``permission_required``
     - ``Meta.permission_required``, a string or an iterable of strings
     - :ref:`topics-forms-actions-guards`
   * - :class:`~django.contrib.auth.mixins.UserPassesTestMixin` with ``test_func``
     - ``check_permissions``, a dependency-injected classmethod evaluated per request
     - :ref:`topics-forms-actions-dynamic-guards`
   * - ``get_form_kwargs``
     - A ``form_class`` factory returning a ``(FormClass, init_kwargs)`` tuple
     - *Dynamic form classes* in :doc:`actions`
   * - ``get_initial``
     - ``get_initial``, a classmethod with the same dependency-injected signature
     - *Handling submissions* above

Two differences are worth stating rather than inferring.
The ``Meta`` guard keys are static and freeze at import time, while ``check_permissions`` and its object-level sibling ``has_object_permission`` run per request and may read the database.
The guards also protect the POST endpoint rather than the page, so rendering a guarded form on a public page is allowed and hiding it in the template is the author's job.

Shared dependency cache
-----------------------

``get_initial``, the handler, and the re-render share one per-request dependency cache.
An expensive provider such as a tenant lookup or a permission check runs once per request, even when validation fails and the page re-renders.
See :doc:`validation-rerender` for the cache mechanics and the access path.

Form-less actions
-----------------

Use ``@action`` to register a plain function when no form fields are needed.
A logout button or a delete confirmation is a typical case.
The name is optional.
A bare ``@action`` registers the function under its own name.

.. code-block:: python
   :caption: page.py

   from django.http import HttpRequest
   from next import action
   from next.forms import redirect_to_origin
   from next.urls import DUrl

   @action("delete_article")
   def delete_article(article_id: DUrl["id", int], request: HttpRequest):
       Article.objects.filter(pk=article_id).delete()
       return redirect_to_origin(request)

The template tag works the same way, but ``form`` is ``None`` inside the block because there is no form class.

Template usage
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

See also
--------

.. seealso::

   :doc:`actions` for auto-registration details, name derivation, and system checks.
   :doc:`templates` for the ``{% form %}`` tag.
   :doc:`validation-rerender` for the re-render pipeline.
   :doc:`backends` for swapping the dispatch backend.
