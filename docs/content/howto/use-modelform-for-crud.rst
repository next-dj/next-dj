.. _howto-modelform-crud:

Use ModelForm for CRUD
======================

Problem
-------

You want create and edit pages for a model with as little glue code as possible.

Solution
--------

Declare one ``next.forms.ModelForm`` subclass.
Add ``Meta.instance_from_url`` so the edit page loads its row from the captured URL kwarg.
The same class drives the create page, where the kwarg is absent and the form renders unbound.

Walkthrough
-----------

Edit page
~~~~~~~~~

The edit page lives under a route that captures the lookup field.
Here the route segment is ``[slug]``, so the captured kwarg is ``slug``.

.. code-block:: python
   :caption: notes/pages/notes/edit/[slug]/page.py

   import next.forms
   from notes.models import Note

   class NoteEditForm(next.forms.ModelForm):
       class Meta:
           model = Note
           fields = ["slug", "title", "body"]
           instance_from_url = "slug"

The default ``get_initial`` loads ``Note.objects.get(slug=<captured slug>)`` through :func:`~django.shortcuts.get_object_or_404`.
The default ``on_valid`` calls ``self.save()`` and redirects to the origin page.
No handler, no hidden lookup field, and no second lookup are needed.

.. code-block:: jinja
   :caption: notes/pages/notes/edit/[slug]/template.djx

   {% form "note_edit_form" %}
     {{ form.slug }}
     {{ form.title }}
     {{ form.body }}
     <button type="submit">Save</button>
   {% endform %}

The ``{% form %}`` tag resolves the action by name, opens the ``<form>`` element, injects the CSRF token, and publishes ``form`` inside the block.
Because the route captures ``slug``, the tag also emits a hidden ``_url_param_slug`` field carrying the current value, so the submission re-attaches to the same row.

Create page
~~~~~~~~~~~

The create page reuses the same class on a route with no captured kwarg.

.. code-block:: python
   :caption: notes/pages/notes/new/page.py

   import next.forms
   from notes.models import Note

   class NoteEditForm(next.forms.ModelForm):
       class Meta:
           model = Note
           fields = ["slug", "title", "body"]
           instance_from_url = "slug"

With no ``slug`` in the URL, ``get_initial`` returns an empty dict and the form renders fresh.
``self.save()`` then inserts a new row.

.. code-block:: jinja
   :caption: notes/pages/notes/new/template.djx

   {% form "note_edit_form" %}
     {{ form.slug }}
     {{ form.title }}
     {{ form.body }}
     <button type="submit">Create</button>
   {% endform %}

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
           {"slug": "intro", "title": "Intro v2", "body": "", "_url_param_slug": note.slug},
       )
       assert Note.objects.get(pk=note.pk).title == "Intro v2"

The first ``post_action`` hits the create page where no URL kwarg is present, so the form is unbound and inserts a row.
The second posts ``_url_param_slug``, which mirrors the hidden field the ``{% form %}`` tag emits for the captured ``slug`` parameter, so ``instance_from_url`` loads the existing row and the save updates it.

The dispatcher casts each recovered ``_url_param_*`` value to ``int`` when the cast succeeds and falls back to the original string when it does not.
A numeric ``[int:id]`` kwarg arrives as an integer on both the initial render and the re-render, while a slug stays a string.

See Also
--------

.. seealso::

   :doc:`/content/topics/forms/modelforms` for the ModelForm topic guide.
   :doc:`/content/topics/forms/templates` for the ``{% form %}`` tag.
   :doc:`/content/topics/forms/validation-rerender` for the re-render flow.
