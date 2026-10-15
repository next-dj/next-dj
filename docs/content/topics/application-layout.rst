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
       def visible_to(self, user):
           if user.is_superuser:
               return self
           if not user.is_authenticated:
               return self.none()
           return self.filter(owner=user)

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

``visible_to(user)`` is a recommended convention of this layout rather than a framework API, and next.dj neither calls nor requires it.
It names the one question every read on behalf of a user starts with, so a page that lists notes and a service that loads one for writing filter the same way.
See :doc:`django:topics/db/managers` for custom ``QuerySet`` classes and ``as_manager``.

Invariants
~~~~~~~~~~

An invariant goes into ``Meta.constraints`` first, so the database enforces it for every writer.
A rule a constraint cannot express goes into ``Model.clean``.
A service calls :meth:`~django.db.models.Model.full_clean` before ``save``, which runs field validation, ``clean``, and the constraint validation in one call.
A ``ModelForm``, the admin form included, runs the same validation on its instance, so the form and the service check the same rules.

Services
--------

A function in ``services.py`` takes the acting user as its first argument and everything else as keyword-only arguments, ``f(user, *, ...)``.
The fixed shape makes every call site easy to find and keeps the access question inside the function that writes.
A service receives plain values and returns model instances or plain values, and it never sees ``request`` or builds a response.

.. code-block:: python
   :caption: notes/services.py

   from django.db import transaction
   from django.utils import timezone
   from notes.models import Note
   from notes.signals import note_archived

   def archive_note(user, *, note_id: int) -> Note:
       with transaction.atomic():
           note = Note.objects.visible_to(user).select_for_update().get(pk=note_id)
           note.archived_at = timezone.now()
           note.pinned = False
           note.full_clean()
           note.save()
           note_archived.send(sender=Note, note=note, user=user)
       return note

``select_for_update`` belongs to the service that loads the row inside the lock, as ``archive_note`` does.
Locking an instance that was loaded before the transaction began guarantees nothing about the values it already holds.
See :meth:`~django.db.models.query.QuerySet.select_for_update` and :func:`~django.db.transaction.atomic` for the Django side.

Delivery calls the domain
-------------------------

A page, a form, and the admin are three ways to deliver one operation, and all three call the same service.
A form reaches it from ``on_valid``, where the dispatcher has already applied the ``Meta`` guards and validated the input.

.. code-block:: python
   :caption: notes/forms.py, the archive form

   from django.http import Http404, HttpRequest, HttpResponseRedirect
   from django.urls import reverse
   from notes.models import Note
   from notes.services import archive_note

   from next.forms import BooleanField, Form
   from next.urls import DUrl

   class ArchiveNoteForm(Form):
       confirm = BooleanField(required=True)

       class Meta:
           login_required = True

       def on_valid(self, request: HttpRequest, note_id: DUrl["id", int]) -> HttpResponseRedirect:
           try:
               archive_note(request.user, note_id=note_id)
           except Note.DoesNotExist as exc:
               raise Http404 from exc
           return HttpResponseRedirect(reverse("next:page_"))

The admin calls the service from its actions, so an archive in the admin passes the same rules as an archive on the site.
Its changelist narrows to ``visible_to(request.user)``, so an action only ever receives rows the service can load for that user.

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
Its context functions read through ``QuerySet`` methods such as ``Note.objects.visible_to(user).pinned()``, and its writes go through a form or a service.

Signals
-------

An application talks to a neighbour by importing a function from the neighbour's ``services``, ``models``, or ``signals`` module.
A signal replaces that import only where the import would create a cycle, which happens when the neighbour already imports this application.
In the example an ``activity`` application imports ``notes`` to record what happens to notes, so ``notes`` cannot import ``activity`` back and announces the archive through ``note_archived`` instead.

.. code-block:: python
   :caption: notes/signals.py

   from django.dispatch import Signal

   note_archived = Signal()

A receiver runs synchronously inside the transaction the sender has open, as ``archive_note`` does, so a receiver that writes defers the write with :func:`~django.db.transaction.on_commit`.
The write then happens only once the archive is committed, and a rolled-back archive leaves no trace in the neighbour.

.. code-block:: python
   :caption: activity/signals.py

   from activity.services import record_event
   from django.db import transaction
   from django.dispatch import receiver
   from notes.signals import note_archived

   @receiver(note_archived)
   def record_archive(sender, note, user, **kwargs) -> None:
       transaction.on_commit(lambda: record_event(user, verb="archived", target=note))

next.dj does not discover ``signals.py``, so the ``AppConfig.ready`` of each application imports it, as :ref:`topics-project-layout` shows for ``notes``.
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
     - The framework signals, importable from ``next.signals``.
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
     - Providers that build a parameter value from its annotation, and the ``form_class`` factory of an ``@action``.
   * - Composite
     - Template includes.
     - Components with props and slots, and zones inside a page.
   * - Specification
     - ``Q`` objects and ``QuerySet`` methods.
     - Nothing beyond Django.
   * - Registry
     - The ``apps`` registry and ``admin.site``.
     - Form classes and providers that register on class definition, ``forms.py`` imported at startup, and components registered from ``_components/`` folders.
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
