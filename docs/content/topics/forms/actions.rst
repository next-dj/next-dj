.. _topics-forms-actions:

Actions
=======

An action is a registered entry point for a form POST.
Either a form class or a plain function can act as an action.
Form classes register automatically through ``__init_subclass__``.
Plain functions register through the ``@action`` decorator.

.. contents::
   :local:
   :depth: 2

Class-Bound Forms
-----------------

Declaring a subclass of ``next.forms.Form`` or ``next.forms.ModelForm`` registers the class automatically.
No decorator is needed.

.. code-block:: python
   :caption: notes/pages/note/page.py

   import next.forms
   from next.forms import redirect_to_origin

   class NoteForm(next.forms.ModelForm):
       class Meta:
           model = Note
           fields = ["title", "body"]

       def on_valid(self, request):
           self.save()
           return redirect_to_origin(request)

The framework derives the action name from the class name and infers the scope from the file where the class is declared.

Name Derivation
---------------

The action name is the ``CamelCase`` class name converted to ``snake_case`` by inserting underscores before each uppercase letter that is preceded by a lowercase letter.

.. list-table::
   :header-rows: 1

   * - Class name
     - Action name
   * - ``NoteForm``
     - ``note_form``
   * - ``ArticleEditForm``
     - ``article_edit_form``
   * - ``ContactForm``
     - ``contact_form``
   * - ``Form``
     - ``form``

The conversion is mechanical and does not special-case acronyms.
``HTMLForm`` becomes ``h_t_m_l_form``, not ``html_form``.
Prefer class names where every acronym is written in title case: ``HtmlForm`` → ``html_form``.

.. warning::

   Renaming a form class changes its action name.
   Any ``{% form "old_name" %}`` tag or reverse URL that used the old name will fail at render time with an unknown-action error.
   Update every template and reverse call when renaming a class.

Anchor Files and Scope
----------------------

The scope of a form class depends on the file it is declared in.

Page scope.
   A class declared in ``page.py`` or ``component.py`` receives ``page`` scope.
   The framework keys it to the absolute path of that file.
   Two pages may each declare a ``NoteForm`` without collision.

Shared scope.
   A class declared in any other file receives ``shared`` scope.
   The framework keys it to the dotted module name.
   The name is reachable project-wide.

Override with ``Meta.scope``.
   Set ``class Meta: scope = "page"`` or ``class Meta: scope = "shared"`` to pin the scope explicitly, regardless of file name.
   Any other value triggers ``next.E047`` and the class is not registered.

Customise the set of anchor file names through ``NEXT_FRAMEWORK["FORM_ANCHOR_FILES"]``.
The default set is ``["page.py", "component.py"]``.

The ``on_valid`` Method
-----------------------

``on_valid`` runs after the framework validates the submitted form.
The method signature uses the same dependency-injection rules as any other DI-resolved callable.

.. code-block:: python

   def on_valid(self, request):
       ...

``self`` is the bound, validated form instance.
``request`` is the current ``HttpRequest``.
Any additional parameter is resolved through the DI injector: ``DUrl[...]`` markers, ``Depends`` providers, and so on.

The default implementation on ``BaseForm`` returns ``redirect_to_origin(request)``.
The default implementation on ``BaseModelForm`` calls ``self.save()`` then returns ``redirect_to_origin(request)``.

The return value follows the same contract as a handler function: an ``HttpResponse`` subclass, a string, an object with a ``url`` attribute, or ``None``.
``None`` triggers a re-render of the origin page with HTTP 200.

``get_initial`` Pre-Populates the Form
--------------------------------------

Override ``get_initial`` as a classmethod to provide initial data before the first render.
The classmethod is DI-resolved, so it can receive ``request``, URL parameters, or providers.

.. code-block:: python

   @classmethod
   def get_initial(cls, request, note_id: int | None = None):
       if note_id is None:
           return {}
       return Note.objects.get(pk=note_id)

``BaseModelForm.get_initial`` may return a model instance.
The framework uses it as the ``instance`` kwarg when constructing the form.
``BaseForm.get_initial`` must return a dict.

Form-Less Actions
-----------------

Use ``@action("name")`` to register a plain callable when no form fields are needed.
Typical use cases include logout buttons, delete confirmations, and any simple POST with no user input.

.. code-block:: python
   :caption: page.py

   from django.http import HttpRequest
   from next.forms import action, redirect_to_origin
   from next.urls import DUrl

   @action("delete_note")
   def delete_note(note_id: DUrl["id", int], request: HttpRequest):
       Note.objects.filter(pk=note_id).delete()
       return redirect_to_origin(request)

The scope of a form-less action follows the same anchor-file rule: ``page.py`` and ``component.py`` produce page-scoped actions.
All other files produce shared actions.

Applying ``@action`` to a class raises ``TypeError`` immediately and triggers the ``next.E053`` system check.
Form classes register through ``__init_subclass__`` and must not use ``@action``.

System Checks
-------------

The forms subsystem contributes Django system checks that run through ``python manage.py check``.

``next.E041``
   Two or more registrations share the same action name but come from different handlers.
   Rename one of them or move one to a different scope.

``next.W046`` (Warning)
   A form class was declared in a file outside ``BASE_DIR``.
   The class is not registered automatically.

``next.E047``
   A form class has ``Meta.scope`` set to a value other than ``"page"`` or ``"shared"``.
   The class is not registered.

``next.E048``
   ``Meta.instance_from_url`` references a field name that does not exist on the model.

``next.E049``
   ``Meta.instance_from_url`` is set on a class that does not subclass ``next.forms.ModelForm``.

``next.E053``
   ``@action`` was applied to a class.
   Remove the decorator and let the class register through ``__init_subclass__``.

A UID hash collision between two distinct action names is not reported as a system check.
It raises ``ImproperlyConfigured`` at import time.

See Also
--------

.. seealso::

   :doc:`overview` for the mental model.
   :doc:`templates` for the ``{% form %}`` tag.
   :doc:`validation-rerender` for what happens on a failing submission.
   :doc:`/content/ref/forms` for the public API.
