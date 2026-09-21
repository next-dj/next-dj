.. _howto-composite-component:

Build a composite component
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

Create the folder ``notes/pages/_components/info_card/`` with three files.

.. code-block:: jinja
   :caption: notes/pages/_components/info_card/component.djx

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
   :caption: notes/pages/_components/info_card/component.py

   from django.http import HttpRequest

   from next import component

   @component.context("subtitle")
   def subtitle(subtitle: str = "") -> str:
       return subtitle.strip()

   @component.context("is_staff")
   def is_staff(request: HttpRequest) -> bool:
       return request.user.is_staff

The parameter and the published key share the name ``subtitle`` on purpose.
The function reads its own ``subtitle`` prop through dependency injection and republishes the cleaned value under the same key, so the trimmed value replaces the raw prop and the template sees the trimmed string.

The second callable takes a parameter no prop supplies.
``request: HttpRequest`` is filled from the page render around the component, which is the same resolver that fills a page context callable, so a component reads URL segments, query parameters, and named dependencies the same way a ``page.py`` does.
See :doc:`/content/topics/dependency-injection` for the providers and what each one matches.

The module carries no ``from __future__ import annotations``.
The resolver reads the annotations of each callable at runtime, and the future import turns every one of them into a string whose names still have to be importable at that point, so a type held behind ``if TYPE_CHECKING`` stops matching any provider and the parameter arrives as ``None``.
A ``component.py`` keeps its annotations real for that reason.

.. code-block:: css
   :caption: notes/pages/_components/info_card/component.css

   .info-card {
     border: 1px solid #ddd;
     border-radius: 8px;
     padding: 1rem;
   }
   .info-card__head { margin-bottom: 0.5rem; }

Use the component
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

The ``render`` function alternative
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

A ``component.py`` that defines a ``render`` function returns the component body itself and the template is never read.
The function takes DI parameters the same way, and it takes over completely, so neither ``component.djx`` nor any ``@component.context`` callable of the module runs for that component.
Reach for it when the body is computed rather than written, for example a server-side gate that returns the empty string, and stay on the template whenever there is markup to keep in a template.
:ref:`components-render-function` covers the return types it accepts.

Verification
------------

Open the page and confirm the card renders with the slot content.
View the HTML source and confirm a ``<link>`` to ``/static/next/components/info_card.css`` appears in ``<head>``, because a component asset is named after the component rather than after the file stem.

See also
--------

.. seealso::

   :doc:`/content/topics/components` for the component lifecycle.
   :doc:`/content/topics/dependency-injection` for the parameters a context callable may declare.
   :doc:`/content/topics/static-assets/co-located-files` for the asset conventions.
