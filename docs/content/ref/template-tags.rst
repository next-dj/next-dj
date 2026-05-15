.. _ref-template-tags:

Template Tags
=============

Module Summary
--------------

The framework registers its template tags as Django builtins through ``next.apps.templates.install``.
Templates therefore use them without an explicit ``{% load %}`` statement.

Forms
-----

.. describe:: {% form @action="<name>" method="post" %}...{% endform %}

   Renders a form bound to a registered action.
   Injects the CSRF token and the hidden ``_next_form_page`` origin field.
   The block body has access to the bound or unbound form through ``{{ form }}``.

   Accepts every HTML attribute as a keyword.
   Accepts captured URL parameters as ``kwargs`` (for example ``id=note.id``).
   Accepts ``form_var="other"`` to publish the form under a custom variable name.

Components
----------

.. describe:: {% component "<name>" key="value" ... %}

   Void form.
   Renders a component by name with the given literal string props.

.. describe:: {% #component "<name>" %}...{% /component %}

   Block form.
   Renders a component and substitutes child content through slots.

.. describe:: {% slot "<name>" %}{% endslot %}

   Placeholder inside a component template where caller content is substituted.

.. describe:: {% #slot "<name>" %}...{% /slot %}

   Block form for declaring slot content from the caller.

.. describe:: {% #set_slot "<name>" %}...{% /set_slot %}

   Pre defines slot content for the next ``{% component %}`` call.

Static Pipeline
---------------

.. describe:: {% collect_styles attrs="..." %}

   Emits one ``<link rel="stylesheet">`` per CSS asset collected during the request.

.. describe:: {% #collect_styles %}...{% /collect_styles %}

   Block form.
   Renders the body before the collected output.

.. describe:: {% collect_scripts attrs="..." %}

   Emits ``<script>`` and ``<script type="module">`` for every JS and module asset.

.. describe:: {% #collect_scripts %}...{% /collect_scripts %}

   Block form.
   Renders the body after the collected output.

.. describe:: {% #inline_style %}...{% /inline_style %}

   Inline ``<style>`` block.
   Participates in deduplication by content hash.

.. describe:: {% #inline_script %}...{% /inline_script %}

   Inline ``<script>`` block.
   Participates in deduplication by content hash.

.. describe:: {% collect_bucket "<name>" %}

   Emits every asset assigned to a custom bucket.
   Use for preload links, font tags, or any non standard collection.

Tag Loading
-----------

.. autofunction:: next.apps.templates.install
   :no-index:

The framework calls ``install`` during ``AppConfig.ready``.
Project code does not need to load the tag libraries manually.

See Also
--------

.. seealso::

   :doc:`/content/topics/forms/templates` for the ``{% form %}`` tag.
   :doc:`/content/topics/components` for ``{% component %}`` and slots.
   :doc:`/content/topics/static-assets/template-tags` for the static tags.
