.. _howto-share-context-across-pages:

Share Context Across Pages
==========================

Problem
-------

You want to publish a value once and read it from every page below a particular directory in the route tree.

Solution
--------

Declare the value in a ``page.py`` that sits at the root of the subtree and pass ``inherit_context=True`` to the decorator.

Walkthrough
-----------

Add the context function to the segment's ``page.py``.

.. code-block:: python
   :caption: notes/routes/page.py

   from notes.models import Note

   from next.pages import context


   @context("note_count", inherit_context=True)
   def note_count() -> int:
       return Note.objects.count()

Use the value from the layout and from any descendant page.

.. code-block:: jinja
   :caption: notes/routes/layout.djx

   <header>
     There are {{ note_count }} notes.
   </header>
   {% block template %}{% endblock template %}

The value is injected into the shared context dict for that request, so both the layout wrappers and every descendant page template can read it.

.. code-block:: jinja
   :caption: notes/routes/notes/[id]/template.djx

   <p>{{ note_count }} notes in total.</p>

Limit Inheritance to a Subtree
------------------------------

Drop the flag for values that should stay local to the current page only.

.. code-block:: python
   :caption: notes/routes/page.py (local only)

   from next.pages import context


   @context("nav_links")
   def nav_links() -> list:
       return [{"label": "Home", "href": "/"}]

Without ``inherit_context=True`` the value is available only when ``notes/routes/page.py`` handles the request directly.
Descendant routes do not receive it.

A descendant page that declares the same key with its own ``@context`` overrides the inherited value for that request.
The page's own function runs after the inherited ones, so its value wins across the whole render, layout chain included.

Verification
------------

Visit two pages under the directory that hosts the ``page.py`` and confirm the value renders on both.
Visit a page outside that directory and confirm the value is undefined.

See Also
--------

.. seealso::

   :doc:`/content/topics/context` for ``inherit_context`` rules.
   :doc:`/content/topics/layouts` for the layout chain.
