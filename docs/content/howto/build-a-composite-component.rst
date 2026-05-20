.. _howto-composite-component:

Build a Composite Component
===========================

Problem
-------

You want a reusable card component that wraps caller-provided content through a named slot and exposes a computed property to its template.

Solution
--------

Create a component folder under the components root with three files.
``component.djx`` holds the markup with a ``{% set_slot %}`` placeholder.
``component.py`` declares context functions and computed values.
``component.css`` ships the style sheet.

Walkthrough
-----------

Create the folder ``notes/_components/info_card/`` with three files.

.. code-block:: jinja
   :caption: notes/_components/info_card/component.djx

   <article class="info-card">
     <header class="info-card__head">
       <h3>{{ title }}</h3>
       {% if subtitle %}<small>{{ subtitle }}</small>{% endif %}
     </header>
     <div class="info-card__body">
       {% set_slot "content" %}
     </div>
   </article>

.. code-block:: python
   :caption: notes/_components/info_card/component.py

   from next.components import component

   @component.context("subtitle")
   def subtitle(subtitle: str = "") -> str:
       return subtitle.strip()

The parameter and the published key share the name ``subtitle`` on purpose.
The function reads its own ``subtitle`` prop through dependency injection and republishes the cleaned value under the same key, so the trimmed value replaces the raw prop and the template sees the trimmed string.

.. code-block:: css
   :caption: notes/_components/info_card/component.css

   .info-card {
     border: 1px solid #ddd;
     border-radius: 8px;
     padding: 1rem;
   }
   .info-card__head { margin-bottom: 0.5rem; }

Use the Component
~~~~~~~~~~~~~~~~~

Call the component in block form and fill the slot.

.. code-block:: jinja
   :caption: notes/pages/template.djx

   {% #component "info_card" title="Quick start" subtitle="Read this first" %}
     {% #slot "content" %}
       <p>Notes is a tiny example application.</p>
     {% /slot %}
   {% /component %}

The framework substitutes the slot content into the component template.

Verification
------------

Open the page and confirm the card renders with the slot content.
View the HTML source and confirm a ``<link>`` to ``info_card/component.css`` appears in ``<head>``.

See Also
--------

.. seealso::

   :doc:`/content/topics/components` for the component lifecycle.
   :doc:`/content/topics/static-assets/co-located-files` for the asset conventions.
