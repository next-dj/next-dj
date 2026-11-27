.. _intro-tutorial07:

Slow blocks with a loading and an error UI
==========================================

Goal
----

This part adds a summary block to the Notes index that takes a second and a half to compute.
The page answers at once with a skeleton in the block's place, and the block arrives in the same response when its work ends.
Then the block fails on purpose, and only the block shows an error while the rest of the page stays as it was.
The whole part is two files beside a component and no Python beyond the component's own context.

Prerequisites
-------------

You have finished :doc:`tutorial06`.
The index template holds the create form, the search box, and the ``note-list`` zone, and the ``note_card`` component from :doc:`tutorial03` renders each row.
The pytest setup from :doc:`tutorial05` is in place.

Walkthrough
-----------

Add a slow block
~~~~~~~~~~~~~~~~

A block that is slow to compute is a component like any other.
Create the folder ``notes/pages/_components/summary/``.
Its context function stands in for a slow query or a call to another service with a pause.

.. code-block:: python
   :caption: notes/pages/_components/summary/component.py

   import time

   from notes.models import Note

   from next import component

   @component.context("summary")
   def summary() -> dict[str, int]:
       time.sleep(1.5)
       words = sum(len(note.body.split()) for note in Note.objects.all())
       return {"notes": Note.objects.count(), "words": words}

.. code-block:: jinja
   :caption: notes/pages/_components/summary/component.djx

   <aside class="summary">
     <h2>Summary</h2>
     <p>{{ summary.notes }} notes, {{ summary.words }} words.</p>
   </aside>

Call it at the top of ``notes/pages/template.djx``, above the create form.
The rest of the template stays as :doc:`tutorial06` left it.

.. code-block:: jinja
   :caption: notes/pages/template.djx, the first line

   {% component "summary" %}

Reload ``/``.
The whole page now waits for the pause, because a component without a loading UI renders in place and the response is not sent before it ends.

Show a loading UI
~~~~~~~~~~~~~~~~~

Put a ``loading.djx`` in the component folder.

.. code-block:: jinja
   :caption: notes/pages/_components/summary/loading.djx

   <aside class="summary summary--loading" aria-busy="true">
     <h2>Summary</h2>
     <p>Counting the words&hellip;</p>
   </aside>

Nothing else changes, not the component, not the template, and no setting.
Reload ``/`` and the page appears at once with the paragraph in the block's place.
About a second later the paragraph gives way to the summary, with no second request and no JavaScript of your own.

The presence of the file is the whole declaration.
It turns every call of ``summary`` into a :term:`deferred boundary`, and the browser fills it from the same response.

Contain a failure
~~~~~~~~~~~~~~~~~

Make the context function fail by adding a line after the pause.

.. code-block:: python
   :caption: notes/pages/_components/summary/component.py

   import time

   from notes.models import Note

   from next import component

   @component.context("summary")
   def summary() -> dict[str, int]:
       time.sleep(1.5)
       raise RuntimeError("The summary service is down")
       words = sum(len(note.body.split()) for note in Note.objects.all())
       return {"notes": Note.objects.count(), "words": words}

Reload ``/``.
The skeleton appears and then gives way to the framework's built-in error UI, a short unstyled notice with a button that tries again, while the form, the search box, and the list work as before.
The notice names nothing of the failure, so it is the same with ``DEBUG`` off.

An ``error.djx`` beside the component says what the visitor sees instead.

.. code-block:: jinja
   :caption: notes/pages/_components/summary/error.djx

   <aside class="summary summary--error">
     <h2>Summary</h2>
     <p>The summary is unavailable right now.</p>
     {% if error.retry %}<button type="button" data-next-retry>Try again</button>{% endif %}
   </aside>

Reload ``/`` once more.
The block shows the message and a button that fetches the block again, and the rest of the page is untouched.
The exception and its traceback are logged with a short digest, and under ``DEBUG`` the traceback also follows the error UI in the page.
Remove the ``raise`` line when the block has shown its failure.

.. note::

   An ``error.djx`` works without a ``loading.djx``.
   It catches a failure of the component in every render, a deferred one or not.

Test the block
~~~~~~~~~~~~~~

The test client of :doc:`tutorial05` reads a response as a browser would, with ``get_stream`` splitting a streamed page into its shell and the blocks that settled after it.
Create ``tests/test_notes_boundaries.py``.

.. code-block:: python
   :caption: tests/test_notes_boundaries.py

   import time

   from notes.models import Note

   from next.testing import boundary_errors

   def test_summary_arrives_after_the_shell(next_client, db, monkeypatch) -> None:
       monkeypatch.setattr(time, "sleep", lambda seconds: None)
       Note.objects.create(title="First", body="one two three")
       page = next_client.get_stream("/")
       assert "Counting the words" in page.shell
       assert "1 notes, 3 words." not in page.shell
       assert page.boundary("summary").state == "ready"
       assert "1 notes, 3 words." in page.final_html()

   def test_summary_failure_stays_in_its_block(next_client, db, monkeypatch) -> None:
       def fail(seconds: float) -> None:
           raise RuntimeError("The summary service is down")

       monkeypatch.setattr(time, "sleep", fail)
       with boundary_errors("render"):
           page = next_client.get_stream("/")
       summary = page.boundary("summary")
       assert summary.state == "error"
       assert "The summary is unavailable" in summary.html
       assert 'data-next-zone="note-list"' in page.shell

The client re-raises a failure that a boundary contained, so a broken component fails its test instead of hiding behind its error UI.
The second test asks for the error UI to render with ``boundary_errors("render")``.
A plain ``next_client.get("/")`` assembles the page whole, with the summary already in it.

What happened
-------------

The page answered with its shell, the response as first rendered with the summary replaced by its ``loading.djx``.
The work of the block then ran while the response was still open, and its HTML followed in the same response as soon as it settled.
The visitor saw the page after a few milliseconds instead of after the pause, and a failure of the block reached only the block.
:doc:`/content/topics/boundaries/index` explains the vocabulary and :doc:`/content/topics/boundaries/compared` sets the same task beside htmx, Next.js, Turbo, and LiveView.

.. note::

   The skeleton does not show where the server cannot stream.
   A sync worker that serves one request at a time, such as gunicorn without threads, assembles the page whole and sends it in one piece, so the visitor waits for the pause and then sees the finished block.
   The same holds behind a proxy that buffers responses, for a crawler, and for a ``POST``.
   On SQLite the blocks of one page fill one after another instead of at once, and the skeleton still shows.
   :doc:`/content/topics/boundaries/streaming` lists every reason and how to see which one applied, and :doc:`/content/topics/boundaries/async` covers the lanes the work runs on.

Checkpoint
----------

.. code-block:: text
   :caption: files touched in this part

   notes/
     pages/
       template.djx          # {% component "summary" %} on the first line
       _components/
         summary/
           component.py
           component.djx
           loading.djx
           error.djx
   tests/
     test_notes_boundaries.py

The index answers with its shell at once and the summary follows.
A failing summary shows its own error UI and leaves the rest of the page working.

Common pitfalls
---------------

The page waits for the summary and no skeleton shows.
   Check that the file is named ``loading.djx`` and sits in the component folder next to ``component.djx``.
   If it does, the server is assembling the page whole, see the note above.

The block stays empty after a failure.
   Add the ``error.djx``, or check that the file sits in the component folder.
   Without it the framework renders an empty block marked as an error.

The retry button is missing.
   ``error.retry`` is false when the page does not load the client runtime, so keep ``{% collect_scripts %}`` in the layout.

A test fails with the exception of the component.
   The test client re-raises a failure a boundary contained.
   Render the error UI inside ``boundary_errors("render")`` when the test is about it.

Next steps
----------

The Notes application is complete, with a slow block that shows a skeleton and survives its own failure.

.. seealso::

   :doc:`whatsnext` lists where to go next, by topic.
   :doc:`/content/topics/boundaries/index` covers the delivery modes, the tokens of a block the browser fetches later, and the settings.
   :doc:`/content/howto/show-a-loading-state` and :doc:`/content/howto/contain-a-failure-with-an-error-boundary` walk through the two tasks of this part.
   :doc:`/content/topics/router/index` covers the client router that already navigates between the pages of Notes.
