.. _topics-permissions:

Permissions
===========

next.dj checks object permissions in Django's own vocabulary.
A permission is a string ``app_label.codename``, code asks ``user.has_perm(perm, obj)``, and the backends listed in ``AUTHENTICATION_BACKENDS`` answer.
An application declares a predicate per permission in its ``permissions.py``, and ``ObjectPermissionBackend`` answers ``has_perm`` from those predicates.
The ``Meta.permission_required`` guard of a form and the ``{% has_perm %}`` tag ask the same question, so one predicate decides a submission, a link, and a button.
A ``proxy.py``, a page, and a component declare the login and the permissions they require in the same words, and the framework checks them on the page, its actions, and its zones, see `Access on the page tree`_.

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
An active superuser holds every permission before any backend is asked, so a superuser passes every predicate and no predicate can deny one.
An inactive user and an anonymous user hold nothing, the rule ``ModelBackend`` follows, so a predicate never sees either of them.
See :ref:`django:topic-authorization` and :ref:`django:authorization_methods` for the Django side.

permissions.py
--------------

The ``permissions.py`` of an application binds each permission name to a predicate.

.. code-block:: python
   :caption: notes/permissions.py

   from next.permissions import add_perm

   def is_owner(user, note):
       return note is not None and note.owner_id == user.pk

   def is_editor(user):
       return user.groups.filter(name="editors").exists()

   def can_edit(user, note):
       return is_editor(user) or is_owner(user, note)

   add_perm("notes.edit_note", can_edit)
   add_perm("notes.delete_note", is_owner)

A predicate is a plain function with one of three signatures, and a check passes it exactly the arguments it declares.

.. code-block:: python
   :caption: notes/permissions.py

   from django.conf import settings

   def notes_open():
       return settings.NOTES_OPEN

   def is_staff(user):
       return user.is_staff

   def is_author(user, note):
       return note is not None and note.author_id == user.pk

``()`` answers without the user, such as a site-wide switch.
``(user)`` always receives the user and never the object, so it answers the same for every row.
``(user, obj)`` receives the user and the row the question names, ``None`` on the model-level question that ``user.has_perm(perm)`` and ``{% if perms.notes.edit_note %}`` ask.
A callable takes at most two positional parameters, and a parameter with a default counts toward the two, so ``(user, obj=None)`` is the two-argument form.
``**kwargs`` is allowed, while ``*args`` and a keyword-only parameter without a default are not.
``add_perm`` counts the parameters once, when it registers the function, so a check costs no signature inspection.

``is_owner`` answers ``False`` on the model-level question, since no row has an owner yet, while ``is_editor`` answers it from the user alone.

Every predicate that takes ``obj`` accepts ``None`` for it and answers the model-level question, because Django asks it far more often than a page does.
``user.has_perm(perm)``, ``{% if perms.notes.edit_note %}``, ``{% if perms.notes %}``, and ``user.get_all_permissions()`` all call the predicates with ``None``.
``user.has_module_perms(app_label)`` does too, and the admin index calls it for every application.
A predicate that reads an attribute of ``obj`` without the ``None`` check raises there, which turns the admin index into a server error for every staff user.

A rule over several conditions combines them with ``and``, ``or``, and ``not`` in the body of one predicate, as ``can_edit`` does above by calling the two others as the plain functions they are.
Python stops once the answer is known, so the order of the operands decides which query runs first.

A predicate that raises :exc:`~django.core.exceptions.PermissionDenied` makes ``user.has_perm()`` and ``user.has_module_perms()`` answer ``False`` and stops the backends listed after it, which is Django's rule for every backend.
``user.get_all_permissions()`` catches nothing, so the exception leaves that call.

``add_perm(name, predicate)`` registers the function under the permission name.
A name outside the ``app_label.codename`` form raises ``ValueError``, since ``user.has_perm()`` looks permissions up by that form.
Registering the same function under a name it already holds changes nothing, and a different predicate under that name raises ``PermissionAlreadyRegisteredError``.

A codename no model defines, such as ``edit_note`` beside the model's ``change_note``, leaves the answer to the predicate alone.
A registered name that is also a model permission, such as ``notes.change_note``, is answered by both backends.
``ModelBackend`` answers nothing once an object is given, so on a row the predicate is the only authority, and a grant through a group or a user permission counts only on the model-level question, where its holder passes whatever the predicate says.

The ``next`` application imports the ``permissions.py`` of every installed application at startup, after every ``deps.py`` and before any ``forms.py``, the way Django's admin imports every ``admin.py``.
A callable outside the three signatures still imports, and ``manage.py check`` reports it as ``next.E159`` with the permission it guards and the reason.

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

A project that registers a permission while no listed backend is ``ObjectPermissionBackend`` or a subclass of it fails ``manage.py check`` with ``next.E160``, because ``user.has_perm()`` would never consult a predicate.
The hint of the message names the ``backend`` argument ``login()`` needs once the second entry is in.

``next.permissions.has_perm(user, perm, obj)`` answers from the registered predicates alone.
It asks no other backend and grants a superuser nothing a predicate refuses, so it fits a test of the predicates themselves, while application code asks ``user.has_perm()``.

.. _topics-permissions-tree:

Access on the page tree
-----------------------

A ``proxy.py``, a page, and a component declare who may reach them in the words of :func:`~django.contrib.auth.decorators.login_required` and :class:`~django.contrib.auth.mixins.PermissionRequiredMixin`.
The framework checks the declaration on every request that reaches the endpoint, the page view, an action posted from the page, and a zone request of the page alike.

The declarations
~~~~~~~~~~~~~~~~

Three calls form the whole vocabulary, and each binds to the file that makes it at module level.

- ``login_required()`` requires a logged-in user.
- ``permission_required(perm, obj=None)`` requires every permission in ``perm``, a name or a tuple of names, and with ``obj`` checks them on one row.
- ``public()`` lifts the requirements of every ``proxy.py`` above the file and lets :class:`~django.contrib.auth.middleware.LoginRequiredMiddleware` pass.

``proxy`` from ``next.pages`` declares in a ``proxy.py`` for its directory and every page below it, ``page`` in a ``page.py`` for that page alone, and ``component`` in a ``component.py``, which has no ``public()``.

.. code-block:: python
   :caption: notes/pages/notes/proxy.py

   from next.pages import proxy

   proxy.login_required()

.. code-block:: python
   :caption: notes/pages/notes/[int:note]/edit/page.py

   from notes.models import Note

   from next import context, page
   from next.deps import DObject

   page.permission_required("notes.edit_note", obj=DObject[Note])

   @context("note")
   def note(note: DObject[Note]) -> Note:
       return note

The ``proxy.py`` binds every page under ``notes/``, and the ``page.py`` adds a permission on the note the URL names.
``obj=DObject[Note]`` loads the row by the URL kwarg ``note``, the ``model_name`` of the model, through ``visible_to(scope)``, and the ``note`` context reads the same row from the cache, so the page runs one query, see :ref:`topics-proxy`.
A ``perm`` that is no non-empty name or tuple of names raises ``TypeError`` where the call is written, and so does an ``obj`` of a page or a ``proxy.py`` that is no ``DObject[Model]`` without a parent.
The ``obj`` of a component names a prop or a ``@component.context`` value as a string, since a component has no URL of its own.

How the check runs
~~~~~~~~~~~~~~~~~~

The rule of a page joins the ``proxy.py`` files from the tree root down to the directory of the page and the ``page.py`` itself, each requirement once, in that order, and a ``public()`` on the way lifts what came before it, see :ref:`topics-proxy`.
A rule without requirements passes without reading the user, so an open page costs one memo read.
The check follows the order of ``AccessMixin`` and ``PermissionRequiredMixin``.

1. An anonymous user is redirected to ``LOGIN_URL`` with the full path of the request as ``next``.
2. Every permission without an object is asked as ``user.has_perm(perm)``, and a refusal answers HTTP 403.
3. Every permission with an object loads its row by the URL kwarg and asks ``user.has_perm(perm, row)``, and a refusal answers HTTP 403.

The check runs before ``render()``, the zone branch, and the page context, so a refused request runs no application code but the ``scope`` dependency the third step needs.
A row the scope does not see answers 404 before any permission is asked, so a refused user never tells a hidden row from a missing one, see :ref:`topics-di-dobject`.
A route that does not capture the kwarg of the model is a configuration mistake rather than a refusal, so it raises ``UnboundObjectError``, which ``next.E162`` reports ahead of any request.

Where a rule applies
~~~~~~~~~~~~~~~~~~~~

A rule reaches six places, and each answers a refusal in its own form.

Page.
   The view of a ``page.py`` checks the rule of its chain, as described above.

``proxy.py``.
   A ``proxy.py`` adds its requirements to every page in its directory and below, and through them to the actions and the zone requests of those pages.

Action.
   An action declared in a file of a page tree answers to the rule of the page in the directory of that file, joined with the rule of the posted origin page.
   The tree rule runs before the ``Meta`` guard and the ``render()`` of the origin, an anonymous POST is redirected to ``LOGIN_URL`` with the origin path as ``next``, and a refusal answers 403 and sends ``form_access_denied`` with ``layer="page"``, see :doc:`/content/security/actions`.

Zone request.
   A zone GET travels the URL of its page through the same view, so the rule answers it with the same redirect, 403, or 404 before any zone renders.
   The client runtime reports a 403 or a 404 as ``partial:error`` and then navigates to the page, and it follows a login redirect with a full navigation.

Component.
   A ``component.py`` that declares access renders nothing for a refused request, the way a ``{% has_perm %}`` block without ``else`` does, with no redirect and no 403.
   Its ``obj`` names a prop or a ``@component.context`` value, and the component runs none of its contexts past the one the check reads.

``{% has_perm %}`` block.
   A block in a template guards markup rather than an endpoint, see `The has_perm tag`_.

Rules the core keeps
~~~~~~~~~~~~~~~~~~~~

Inheritance adds until a public() lifts.
   A page joins every requirement of the ``proxy.py`` files above it.
   Only an explicit ``public()`` in a deeper ``proxy.py`` or in the ``page.py`` removes them, ``manage.py permissions`` names the file of every such lift, and a requirement declared below the lift binds again.
   ``public()`` beside a requirement of the same file is ``next.E163``, since the requirement still binds.

Actions and zones pass the access of their page.
   Neither has a rule of its own to forget, so a closed page stays closed for every action declared beside it and for every zone inside it, and an action under a lift is as public as its page.
   A forged origin adds the rule of the page it names and lifts none of the owner's rule.

The decision lives inside the zone.
   A zone under a ``{% has_perm %}`` block or in the body of a component that decides what it shows is refused to a partial request, see `Zones and the decision`_.

``LoginRequiredMiddleware`` works unchanged.
   The view of every page carries the ``login_required`` attribute the middleware reads, ``False`` for a page whose rule is public, whether its own ``page.public()`` or a ``proxy.py`` above made it so.
   The action endpoint, the CSRF endpoint, ``sitemap.xml``, and ``robots.txt`` are marked with :func:`~django.contrib.auth.decorators.login_not_required`, and the dispatcher itself requires a login for an action whose owner or origin declares nothing and is not public, an action outside any page tree included.
   A ``Patches.morph`` onto a foreign page applies the same middleware default to that page.

Mistakes surface at ``manage.py check``.
   ``next.E162`` reports an ``obj=DObject[Model]`` whose kwarg the route of a page under it does not capture, ``next.E163`` a ``public()`` beside a requirement of the same file, and ``next.E164`` a declaration made from a module that is no ``page.py``, ``proxy.py``, or ``component.py``.
   ``next.W127`` warns about a permission nothing defines, ``next.W128`` about a ``proxy.py`` with no page below it, and ``next.W129`` about an action nothing guards, see :doc:`/content/ref/system-checks`.

Listing the rules
~~~~~~~~~~~~~~~~~

``manage.py permissions`` prints the rule of every page, every action, and every component that declares one, with the files each requirement comes from, and ``public (file)`` for a public rule, naming the file whose ``public()`` opened it.
An action row carries the file that declares the action beside its name, since two pages may declare actions of one name.
Every other view of the URLconf follows as a ``view`` row, read off the ``login_required`` mark ``LoginRequiredMiddleware`` consults, so under the middleware the table answers what an anonymous user reaches.
``manage.py permissions /notes/1/edit/`` narrows the table to one page, the actions of its directory, and the declaring components its template renders, a view URL narrows it to that view, and ``manage.py routes`` shows the same rule in its ``Access`` column, see :doc:`/content/ref/management`.

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
``manage.py check`` warns with ``next.W127`` about a literal name that no installed model defines and no ``add_perm()`` registers, in a page template or a component template, since such a block renders its ``else`` branch for every user but a superuser.

.. _topics-permissions-zones:

Zones and the decision
~~~~~~~~~~~~~~~~~~~~~~

A partial request renders one zone on its own, without the markup around it, so a decision taken outside the zone is not taken again.
The rule for a page with zones is that the decision about a permission lives inside the zone.

.. code-block:: jinja
   :caption: notes/pages/notes/[int:note]/template.djx

   {% zone "toolbar" %}
     {% has_perm "notes.edit_note" note %}
       <a href="edit/">Edit</a>
     {% endhas_perm %}
   {% endzone %}

Every render of this zone, the full page and a partial request alike, asks the permission for the user it renders for.

The framework reads the composed template of every page when it registers the zones of that page and marks every zone that sits behind a decision.

- A zone under a ``{% has_perm %}`` block of the composed page, a layout or the page template, the ``else`` branch included.
- A zone under an ``{% if %}`` or a ``{% for %}`` of the composed page.
- A zone under a block tag from a module outside ``django.`` and ``next.``, since such a tag may decide in code what its body shows.
- A zone in the body of a component, any slot or the children, when the component decides whether that part shows.

A component decides when it has a ``component.py``, whose ``render()``, contexts, or access declarations may decide in Python the analysis does not read, or when its template, followed through nested components, a literal ``{% extends %}``, and a literal ``{% include %}``, reaches a ``{% has_perm %}``, a condition around that part, or a reference the analysis cannot follow.
A reference it cannot follow is a component that does not resolve, a template name held in a variable, a template that does not exist, or one that does not compile.

A marked zone counts as undeclared for every user, so its partial request answers 400 ``unknown zone`` in production and in development alike, and ``Patches.morph_zone`` raises ``UnknownZoneError``.
A form whose zone is marked still posts, and its re-render answers with a morph of the form extracted from a full render of the page, which runs every decision.
``manage.py check`` reports such a zone as ``next.E161`` under a ``{% has_perm %}`` or in a deciding component, as ``next.E063`` under an ``{% if %}`` or an unread tag, and as ``next.E062`` under a ``{% for %}``.

.. code-block:: jinja
   :caption: a zone the framework refuses

   {% has_perm "notes.edit_note" note %}
     {% zone "editor" %}
       {% include "notes/editor.html" %}
     {% endzone %}
   {% endhas_perm %}

The analysis is static and reads templates only.
It cannot tell a ``component.py`` that decides from one that does not, so it treats every component with a ``component.py`` as deciding and refuses the zones in its body, which keeps the zone closed at the price of a partial render.
With ``DEBUG`` off the analysis of a page is kept for the life of the process, so an edit to a component, an extended, or an included template takes effect at the next start, while with ``DEBUG`` on the next request analyses the zones again.
A decision taken anywhere else in Python, such as a context value the zone prints, runs again in every render of the zone for the user it renders for, as any code inside the zone does.

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

The rule of the page tree the action belongs to runs first, see `Where a rule applies`_, and the static guard follows, so an anonymous visitor is redirected to ``LOGIN_URL`` and an unregistered permission is checked before the form binds, as it is for every model permission.
A registered permission waits for the bound form and is checked once, as ``user.has_perm(perm, form.instance)`` for a ``ModelForm`` that edits a saved row.
A ``ModelForm`` that creates a record and a plain ``Form`` pass ``None``, the model-level question.
A rule on the parent row a record is created under goes into ``has_object_permission`` or into the access of the page as ``permission_required(perm, obj=DObject[Model])``, see :ref:`topics-proxy`.

The permission runs only once the row has loaded, so the way the form loads it decides what a refused user learns.
``DObject`` in ``get_form_kwargs`` reads the row through ``visible_to(scope)``, so a row outside the scope answers 404 before any permission runs, exactly like a missing one, see :ref:`topics-di-dobject`.
``Meta.instance_from_url`` reads through ``visible_to(scope)`` the same way once the model manager defines ``visible_to`` and the project registers ``scope``.
Without both it loads through an unscoped :func:`~django.shortcuts.get_object_or_404`, so a refused user gets 403 for an id that exists and 404 for one that does not, which lets them probe which ids exist.
An overridden ``has_object_permission`` runs afterwards in addition, so it stays the place for a check that reads the request, the tenant, or a URL kwarg, see :ref:`topics-forms-actions-dynamic-guards`.

A ``FormWizard`` and a handler-only ``@action`` bind no form at the guard, so they check every listed permission before anything binds, registered ones included, with ``obj`` set to ``None``.

A denial on the bound row answers HTTP 403 and sends ``form_access_denied`` with ``layer="object"`` and ``reason="denied"``, see :ref:`topics-forms-signals-form-access-denied`.
``manage.py check`` warns with ``next.W127`` about a ``permission_required`` name that no installed model defines and no ``add_perm()`` registers, since such a guard refuses every user but a superuser, in a project that declares access, see :doc:`/content/ref/system-checks`.

``next.W127`` reads the model permissions off the model classes, the ``default_permissions`` and the ``Meta.permissions`` of every installed model, beside the registered names.
A ``Permission`` row that exists only in the database, created by a data migration or through the admin, is unknown to them and draws the warning.
Declare such a permission in ``Meta.permissions`` of its model, or list the code in ``SILENCED_SYSTEM_CHECKS``.

django-rules compatibility
--------------------------

next.dj does not depend on django-rules, and the two sit side by side.
Both backends can be listed in ``AUTHENTICATION_BACKENDS``, and ``user.has_perm()`` asks each of them, so a permission declared in either one answers the same call.
``rules.py`` belongs to django-rules and ``permissions.py`` to next.dj, and a project can keep both.
Only a name registered with ``next.permissions`` waits for the bound form, so a django-rules permission named in ``Meta.permission_required`` is checked by the static guard before the form binds, with ``obj`` set to ``None``.
When ``rules.permissions.ObjectPermissionBackend`` is listed, ``next.W127`` counts a permission django-rules registers as known.
A template that runs ``{% load rules %}`` gets django-rules' own ``has_perm`` tag in that template, which replaces the builtin there.

The tag library of ``{% has_perm %}`` is the module ``next.templatetags.permissions``, so its library name is ``permissions``.
A project application with a ``templatetags/permissions.py`` of its own shares that name, and Django reports the clash as ``templates.W003``, as it does for the other next.dj libraries.

See also
--------

.. seealso::

   :doc:`/content/howto/enforce-object-level-permissions` for an owner-only edit form end to end.
   :doc:`/content/ref/permissions` for the public names.
   :ref:`topics-forms-actions-guards` for the static guard and the dynamic hooks.
   :doc:`proxy` for the access a ``proxy.py`` declares over a subtree and the lift of ``public()``.
   :doc:`/content/howto/require-login-on-pages` for a login on every page through ``LoginRequiredMiddleware``.
   :doc:`application-layout` for where a predicate sits beside ``visible_to(scope)`` and the services.
   :doc:`/content/security/actions` for the access an action owes to the tree it is declared in.
   :doc:`/content/ref/system-checks` for ``next.E159`` to ``next.E164`` and ``next.W127`` to ``next.W129``.
