.. _topics-application-layout:

Where logic lives
=================

Inside one application every piece of domain logic has one place, chosen by what the logic touches.
The rule is Django's own, and next.dj adds no layer, container, or base class on top of it.
:doc:`project-layout` names the files, and this page decides which of them a function belongs in.

.. contents::
   :local:
   :depth: 2

The placement rule
------------------

Logic that fits a method on one model instance goes on the model.
Logic over a set of rows goes on a custom ``QuerySet``.
Logic that touches several models or needs a transaction goes into a function in ``services.py``.
Each of them is a function or a method, never a service class or a repository object, because a function is the smallest unit Python imports, tests, and finds with a text search.

When ``services.py`` outgrows one screen it becomes a ``services/`` package with one-word submodules, and its import address stays the same, see :ref:`topics-project-layout`.

Models and querysets
--------------------

A model carries its fields, the methods that act on one instance, and the invariants of a single row.
A custom ``QuerySet`` carries the reads over many rows, so a page and a service ask the same named question instead of repeating a filter.

.. code-block:: python
   :caption: notes/models.py

   from django.conf import settings
   from django.db import models

   class NoteQuerySet(models.QuerySet):
       def visible_to(self, scope):
           if scope.is_superuser:
               return self
           if not scope.is_authenticated:
               return self.none()
           return self.filter(owner=scope)

       def pinned(self):
           return self.filter(pinned=True, archived_at__isnull=True)

   class Note(models.Model):
       owner = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
       title = models.CharField(max_length=200)
       body = models.TextField(blank=True)
       pinned = models.BooleanField(default=False)
       archived_at = models.DateTimeField(null=True, blank=True)

       objects = NoteQuerySet.as_manager()

       class Meta:
           constraints = [
               models.UniqueConstraint(fields=["owner", "title"], name="note_title_per_owner"),
               models.CheckConstraint(
                   condition=models.Q(archived_at__isnull=True) | models.Q(pinned=False),
                   name="archived_note_not_pinned",
               ),
           ]

       @property
       def is_archived(self) -> bool:
           return self.archived_at is not None

``visible_to(scope)`` names the one question every read on behalf of someone starts with, so a page that lists notes and a service that loads one for writing filter the same way.
The scope is whatever owns the rows, a user here and a tenant in a multi-tenant project, and the ``scope`` dependency in ``deps.py`` decides it once per project.

.. code-block:: python
   :caption: notes/deps.py

   from django.http import HttpRequest

   from next.deps import resolver

   @resolver.dependency("scope")
   def scope(request: HttpRequest):
       return request.user

``DObject[Note]`` loads the row a URL addresses through the same method, so a page never reaches a note its scope cannot see, see :ref:`topics-di-dobject`.
A model never placed under ``DObject`` may skip ``visible_to``, while one that is placed there without it fails ``manage.py check`` with ``next.E170``.
See :doc:`django:topics/db/managers` for custom ``QuerySet`` classes and ``as_manager``.

Invariants
~~~~~~~~~~

An invariant goes into ``Meta.constraints`` first, so the database enforces it for every writer.
A rule a constraint cannot express goes into ``Model.clean``.
A service calls :meth:`~django.db.models.Model.full_clean` before ``save``, which runs field validation, ``clean``, and the constraint validation in one call.
A ``ModelForm``, the admin form included, runs the same validation on its instance, so the form and the service check the same rules.

Services
--------

A function in ``services.py`` that acts on behalf of the scope takes it as its first argument and everything else as keyword-only arguments, ``f(scope, *, ...)``.
The fixed shape makes every call site easy to find and keeps the access question inside the function that writes.
A public read that does not depend on who asks takes no scope, as ``cached_search`` in ``catalog/services.py`` of the ``examples/search-catalog`` project shows.
A service receives plain values and returns model instances or plain values, and it never sees ``request`` or builds a response.

.. code-block:: python
   :caption: notes/services.py

   from django.db import transaction
   from django.utils import timezone
   from notes.models import Note
   from notes.signals import note_archived

   def archive_note(scope, *, note_id: int) -> Note:
       with transaction.atomic():
           note = Note.objects.visible_to(scope).select_for_update().get(pk=note_id)
           note.archived_at = timezone.now()
           note.pinned = False
           note.full_clean()
           note.save()
           note_archived.send(sender=Note, note=note, scope=scope)
       return note

``select_for_update`` belongs to the service that loads the row inside the lock, as ``archive_note`` does.
Locking an instance that was loaded before the transaction began guarantees nothing about the values it already holds.
See :meth:`~django.db.models.query.QuerySet.select_for_update` and :func:`~django.db.transaction.atomic` for the Django side.

Delivery calls the domain
-------------------------

A page, a form, and the admin are three ways to deliver one operation, and all three call the same service.
A form reaches it from ``on_valid``, where the dispatcher has already applied the ``Meta`` guards and validated the input, and asks for the scope through ``Depends("scope")``.

.. code-block:: python
   :caption: notes/forms.py, the archive form

   from django.http import Http404, HttpResponseRedirect
   from django.urls import reverse
   from notes.models import Note
   from notes.services import archive_note

   from next import Depends
   from next.forms import BooleanField, Form
   from next.urls import DUrl

   class ArchiveNoteForm(Form):
       confirm = BooleanField(required=True)

       class Meta:
           login_required = True

       def on_valid(self, note_id: DUrl["id", int], scope=Depends("scope")) -> HttpResponseRedirect:
           try:
               archive_note(scope, note_id=note_id)
           except Note.DoesNotExist as exc:
               raise Http404 from exc
           return HttpResponseRedirect(reverse("next:page_"))

The admin calls the service from its actions, so an archive in the admin passes the same rules as an archive on the site.
The admin resolves no dependencies, so it passes ``request.user``, the value the ``scope`` dependency of this project returns.
Its changelist narrows to ``visible_to(request.user)``, so an action only ever receives rows the service can load for that scope.

.. code-block:: python
   :caption: notes/admin.py

   from django.contrib import admin
   from notes import services
   from notes.models import Note

   @admin.register(Note)
   class NoteAdmin(admin.ModelAdmin):
       actions = ["archive"]

       def get_queryset(self, request):
           return super().get_queryset(request).visible_to(request.user)

       @admin.action(description="Archive selected notes")
       def archive(self, request, queryset):
           for note in queryset:
               services.archive_note(request.user, note_id=note.pk)

A ``page.py`` follows the same split.
Its context functions read lists through ``QuerySet`` methods such as ``Note.objects.visible_to(scope).pinned()``, take the row a URL addresses as ``DObject[Note]``, and send writes through a form or a service.

Signals
-------

An application talks to a neighbour by importing a function from the neighbour's ``services``, ``models``, or ``signals`` module.
A signal replaces that import only where the import would create a cycle, which happens when the neighbour already imports this application.
In the example an ``activity`` application imports ``notes`` to record what happens to notes, so ``notes`` cannot import ``activity`` back and announces the archive through ``note_archived`` instead.

.. code-block:: python
   :caption: notes/signals.py

   from django.dispatch import Signal

   note_archived = Signal()

A receiver runs synchronously inside the transaction the sender has open, as ``archive_note`` does, so a receiver that writes connects with ``receiver(..., on_commit=True)``.
The receiver then runs only once the archive is committed, and a rolled-back archive leaves no trace in the neighbour.

.. code-block:: python
   :caption: activity/signals.py

   from activity.services import record_event
   from notes.signals import note_archived

   from next import receiver

   @receiver(note_archived, on_commit=True)
   def record_archive(sender, note, scope, **kwargs) -> None:
       record_event(scope, verb="archived", target=note)

The ``next`` application imports the ``signals.py`` of every installed application at startup, so the receiver connects with no ``AppConfig.ready`` code.
An event that must survive a crash between the commit and the receiver is no job for a signal or for ``on_commit``, and it belongs to an outbox library.
See :doc:`django:topics/signals` for the Django side and :doc:`signals` for the signals the framework itself sends.

Patterns already in the box
---------------------------

A pattern gets built twice when nobody recognises it in the tools already at hand.
The table names each familiar pattern, where Django already provides it, and what next.dj contributes today.

.. list-table::
   :header-rows: 1
   :widths: 18 38 44

   * - Pattern
     - Where it already lives
     - What next.dj adds
   * - Facade
     - ``services.py`` of the application, a flat module of functions over its models.
     - Nothing beyond the convention.
   * - Observer
     - ``django.dispatch.Signal``, ``@receiver``, and the ``signals.py`` of each application.
     - The framework signals, importable from ``next.signals``, and ``receiver(..., on_commit=True)`` for a receiver that waits for the commit.
   * - Chain of responsibility
     - Middleware.
     - The ``layout.djx`` chain that wraps every page below a layout, and ``inherit_context=True`` that passes a context value down the tree.
   * - Command
     - Management commands.
     - Form actions, each an ``on_valid`` or an ``@action`` handler served at its own endpoint behind its guards, and ``FormWizard`` as a command split into steps.
   * - Strategy and adapter
     - ``AUTHENTICATION_BACKENDS`` and the cache and storage backends.
     - The backend keys of ``NEXT_FRAMEWORK`` such as ``PAGE_BACKENDS`` and ``STATIC_BACKENDS``, and providers in ``deps.py`` that adapt the request to a domain value.
   * - Template method
     - Template inheritance with ``{% block %}`` and class-based views.
     - ``layout.djx`` with ``{% template %}``, composed with the page template into one template per page.
   * - Builder
     - ``QuerySet`` chaining.
     - ``Patches``, which collects zone refreshes into one partial response.
   * - Factory
     - ``Manager.create`` and form construction.
     - Providers that build a parameter value from its annotation, ``get_form_kwargs`` that builds the constructor arguments of a form, and the ``form_class`` factory of an ``@action``.
   * - Composite
     - Template includes.
     - Components with props and slots, and zones inside a page.
   * - Specification
     - ``Q`` objects and ``QuerySet`` methods.
     - Nothing beyond Django.
   * - Registry
     - The ``apps`` registry and ``admin.site``.
     - Form classes and providers that register on class definition, ``deps.py``, ``forms.py``, and ``signals.py`` imported at startup, and components registered from ``_components/`` folders.
   * - Memento and state
     - Sessions.
     - ``FormWizard`` drafts kept in the storage named by ``FORM_WIZARD_BACKEND``.

What the layout leaves out
~~~~~~~~~~~~~~~~~~~~~~~~~~

No Repository or Unit of Work sits over the ORM, because the ``QuerySet`` and ``transaction.atomic`` already are both.
No ``Service`` class with a ``handle`` method appears, because an operation is a function.
No event bus mediates between applications, because signals cover reactions inside the process and an event that must never be lost belongs to an outbox library.
No singleton DI container holds services, because a Python module already is a singleton and ``deps.py`` covers the values a request carries.

See also
--------

.. seealso::

   :doc:`project-layout` for the files of an application and the project sizes.
   :doc:`dependency-injection` for the providers that live in ``deps.py``.
   :doc:`signals` for the signals the framework sends.
   :doc:`/content/topics/forms/actions` for the ``Meta`` guards on a form.
