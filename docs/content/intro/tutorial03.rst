.. _intro-tutorial03:

Components and Static Assets
============================

Goal
----

By the end of this part the index page renders each note through a reusable component, the component ships its own CSS and JS, and both files load through the static collector without any manual ``<link>`` or ``<script>`` plumbing.

Prerequisites
-------------

You have finished :doc:`tutorial02`.
The root layout publishes ``site_name``, ``tagline``, and ``note_count``.
The detail page at ``/notes/<id>/`` renders one note from the URL.
:doc:`tutorial04` adds form actions on top of this component-driven UI.

Walkthrough
-----------

Create the Component Folder
~~~~~~~~~~~~~~~~~~~~~~~~~~~

The :doc:`component </content/topics/components>` backend looks for component folders under the configured root.
:doc:`install` set ``COMPONENTS_DIR`` to ``_components``, so a component named ``note_card`` lives at ``notes/_components/note_card/``.
The framework treats the folder name as the component name.

Create the directory and an empty template.

.. code-block:: jinja
   :caption: notes/_components/note_card/component.djx

   <article class="note-card">
     <header>
       <a href="{% url 'next:page_notes_id' id=note.id %}">{{ note.title }}</a>
       <time datetime="{{ note.created_at|date:'c' }}">{{ note.created_at|date:'Y-m-d H:i' }}</time>
     </header>
     {% if note.body %}<p>{{ note.body }}</p>{% endif %}
   </article>

Components reference variables by name.
``note`` will come from the surrounding template context that the framework forwards into the component automatically.

Use the Component
~~~~~~~~~~~~~~~~~

Replace the inline markup in the index template with a call to ``note_card``.

.. code-block:: jinja
   :caption: notes/routes/template.djx

   <ul class="note-list">
     {% for note in notes %}
       <li>{% component "note_card" %}</li>
     {% endfor %}
   </ul>

The ``{% component "note_card" %}`` tag does three things in one line.
It resolves the component by name, runs any context functions declared in a ``component.py``, and renders the component template inside the loop iteration.

Reload ``/`` and confirm that the page still lists both notes.
The HTML now uses an ``<article>`` per note instead of an ``<li>`` block.

Add Co-located CSS
~~~~~~~~~~~~~~~~~~

Place a CSS file next to ``component.djx`` and the :doc:`static pipeline </content/topics/static-assets/index>` picks it up automatically.

.. code-block:: css
   :caption: notes/_components/note_card/component.css

   .note-card {
     border: 1px solid #ddd;
     border-radius: 8px;
     padding: 1rem;
     margin-bottom: 0.75rem;
   }

   .note-card header {
     display: flex;
     justify-content: space-between;
     gap: 1rem;
   }

   .note-card time {
     color: #888;
     font-size: 0.85rem;
   }

The framework finds ``component.css`` by stem.
When a page renders a component that has co-located styles, the static collector adds the file to the current request slot.

Wire the Collector Into the Layout
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Tell the layout where to emit the collected style and script tags.

.. code-block:: jinja
   :caption: notes/routes/layout.djx

   <!doctype html>
   <html>
     <head>
       <title>{{ site_name }}</title>
       {% collect_styles %}
     </head>
     <body>
       <header>
         <a href="{% url 'next:page_' %}"><h1>{{ site_name }}</h1></a>
         <p>{{ tagline }} ({{ note_count }} notes)</p>
       </header>
       <main>
         {% block template %}{% endblock template %}
       </main>
       {% collect_scripts %}
     </body>
   </html>

The ``{% collect_styles %}`` tag emits one ``<link>`` per discovered stylesheet.
``{% collect_scripts %}`` does the same for JavaScript.
Each asset is hashed and deduplicated, so the same file referenced from two components is emitted once.

Reload ``/`` and confirm that the served HTML now contains a ``<link>`` to ``note_card/component.css``.

Add Co-located JavaScript
~~~~~~~~~~~~~~~~~~~~~~~~~

Co-located scripts work the same way.
Add a small enhancement that toggles a class when the note title is clicked.

.. code-block:: javascript
   :caption: notes/_components/note_card/component.js

   document.addEventListener("DOMContentLoaded", () => {
     for (const card of document.querySelectorAll(".note-card")) {
       const title = card.querySelector("header a");
       if (!title) continue;
       title.addEventListener("click", (event) => {
         event.preventDefault();
         card.classList.toggle("note-card--expanded");
         setTimeout(() => {
           window.location.href = title.href;
         }, 150);
       });
     }
   });

The collector emits one ``<script>`` tag for the file at the location of ``{% collect_scripts %}``.

Module Components With Component Context
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Some components need Python logic.
The note card formats a short preview from the body when one is present.
Add a ``component.py`` next to the template.

.. code-block:: python
   :caption: notes/_components/note_card/component.py

   from notes.models import Note

   from next.components import component


   @component.context("preview")
   def preview(note: Note) -> str:
       words = note.body.split()
       return " ".join(words[:12])

The framework resolves the ``note`` parameter from the surrounding template context.
Refer to the new value in the component template.

.. code-block:: jinja
   :caption: notes/_components/note_card/component.djx

   <article class="note-card">
     <header>
       <a href="{% url 'next:page_notes_id' id=note.id %}">{{ note.title }}</a>
       <time datetime="{{ note.created_at|date:'c' }}">{{ note.created_at|date:'Y-m-d H:i' }}</time>
     </header>
     {% if preview %}<p>{{ preview }}</p>{% endif %}
   </article>

Reload ``/`` and confirm that each note shows a short preview, capped at twelve words.

Inspect the Collected Output
~~~~~~~~~~~~~~~~~~~~~~~~~~~~

View the page source in the browser to see the ``<link>`` and ``<script>`` tags the collector injected.

Checkpoint
----------

Your project tree now looks like this.

.. code-block:: text
   :caption: notes layout

   notes/
     models.py
     migrations/
     _components/
       note_card/
         component.djx
         component.py
         component.css
         component.js
     routes/
       layout.djx
       page.py
       template.djx
       notes/
         layout.djx
         [id]/
           page.py
           template.djx

The index lists notes through the ``note_card`` component.
The component carries its own template, Python context, CSS, and JS.
The layout pulls both ``{% collect_styles %}`` and ``{% collect_scripts %}`` from the static pipeline.

Common Pitfalls
---------------

Component not found.
   Confirm that the folder name and the string argument to ``{% component %}`` match exactly.
   The component name comes from the directory, not from any Python identifier.

CSS does not load.
   Make sure ``{% collect_styles %}`` sits inside ``<head>`` and that the file is named ``component.css`` next to ``component.djx``.
   The default stem registry only collects assets that match the component stem.

JavaScript loads twice.
   Two ``{% collect_scripts %}`` calls in the same template emit duplicate tags.
   Place the directive only inside the outermost layout.

Component context not resolved.
   The framework forwards the parent template scope into the component, so ``note`` must be in scope where ``{% component "note_card" %}`` is called.
   Inside a ``{% for note in notes %}`` block the variable is in scope, outside the loop it is not.

Next Steps
----------

The notes are visible but not editable.
The next part adds forms and actions for creating, editing, and deleting notes.

.. seealso::

   :doc:`tutorial04` adds forms and dispatch.
   :doc:`/content/topics/components` covers component composition and slots.
   :doc:`/content/topics/static-assets/index` covers deduplication, asset kinds, and serializers.
