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

The conversion collapses consecutive uppercase runs, so an acronym stays a single word.
``HTTPLoginForm`` becomes ``http_login_form`` and ``HTMLForm`` becomes ``html_form``.

.. warning::

   Renaming a form class changes its action name.
   Any ``{% form "old_name" %}`` tag or reverse URL that used the old name will fail at render time with ``FormActionNotFound``.
   The exception message lists the closest registered names, so a rename typo is usually visible in the error itself.
   Update every template and reverse call when renaming a class.

Anchor Files and Scope
----------------------

The scope of a form class depends on the file it is declared in.

Page scope.
   A class declared in ``page.py`` or ``component.py`` receives ``page`` scope.
   The framework keys it to the absolute path of that file.
   A page-scoped name is local to its directory, the way a name is local to a Python module.
   Two pages may each declare a ``NoteForm`` with no coordination, and a form opts into a project-wide name by moving to a shared file.

Shared scope.
   A class declared in any other file receives ``shared`` scope.
   The framework keys it to the dotted module name.
   The name is reachable project-wide.

Override with ``Meta.scope``.
   Set ``class Meta: scope = "page"`` or ``class Meta: scope = "shared"`` to pin the scope explicitly, regardless of file name.
   Any other value triggers ``next.E047`` and the class is not registered.

Customise the set of anchor file names through ``NEXT_FRAMEWORK["FORM_ANCHOR_FILES"]``.
The default set is ``["page.py", "component.py"]``.

.. _topics-forms-actions-uid:

UID Stability
-------------

Each action gets a stable URL at ``/_next/form/<uid>/``.
The UID is the first 16 hex characters of ``SHA-256("next:form:{scope_key}:{name}")``, where ``name`` is the derived action name and ``scope_key`` depends on the scope.

Page scope.
   ``scope_key`` is the absolute filesystem path of the declaring ``page.py`` or ``component.py``.
   The UID is therefore stable only as long as the file stays where it is.

Shared scope.
   ``scope_key`` is the dotted module name, walked up from the file while an ``__init__.py`` exists.
   The UID is stable as long as the module path stays the same.

This has one practical consequence.

.. warning::

   Moving a page-scoped form's file or renaming its class changes the UID, and so changes the POST URL.
   A bookmarked or cached ``/_next/form/<uid>/`` URL from the old location stops resolving.
   The same holds for a shared form when its module moves.
   Treat a file move or a class rename as a URL change and expect old action URLs to 404.

The UID is derived, never stored in a template by hand.
A ``{% form "name" %}`` tag reverses the current UID at render time, so a freshly rendered page always posts to the right URL.
Only out-of-band references to a stale UID break.

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

The default implementation on ``BaseForm`` redirects to ``Meta.success_url`` when declared, otherwise it returns ``redirect_to_origin(request)``.
The default implementation on ``BaseModelForm`` calls ``self.save()`` then follows the same redirect rule.
See `Success Feedback`_ for the ``success_url`` contract.

The return value follows the same contract as a handler function, checked in a fixed order.
An ``HttpResponse`` instance passes through unchanged, and this check runs first, so every rich response type the framework ships subclasses ``HttpResponse``.
A string becomes the body of an HTTP 200 response, never a redirect target.
``None`` triggers a re-render of the origin page with HTTP 200.
A model instance with a ``get_absolute_url`` method redirects to that URL, the ``CreateView``-style idiom for a handler that saves and shows the result.
Any other object with a truthy ``url`` attribute redirects to that URL, a last-resort convenience for model-like objects.
Any other return value emits a ``RuntimeWarning`` and is treated as ``None``.

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

You never call ``get_initial`` yourself.
The dispatcher calls it through the dependency injector before the initial render.
``request`` is supplied by the framework.
A parameter whose name matches a captured URL segment is filled from the URL, so ``note_id`` above receives the ``note_id`` route kwarg.
Any other parameter resolves through a registered provider, the same as on a handler.
The base signatures carry no positional arguments of their own: ``BaseForm.get_initial(cls)`` takes none, and ``BaseModelForm.get_initial(cls, **url_kwargs)`` accepts the URL kwargs as keywords.
Declare only the parameters an override actually reads.

Form-Less Actions
-----------------

Use ``@action`` to register a plain callable when no form fields are needed.
Typical use cases include logout buttons, delete confirmations, and any simple POST with no user input.
The name is optional.
A bare ``@action`` or an empty ``@action()`` registers the function under its own name, and ``@action("custom_name")`` overrides it.

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
Pass ``scope="page"`` or ``scope="shared"`` to override the file-derived scope, the same override ``Meta.scope`` provides for a form class.
Any other value triggers ``next.E047`` and the action is not registered.
The ``login_required`` and ``permission_required`` keywords guard the endpoint, see `Access Guards`_.

Applying ``@action`` to a class registers no action: the decorator records the misuse, returns the class unchanged, and ``manage.py check`` reports it as ``next.E053``.
Form classes register through ``__init_subclass__`` and must not use ``@action``.

Injecting the Form Into a Handler
---------------------------------

A handler registered with ``@action("name", form_class=...)`` receives the bound, validated form.
A parameter named ``form`` resolves to it, untyped.
Annotate the parameter with ``DForm[FormClass]`` to type the form for editors and type checkers.

``form_class=`` accepts the form class directly when that class does not register an endpoint of its own.
A ``next.forms`` base marked ``Meta.abstract = True`` is the canonical case: it skips auto-registration yet keeps the ``get_initial`` classmethod the dispatcher calls.
Passing a class that already registered itself raises ``TypeError`` at decoration time.
Mark such a class abstract, or move the handler logic into its ``on_valid``.

.. code-block:: python
   :caption: page.py

   import next.forms
   from django.shortcuts import redirect
   from next.forms import action
   from next.forms.markers import DForm

   class ContactForm(next.forms.ModelForm):
       class Meta:
           model = Contact
           fields = ["name", "email"]
           abstract = True

   @action("create_contact", form_class=ContactForm)
   def create_contact(form: DForm[ContactForm]):
       form.save()
       return redirect("/contacts/")

A handler that returns a bare string sends it as the response body, so a redirect must come back as a response object.
The marker only types the parameter.
The framework still injects the same bound form a parameter named ``form`` would receive.
See :doc:`/content/ref/decorators` for ``DForm`` and ``FormProvider``.

Dynamic Form Classes
--------------------

A ``form_class=`` argument may be a factory callable instead of a form class.
The factory is dependency-injected, so it can read ``request`` and URL kwargs, and it returns one of two shapes.

A plain form class.
   The dispatcher then calls ``get_initial`` on it and binds the form as usual.

A ``(FormClass, init_kwargs)`` tuple.
   The dispatcher passes ``**init_kwargs`` straight to the form constructor and skips ``get_initial`` entirely.
   Use this when the constructor needs arguments that ``get_initial`` cannot supply, such as a preloaded model ``instance`` or a formset ``queryset``.

.. code-block:: python
   :caption: page.py — a factory returning the tuple form

   from django.shortcuts import get_object_or_404, redirect
   from next.forms import action
   from next.urls import DUrl

   def edit_form_factory(note_id: DUrl["id", int]) -> tuple:
       note = get_object_or_404(Note, pk=note_id)
       return NoteForm, {"instance": note}

   @action("edit_note", form_class=edit_form_factory)
   def edit_note(form: NoteForm):
       form.save()
       return redirect("/notes/")

The tuple path bypasses ``get_initial``, so do not rely on ``get_initial`` running when a factory returns the tuple form.
See :doc:`formsets` for the same pattern applied to formset and inline-formset actions.

.. _topics-forms-actions-abstract:

Preventing Registration
-----------------------

A project base class that other forms subclass should not register as an action of its own.
Set ``Meta.abstract = True`` to skip auto-registration.

.. code-block:: python
   :caption: app/forms.py — a shared base that does not register

   class TenantForm(next.forms.Form):
       class Meta:
           abstract = True

       def on_valid(self, request):
           return redirect_to_origin(request)

   class InviteForm(TenantForm):
       email = next.forms.EmailField()

``TenantForm`` is skipped at the ``__init_subclass__`` hook and never appears in the registry.
``InviteForm`` registers normally as ``invite_form`` and inherits the base behaviour.
The same ``Meta.abstract`` flag works on a ``FormWizard`` base class.

.. _topics-forms-actions-guards:

Access Guards
-------------

The dispatch endpoint of an action lives at ``/_next/form/<uid>/``, outside the page URL space, so page-level protection does not cover it.
Declare the access requirements on the action itself.
``Meta.login_required`` and ``Meta.permission_required`` guard a class-bound form, and the same names are keyword arguments on ``@action``.

.. code-block:: python
   :caption: page.py

   import next.forms
   from django.http import HttpRequest
   from next.forms import action, redirect_to_origin
   from next.urls import DUrl

   class NoteDeleteForm(next.forms.Form):
       class Meta:
           login_required = True
           permission_required = "notes.delete_note"

   @action("purge_note", login_required=True)
   def purge_note(note_id: DUrl["id", int], request: HttpRequest):
       Note.objects.filter(pk=note_id).delete()
       return redirect_to_origin(request)

The semantics mirror Django's :class:`~django.contrib.auth.mixins.PermissionRequiredMixin`.
``permission_required`` accepts a single permission string or an iterable of them, the user must hold every listed permission, and declaring a permission implicitly requires authentication.
The guard runs before the form is built, ahead of ``get_initial``, form binding, and any database access.
An anonymous user is redirected to ``LOGIN_URL`` with ``next`` set to the posted origin page.
An authenticated user missing a permission gets :exc:`~django.core.exceptions.PermissionDenied`, which Django renders as HTTP 403.

Unlike ``Meta.abstract``, which is own-class-only, the guard keys are inherited: a base class with ``login_required = True`` protects every concrete subclass, the same way Django's auth mixins protect CBV subclasses.
The same ``Meta`` keys work on a ``FormWizard``, where the guard is enforced on every step submission.

Rendering a guarded form on a public page is not blocked.
The guard protects the mutation, not the markup, exactly as a rendered Django form knows nothing about authorisation.
Hide the form in the template when the page should not show it to anonymous visitors.

The guard is stored as an ``ActionGuard`` on the action's registry metadata, so a custom backend that delegates to the standard pipeline inherits the enforcement.
The ``next.W060`` check warns when ``permission_required`` is declared while ``django.contrib.auth`` is not installed.

.. _topics-forms-actions-success:

Success Feedback
----------------

Two ``Meta`` keys describe what a valid submission tells the user: a flash message and a redirect target.

Success Messages
~~~~~~~~~~~~~~~~

``Meta.success_message`` flashes a message through :doc:`django.contrib.messages <django:ref/contrib/messages>` after a valid submission.
The template is interpolated with ``%`` formatting over ``cleaned_data``, the exact contract of Django's ``SuccessMessageMixin``.

.. code-block:: python

   class NoteForm(next.forms.ModelForm):
       class Meta:
           model = Note
           fields = ["title", "body"]
           success_message = "Note %(title)s saved."

Override ``get_success_message(cleaned_data)`` for a dynamic message, for example to name attributes of the saved instance on a ``ModelForm``.
An empty return value sends nothing.

The message is sent only when the action outcome shapes into a response with a status below 400, so a failed validation flashes nothing.
On a ``FormWizard`` the message is sent once, after ``done`` succeeds, interpolated over the merged step data.

The messages framework must be fully installed: ``django.contrib.messages`` in ``INSTALLED_APPS`` and ``MessageMiddleware`` in ``MIDDLEWARE``.
Without it a valid submission raises ``MessageFailure`` rather than silently dropping the message, and the ``next.W061`` check reports the gap at ``manage.py check`` time.

A handler-only action has no ``cleaned_data`` to interpolate, so ``@action`` takes no ``success_message`` keyword.
Call ``messages.success`` in the handler body instead.

.. code-block:: python

   from django.contrib import messages
   from django.http import HttpRequest
   from next.forms import action, redirect_to_origin
   from next.urls import DUrl

   @action("archive_note")
   def archive_note(note_id: DUrl["id", int], request: HttpRequest):
       Note.objects.filter(pk=note_id).update(archived=True)
       messages.success(request, "Note archived.")
       return redirect_to_origin(request)

Success Redirects
~~~~~~~~~~~~~~~~~

``Meta.success_url`` names where the default ``on_valid`` redirects after a valid submission.
An explicit ``success_url`` wins over the ``redirect_to_origin`` default.
The value is a path string, a lazy object, or a zero-argument callable returning the path, evaluated when the response is built.
:func:`next.urls.page_reverse_lazy` is the lazy companion of ``page_reverse`` for exactly this position, because ``Meta`` evaluates at class definition, before the URLconf is ready.

.. code-block:: python

   from next.urls import page_reverse_lazy

   class AttachmentForm(next.forms.ModelForm):
       class Meta:
           model = Attachment
           fields = ("title", "file")
           success_url = page_reverse_lazy("attachments")

On a ``ModelForm`` the default ``on_valid`` saves first, then follows ``success_url``.
A custom ``on_valid`` or handler that saves an instance can lean on the model itself: returning the instance redirects to its ``get_absolute_url()``, the ``CreateView`` idiom.

.. code-block:: python

   def on_valid(self, request: HttpRequest):
       return self.save()

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
   A form class ``Meta.scope`` or an ``@action`` ``scope`` keyword is set to a value other than ``"page"`` or ``"shared"``.
   The class or action is not registered.

``next.E048``
   ``Meta.instance_from_url`` references a field name that does not exist on the model.

``next.E049``
   ``Meta.instance_from_url`` is set on a class that does not subclass ``next.forms.ModelForm``.

``next.E052``
   ``FORM_ANCHOR_FILES`` is not None or a list, tuple, or set of strings.

``next.E053``
   ``@action`` was applied to a class.
   Remove the decorator and let the class register through ``__init_subclass__``.

``next.W060`` (Warning)
   An action declares ``permission_required`` while ``django.contrib.auth`` is not in ``INSTALLED_APPS``, so the permission check cannot resolve users or permissions.

``next.W061`` (Warning)
   An action declares ``Meta.success_message`` while the messages framework is not fully installed.
   A valid submission raises ``MessageFailure`` until ``django.contrib.messages`` and ``MessageMiddleware`` are both configured.

A UID hash collision between two distinct registrations is not reported as a system check.
It raises ``ImproperlyConfigured`` at import time when two different ``(scope_key, name)`` pairs hash to the same UID.
This is distinct from the ``next.E041`` name collision above, which fires when one name is registered from two different handlers.

See Also
--------

.. seealso::

   :doc:`overview` for the mental model.
   :doc:`templates` for the ``{% form %}`` tag.
   :doc:`validation-rerender` for what happens on a failing submission.
   :doc:`/content/ref/forms` for the public API.
