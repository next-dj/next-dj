.. _howto-modelform-crud:

Use ModelForm for CRUD
======================

Problem
-------

You want create and edit pages for a model with as little glue code as possible.

Solution
--------

Declare one ``next.forms.ModelForm`` subclass in a shared module such as ``notes/forms.py``.
Add ``Meta.instance_from_url`` so the edit page loads its row from the captured URL kwarg.
The same class drives the create page, where the kwarg is absent and the form renders unbound.

Walkthrough
-----------

Declare the form once
~~~~~~~~~~~~~~~~~~~~~

The class lives outside ``page.py``, so it registers with shared scope: one action name and one action URL serve both pages.
Autodiscovery imports ``notes/forms.py`` on startup, so neither page module imports it.

.. code-block:: python
   :caption: notes/forms.py

   import next.forms
   from notes.models import Note

   class NoteEditForm(next.forms.ModelForm):
       class Meta:
           model = Note
           fields = ["slug", "title", "body"]
           instance_from_url = "slug"

A copy declared in each ``page.py`` would be page-scoped instead, keeping the shared action name but giving every per-file registration its own action URL.
See :doc:`/content/topics/forms/modelforms` for that distinction and :doc:`/content/topics/forms/actions` for the scope rules.

Edit page
~~~~~~~~~

The edit page lives under a route that captures the lookup field.
Here the route segment is ``[slug]``, so the captured kwarg is ``slug``.

.. code-block:: jinja
   :caption: notes/pages/notes/edit/[slug]/template.djx

   {% form "note_edit_form" %}
     {{ form.slug }}
     {{ form.title }}
     {{ form.body }}
     <button type="submit">Save</button>
   {% endform %}

The default ``get_initial`` loads ``Note.objects.get(slug=<captured slug>)`` through :func:`~django.shortcuts.get_object_or_404`.
The default ``on_valid`` calls ``self.save()`` and redirects to the origin page.
No handler, no hidden lookup field, and no second lookup are needed.

The ``{% form %}`` tag resolves the action by name, opens the ``<form>`` element, injects the CSRF token, and publishes ``form`` inside the block.
It also emits a hidden ``_next_form_origin`` field with the page URL, so the dispatcher recovers the captured ``slug`` by resolving that path and the submission re-attaches to the same row.

Create page
~~~~~~~~~~~

The create page renders the same form on a route with no captured kwarg.

.. code-block:: jinja
   :caption: notes/pages/notes/new/template.djx

   {% form "note_edit_form" %}
     {{ form.slug }}
     {{ form.title }}
     {{ form.body }}
     <button type="submit">Create</button>
   {% endform %}

With no ``slug`` in the URL, ``get_initial`` returns an empty dict and the form renders fresh.
``self.save()`` then inserts a new row.

URL names follow the ``page_{path}`` convention where path segments are joined with underscores and captured-parameter brackets are dropped.
See :doc:`/content/topics/file-router` for the full naming rules.

Verification
------------

Walk through the flow once.
Create a note, then edit it, and confirm the index reflects each step.

A test asserts the same flow with ``NextClient``.

.. code-block:: python
   :caption: tests/test_crud.py

   from next.testing.client import NextClient
   from notes.models import Note

   def test_crud_flow(db) -> None:
       client = NextClient()
       client.post_action("note_edit_form", {"slug": "intro", "title": "Intro", "body": ""})
       note = Note.objects.get(slug="intro")
       client.post_action(
           "note_edit_form",
           {"slug": "intro", "title": "Intro v2", "body": ""},
           origin=f"/notes/edit/{note.slug}/",
       )
       assert Note.objects.get(pk=note.pk).title == "Intro v2"

The first ``post_action`` mimics the create page: with no ``origin`` there is no captured kwarg, so the form is unbound and inserts a row.
The second passes ``origin``, which fills the ``_next_form_origin`` field the ``{% form %}`` tag emits, so resolving the edit-page path yields the ``slug`` kwarg, ``instance_from_url`` loads the existing row, and the save updates it.

The recovered kwargs come through the URL converters of the resolved route, so a ``[int:id]`` kwarg arrives as an integer on both the initial render and the re-render, while a slug stays a string.

See Also
--------

.. seealso::

   :doc:`/content/topics/forms/modelforms` for the ModelForm topic guide.
   :doc:`/content/topics/forms/templates` for the ``{% form %}`` tag.
   :doc:`/content/topics/forms/validation-rerender` for the re-render flow.
