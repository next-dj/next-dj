.. _topics-forms-actions:

Actions
=======

An action is a Python callable registered as a form handler.
The framework assigns it a stable URL, dispatches form submissions to it, and resolves its parameters through the dependency injector.
This page covers every shape of the ``@action`` decorator, the handler signature options, the return type contract, and the patterns for registering actions across applications.

.. contents::
   :local:
   :depth: 2

The Decorator
-------------

``next.forms.action`` registers a callable as a form handler.

.. code-block:: python
   :caption: notes/routes/page.py

   from django.http import HttpResponseRedirect

   from next.forms import action

   from notes.forms import NoteForm


   @action("create_note", form_class=NoteForm)
   def create_note(form: NoteForm) -> HttpResponseRedirect:
       form.save()
       return HttpResponseRedirect("/")

The decorator takes one required positional argument, the action name, and two keyword arguments.

name (required).
   String identifier used in templates and inside the registry.
   Must be unique across the project unless a namespace prefix differentiates duplicates.

form_class (optional).
   Form class to bind and validate the POST body against.
   Omit it for non form POST actions, see the section below.

namespace (optional, keyword only).
   Prefix prepended to the name as ``"<namespace>:<name>"``.
   Useful when several apps share short names such as ``"save"``.

The decorated function joins the form action registry when the module that contains it is imported.
The framework imports ``page.py`` files when resolving URL patterns and imports ``component.py`` files when the components backend initialises, so actions declared in either file are registered before the first request.
Actions in other modules (such as a shared ``actions.py``) register when those modules are first imported. Import them from ``AppConfig.ready`` to guarantee early registration.

Action Names and Namespaces
---------------------------

Names hash into a 16 character UID that becomes the path of the dispatch URL.
Two ``@action`` calls that register the same name from different handlers are reported by the ``next.E041`` system check.

.. code-block:: python
   :caption: collision-free naming

   from next.forms import action


   @action("save", namespace="notes")
   def save_note(form): ...


   @action("save", namespace="comments")
   def save_comment(form): ...

Templates address each action through ``@action="notes:save"`` and ``@action="comments:save"``.

Rename one of the actions to resolve a hash collision when namespaces are not appropriate.

Handler Signature
-----------------

The handler can take any parameters the dependency injector knows how to resolve.

.. code-block:: python
   :caption: typical handlers

   from django.http import HttpRequest, HttpResponseRedirect

   from next.forms import action
   from next.pages import Context
   from next.urls import DUrl


   @action("simple", form_class=NoteForm)
   def simple(form: NoteForm) -> HttpResponseRedirect:
       form.save()
       return HttpResponseRedirect("/")


   @action("with_request", form_class=NoteForm)
   def with_request(form: NoteForm, request: HttpRequest) -> HttpResponseRedirect:
       form.user = request.user
       form.save()
       return HttpResponseRedirect("/")


   @action("with_url", form_class=NoteForm)
   def with_url(form: NoteForm, note_id: DUrl[int]) -> HttpResponseRedirect:
       form.instance = Note.objects.get(pk=note_id)
       form.save()
       return HttpResponseRedirect("/")


   @action("with_context", form_class=NoteForm)
   def with_context(
       form: NoteForm,
       feature_flag: str = Context("feature_flag"),
   ) -> HttpResponseRedirect:
       if feature_flag == "on":
           form.save()
       return HttpResponseRedirect("/")

The injector fills each parameter from the first matching provider.
Omitted parameters are not resolved and not passed to the handler.

Return Types
------------

A handler returns a response that the dispatcher forwards to the client.

HttpResponse subclasses.
   Returned verbatim.
   ``HttpResponseRedirect``, ``JsonResponse``, ``StreamingHttpResponse``, and your own subclasses all work.

String.
   Wrapped in an ``HttpResponse`` by the dispatcher.

Object with a ``url`` attribute.
   Coerced into an ``HttpResponseRedirect`` to that URL.

None.
   The dispatcher returns the rendered origin page.
   Use this when an action only needs a side effect on success and no special redirect.

Actions Without form_class
--------------------------

Drop ``form_class`` to handle non form POST submissions such as confirmation buttons.

.. code-block:: python
   :caption: confirmation action

   from next.forms import action


   @action("delete_note")
   def delete_note(note_id: DUrl[int]) -> HttpResponseRedirect:
       Note.objects.filter(pk=note_id).delete()
       return HttpResponseRedirect("/")

The template still uses ``{% form @action="delete_note" %}``.
The dispatcher posts to the action URL without binding a form, which means the handler signature must not include a ``form`` parameter.

ModelForm Actions
-----------------

Pass a ``ModelForm`` to handle create and edit flows in one decorator.

.. code-block:: python
   :caption: create flow

   from next.forms import action

   from notes.forms import NoteForm


   @action("create_note", form_class=NoteForm)
   def create_note(form: NoteForm) -> HttpResponseRedirect:
       form.save()
       return HttpResponseRedirect("/")

.. code-block:: python
   :caption: edit flow

   from django.shortcuts import get_object_or_404

   from next.forms import action
   from next.urls import DUrl

   from notes.forms import NoteForm
   from notes.models import Note


   @action("update_note", form_class=NoteForm)
   def update_note(form: NoteForm, note_id: DUrl[int]) -> HttpResponseRedirect:
       form.instance = get_object_or_404(Note, pk=note_id)
       form.save()
       return HttpResponseRedirect("/")

See :doc:`modelforms` for ``get_initial`` patterns that preload the instance from the request.

Form Factory Callable
---------------------

The ``form_class`` argument accepts a factory callable in place of a concrete ``Form`` subclass.
The framework resolves the factory through the dependency injector once per request, before binding the POST body.
The factory may return either of two shapes.

A ``Form`` subclass.
   The dispatcher binds POST data and ``get_initial`` exactly as it does for a static ``form_class``.
   Use this when only the choice of class is dynamic.

A ``(FormClass, init_kwargs)`` tuple.
   The dispatcher passes ``**init_kwargs`` straight to the form constructor and skips ``get_initial`` entirely.
   Use this when the form needs constructor arguments that only exist at request time.

The factory is dependency-resolved, so it can declare ``request: HttpRequest``, a ``DUrl[...]`` parameter, or any ``Depends`` provider in its signature.

.. code-block:: python
   :caption: notes/routes/login/page.py

   from typing import Any

   from django.contrib.auth.forms import AuthenticationForm
   from django.http import HttpRequest, HttpResponse

   from next.forms import action


   def login_form_factory(
       request: HttpRequest,
   ) -> tuple[type[AuthenticationForm], dict[str, Any]]:
       return AuthenticationForm, {"request": request}


   @action("login", form_class=login_form_factory)
   def login(request: HttpRequest, form: AuthenticationForm) -> HttpResponse:
       ...

``AuthenticationForm`` requires ``request`` as its first constructor argument and has no ``get_initial`` method.
The tuple shape supplies that argument and bypasses ``get_initial``, so the form binds cleanly.

When the factory returns a bare class instead of a tuple, the dispatcher takes the regular path.
Use that shape to pick a form per request without changing the constructor call.

.. code-block:: python
   :caption: choosing a class per request

   from next.forms import action
   from next.urls import DUrl


   def report_form_factory(kind: DUrl[str]) -> type:
       return WeeklyReportForm if kind == "weekly" else DailyReportForm


   @action("submit_report", form_class=report_form_factory)
   def submit_report(form) -> HttpResponse:
       form.save()
       return HttpResponseRedirect("/reports/")

A factory that returns anything other than a class or a ``(class, dict)`` tuple raises ``TypeError`` at dispatch time.
The template can reference the action through a variable when the name is computed in context.

.. code-block:: jinja
   :caption: action name from context

   {% form @action=action_name %}

Multiple Actions on One Page
----------------------------

A page module can register several actions.
Each lives at its own URL so the dispatcher can tell them apart.

.. code-block:: python
   :caption: notes/routes/notes/[id]/page.py

   from next.forms import action


   @action("update_note", form_class=NoteForm)
   def update_note(form: NoteForm, note_id: DUrl[int]) -> HttpResponseRedirect:
       form.instance = Note.objects.get(pk=note_id)
       form.save()
       return HttpResponseRedirect("/")


   @action("delete_note")
   def delete_note(note_id: DUrl[int]) -> HttpResponseRedirect:
       Note.objects.filter(pk=note_id).delete()
       return HttpResponseRedirect("/")

Templates reference both names.

.. code-block:: jinja
   :caption: notes/routes/notes/[id]/template.djx

   {% form @action="update_note" %}
     {{ form.title }}
     {{ form.body }}
     <button type="submit">Save</button>
   {% endform %}

   {% form @action="delete_note" %}
     <button type="submit" class="danger">Delete</button>
   {% endform %}

Both forms render on a page whose URL captures ``id``.
The ``{% form %}`` tag emits a hidden ``_url_param_id`` field for every captured URL parameter, so each handler resolves ``DUrl[int]`` without any extra tag argument.

Component Actions
-----------------

A composite component can register its own actions.
The action stays valid wherever the component renders.

.. code-block:: python
   :caption: _components/comment_box/component.py

   from next.forms import action


   @action("post_comment", form_class=CommentForm, namespace="comments")
   def post_comment(form: CommentForm) -> HttpResponseRedirect:
       form.save()
       return HttpResponseRedirect(form.cleaned_data["origin"])

Components register their actions when the components backend imports each ``component.py``.

System Checks
-------------

The forms subsystem contributes Django system checks.

- ``next.E041`` reports two ``@action`` registrations that share a name but come from different handlers.
- ``next.E044`` reports a malformed ``DEFAULT_FORM_ACTION_BACKENDS`` entry.
- ``next.E045`` reports a backend that does not subclass ``FormActionBackend``.

Run them through ``uv run python manage.py check``.

See Also
--------

.. seealso::

   :doc:`templates` for the ``{% form %}`` tag.
   :doc:`validation-rerender` for what happens on a failing submission.
   :doc:`/content/howto/handle-file-uploads` for file inputs.
   :doc:`/content/ref/forms` for the public API.
