.. _topics-permissions:

Permissions
===========

next.dj checks object permissions in Django's own vocabulary.
A permission is a string ``app_label.codename``, code asks ``user.has_perm(perm, obj)``, and the backends listed in ``AUTHENTICATION_BACKENDS`` answer.
An application declares a predicate per permission in its ``permissions.py``, and ``ObjectPermissionBackend`` answers ``has_perm`` from those predicates.
The ``Meta.permission_required`` guard of a form and the ``{% has_perm %}`` tag ask the same question, so one predicate decides a submission, a link, and a button.

.. contents::
   :local:
   :depth: 2

The vocabulary
--------------

Django creates the ``add``, ``change``, ``delete``, and ``view`` permissions of every installed model, plus the codenames a model lists in ``Meta.permissions``.
:class:`~django.contrib.auth.backends.ModelBackend` answers them from the database, and a template reads them through ``{% if perms.notes.change_note %}``.
``ModelBackend`` holds no permission on a single row, so it answers ``False`` to every question that names an object.
A question about one row therefore needs another backend, and ``ObjectPermissionBackend`` is that backend.

:meth:`~django.contrib.auth.models.User.has_perm` asks every listed backend in turn and grants the permission once one of them does.
An active superuser holds every permission before any backend is asked, so a superuser passes every predicate and a ``~`` rule cannot deny one.
An inactive user and an anonymous user hold nothing, the rule ``ModelBackend`` follows, so a predicate never sees either of them.
See :ref:`django:topic-authorization` and :ref:`django:authorization_methods` for the Django side.

permissions.py
--------------

The ``permissions.py`` of an application binds each permission name to a predicate.

.. code-block:: python
   :caption: notes/permissions.py

   from next.permissions import add_perm, predicate

   @predicate
   def is_owner(user, note):
       return note is not None and note.owner_id == user.pk

   @predicate
   def is_editor(user):
       return user.groups.filter(name="editors").exists()

   add_perm("notes.edit_note", is_owner | is_editor)
   add_perm("notes.delete_note", is_owner)

``@predicate`` turns a callable into a ``Predicate``.
The callable declares one of three signatures, and the predicate passes it exactly the arguments it declares.

.. code-block:: python
   :caption: notes/permissions.py

   from django.conf import settings

   from next.permissions import predicate

   @predicate
   def notes_open():
       return settings.NOTES_OPEN

   @predicate
   def is_staff(user):
       return user.is_staff

   @predicate
   def is_author(user, note):
       return note is not None and note.author_id == user.pk

``()`` answers without the user, such as a site-wide switch.
``(user)`` always receives the user and never the object, so it answers the same for every row.
``(user, obj)`` receives the user and the row the question names, ``None`` on the model-level question that ``user.has_perm(perm)`` and ``{% if perms.notes.edit_note %}`` ask.
A callable takes at most two positional parameters, and a parameter with a default counts toward the two, so ``(user, obj=None)`` is the two-argument form.
``**kwargs`` is allowed, while ``*args`` and a keyword-only parameter without a default are not.
``@predicate`` counts the parameters once, when it wraps the callable, so a call costs no signature inspection.
``Predicate`` checks its own shape when it is built and raises ``ValueError`` for a leaf or a composite it cannot evaluate.

``is_owner`` answers ``False`` on the model-level question, since no row has an owner yet, while ``is_editor`` answers it from the user alone.

Every predicate that takes ``obj`` accepts ``None`` for it and answers the model-level question, because Django asks it far more often than a page does.
``user.has_perm(perm)``, ``{% if perms.notes.edit_note %}``, ``{% if perms.notes %}``, and ``user.get_all_permissions()`` all call the predicates with ``None``.
``user.has_module_perms(app_label)`` does too, and the admin index calls it for every application.
A predicate that reads an attribute of ``obj`` without the ``None`` check raises there, which turns the admin index into a server error for every staff user.

Predicates compose with ``&``, ``|``, and ``~``, whatever the signature of each operand.
A composite passes each operand the arguments that operand declares and evaluates its operands left to right and stops once the answer is known, so ``is_owner | is_editor`` runs no group query for the owner of the note.
Both operands of ``&`` and ``|`` are predicates, so a plain function joins a composition through ``@predicate`` first.

A predicate that raises :exc:`~django.core.exceptions.PermissionDenied` makes ``user.has_perm()`` and ``user.has_module_perms()`` answer ``False`` and stops the backends listed after it, which is Django's rule for every backend.
``user.get_all_permissions()`` catches nothing, so the exception leaves that call.

``add_perm(name, predicate)`` registers the predicate under the permission name and returns it as a ``Predicate``, and it accepts a plain callable, which it wraps the way ``@predicate`` does.
A name outside the ``app_label.codename`` form raises ``ValueError``, since ``user.has_perm()`` looks permissions up by that form.
Registering an equal predicate under a name it already holds changes nothing, and a different predicate under that name raises ``PermissionAlreadyRegisteredError``.
Each new registration sends ``permission_registered`` with ``name`` and ``predicate``, see :doc:`/content/ref/signals`.

A codename no model defines, such as ``edit_note`` beside the model's ``change_note``, leaves the answer to the predicate alone.
A registered name that is also a model permission, such as ``notes.change_note``, is answered by both backends.
``ModelBackend`` answers nothing once an object is given, so on a row the predicate is the only authority, and a grant through a group or a user permission counts only on the model-level question, where its holder passes whatever the predicate says.

The ``next`` application imports the ``permissions.py`` of every installed application at startup, after every ``deps.py`` and before any ``forms.py``.
``PERMISSIONS_AUTODISCOVER`` turns that sweep off, and a project that turns it off imports the modules from ``AppConfig.ready``, see :doc:`/content/ref/settings`.
A callable outside the three signatures still imports, and ``manage.py check`` reports it as ``next.E161`` with the permission it guards and the reason.

A predicate is synchronous and may query the database.
It reads nothing but the user and the object, so a decision that needs the request, the tenant, or a captured URL kwarg belongs to a form hook, see `Forms`_.

The backend
-----------

``ObjectPermissionBackend`` sits next to ``ModelBackend`` in ``AUTHENTICATION_BACKENDS``.

.. code-block:: python
   :caption: settings.py

   AUTHENTICATION_BACKENDS = [
       "django.contrib.auth.backends.ModelBackend",
       "next.permissions.ObjectPermissionBackend",
   ]

The backend authenticates nobody, so the backend before it still logs the user in.
With two entries listed, Django no longer picks the backend for a user that :func:`~django.contrib.auth.authenticate` did not return.
:func:`~django.contrib.auth.login` then raises ``ValueError`` without a ``backend`` argument, which a signup view that logs in the user it has just created runs into, and ``User.objects.with_perm()`` needs ``backend=`` for the same reason.

.. code-block:: python
   :caption: a signup handler

   from django.contrib.auth import login

   def sign_in(request, user):
       login(request, user, backend="django.contrib.auth.backends.ModelBackend")

``has_perm(user, perm, obj)`` runs the predicate of a registered permission and answers ``False`` for any other name.
``get_all_permissions(user, obj)`` returns the registered permissions the user holds on ``obj``, which ``user.get_all_permissions(obj)`` merges with the answers of the other backends.
``has_module_perms(user, app_label)`` answers whether the user holds any registered permission of the application on the model-level question, which ``{% if perms.notes %}`` asks.
``ahas_perm``, ``aget_all_permissions``, and ``ahas_module_perms`` are the async twins Django's async user methods reach, and they run the predicates in a thread, since a predicate may query.

A project that registers a permission while no listed backend is ``ObjectPermissionBackend`` or a subclass of it fails ``manage.py check`` with ``next.E162``, because ``user.has_perm()`` would never consult a predicate.
The hint of the message names the ``backend`` argument ``login()`` needs once the second entry is in.

``next.permissions.has_perm(user, perm, obj)`` and ``next.permissions.perms_for(user, obj)`` answer from the registered predicates alone.
They ask no other backend and grant a superuser nothing a predicate refuses, so they fit a test of the predicates themselves, while application code asks ``user.has_perm()``.

The has_perm tag
----------------

``{% has_perm %}`` renders its first branch for a user who holds the permission and the optional ``{% else %}`` branch for everyone else.
It is a builtin, so a template uses it without ``{% load %}``.

.. code-block:: jinja
   :caption: notes/pages/notes/[slug]/template.djx

   <h1>{{ note.title }}</h1>
   {% has_perm "notes.edit_note" note %}
     <a href="edit/">Edit</a>
   {% else %}
     <p>Read only.</p>
   {% endhas_perm %}

The tag reads the user off the request of the render, so it answers the same way in a full page render, a component render, and a zone render.
With an object it asks ``user.has_perm(perm, obj)``.
An object that resolves to ``None`` or to nothing renders the ``else`` branch without asking, so a missing row never turns into the model-level question.
Without an object it asks ``user.has_perm(perm)``, which model permissions answer as well as predicates.
The permission is a quoted name or a variable that resolves to one, and a variable that resolves to nothing renders the ``else`` branch.
``manage.py check`` warns with ``next.W130`` about a literal name that no installed model defines and no ``add_perm()`` registers, in a page template or a component template, since such a block renders its ``else`` branch for every user but a superuser.

A ``{% zone %}`` inside a ``{% has_perm %}`` block fails ``manage.py check`` with ``next.E163``.
A standalone zone render does not check the enclosing block again, so the zone would render for users the block refuses.
The check reports a zone at any depth under the block in the composed page, one nested in another zone included.
It also reports a zone anywhere in the body of a component, any slot or the children, whose template reaches a ``{% has_perm %}``.
The analysis follows the template through nested components, a literal ``{% extends %}``, and a literal ``{% include %}``.
A reference it cannot follow counts as a ``{% has_perm %}``, such as a component that does not resolve, a template name held in a variable, a template that does not exist, or one that does not compile.
The rule refuses more than it has to on purpose, so a zone in a component that never guards that slot is still reported, and the message names the component.

The runtime refuses every zone the check reports as it refuses a zone the page does not declare, since both read the same analysis, computed once per composed page template.
A standalone zone request for it answers 400 ``unknown zone``, a form re-render that names it falls back to an extract-morph of a full render, and ``Patches.morph_zone`` or any other server-side render of it raises ``UnknownZoneError``.
The runtime refuses a zone under an ``{% if %}`` or a ``{% for %}`` of the composed page the same way, which ``next.E063`` and ``next.E062`` report.
With ``DEBUG`` on, an edit to a component template, an extended template, or an included template makes the next request analyse the zones again.

A component whose Python ``render()`` decides what it shows is not analysed, because a condition written in Python is like an ``{% if %}`` the analysis cannot read.
An ``{% if %}`` or a ``{% for %}`` that a component's own template wraps around its slot or children is not analysed either, since the component analysis looks only for ``{% has_perm %}`` and unfollowable references and ``next.E062`` and ``next.E063`` read the composed page alone, so a zone in such a body renders on its own without that condition.
Keep the permission decision for zone content in a ``{% has_perm %}`` inside the zone, and every render of the zone checks the permission for the user it renders for.

.. code-block:: jinja
   :caption: notes/pages/notes/[slug]/template.djx

   {% zone "toolbar" %}
     {% has_perm "notes.edit_note" note %}
       <a href="edit/">Edit</a>
     {% endhas_perm %}
   {% endzone %}

Forms
-----

``Meta.permission_required`` names permissions the way :class:`~django.contrib.auth.mixins.PermissionRequiredMixin` does, and a registered permission is checked on the row the form edits.

.. code-block:: python
   :caption: notes/pages/notes/[int:note]/edit/page.py

   from typing import Any

   from notes.models import Note

   import next.forms
   from next.deps import DObject

   class NoteEditForm(next.forms.ModelForm):
       class Meta:
           model = Note
           fields = ["title", "body"]
           permission_required = "notes.edit_note"

       @classmethod
       def get_form_kwargs(cls, note: DObject[Note]) -> dict[str, Any]:
           return {"instance": note}

The static guard runs first, so an anonymous visitor is redirected to ``LOGIN_URL`` and an unregistered permission is checked before the form binds, as it is for every model permission.
A registered permission waits for the bound form and is checked once, as ``user.has_perm(perm, form.instance)`` for a ``ModelForm`` that edits a saved row.
A ``ModelForm`` that creates a record and a plain ``Form`` pass ``None``, the model-level question.

The permission runs only once the row has loaded, so the way the form loads it decides what a refused user learns.
``DObject`` in ``get_form_kwargs`` reads the row through ``visible_to(scope)``, so a row outside the scope answers 404 before any permission runs, exactly like a missing one, see :ref:`topics-di-dobject`.
``Meta.instance_from_url`` loads through an unscoped :func:`~django.shortcuts.get_object_or_404` on the model instead, so a refused user gets 403 for an id that exists and 404 for one that does not, which lets them probe which ids exist.
An overridden ``has_object_permission`` runs afterwards in addition, so it stays the place for a check that reads the request, the tenant, or a URL kwarg, see :ref:`topics-forms-actions-dynamic-guards`.

A ``FormWizard`` and a handler-only ``@action`` bind no form at the guard, so they check every listed permission before anything binds, registered ones included, with ``obj`` set to ``None``.

A denial on the bound row answers HTTP 403 and sends ``form_access_denied`` with ``layer="object"`` and ``reason="denied"``, see :ref:`topics-forms-signals-form-access-denied`.
``manage.py check`` warns with ``next.W129`` about a ``permission_required`` name that no installed model defines and no ``add_perm()`` registers, since such a guard refuses every user but a superuser.

``next.W129`` and ``next.W130`` read the model permissions off the model classes, the ``default_permissions`` and the ``Meta.permissions`` of every installed model, beside the registered names.
A ``Permission`` row that exists only in the database, created by a data migration or through the admin, is unknown to them and draws the warning.
Declare such a permission in ``Meta.permissions`` of its model, or list the code in ``SILENCED_SYSTEM_CHECKS``.

django-rules compatibility
--------------------------

next.dj does not depend on django-rules, and the two sit side by side.
Both backends can be listed in ``AUTHENTICATION_BACKENDS``, and ``user.has_perm()`` asks each of them, so a permission declared in either one answers the same call.
``rules.py`` belongs to django-rules and ``permissions.py`` to next.dj, and a project can keep both.
Only a name registered with ``next.permissions`` waits for the bound form, so a django-rules permission named in ``Meta.permission_required`` is checked by the static guard before the form binds, with ``obj`` set to ``None``.
When ``rules.permissions.ObjectPermissionBackend`` is listed, ``next.W129`` and ``next.W130`` count a permission django-rules registers as known.
A template that runs ``{% load rules %}`` gets django-rules' own ``has_perm`` tag in that template, which replaces the builtin there.

The tag library of ``{% has_perm %}`` is the module ``next.templatetags.permissions``, so its library name is ``permissions``.
A project application with a ``templatetags/permissions.py`` of its own shares that name, and Django reports the clash as ``templates.W003``, as it does for the other next.dj libraries.

See also
--------

.. seealso::

   :doc:`/content/howto/enforce-object-level-permissions` for an owner-only edit form end to end.
   :doc:`/content/ref/permissions` for the public names.
   :ref:`topics-forms-actions-guards` for the static guard and the dynamic hooks.
   :doc:`/content/ref/system-checks` for ``next.E161``, ``next.E162``, ``next.E163``, ``next.W129``, and ``next.W130``.
