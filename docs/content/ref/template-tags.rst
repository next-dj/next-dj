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

.. describe:: {% slot "<name>" %}

   Void form.
   Fills a named slot from the caller inside a ``{% #component %}`` block.

.. describe:: {% #slot "<name>" %}...{% /slot %}

   Block form.
   Fills a named slot with body content from the caller inside a ``{% #component %}`` block.

.. describe:: {% set_slot "<name>" %}

   Void form.
   Marks a slot location inside a component template, with no default body.

.. describe:: {% #set_slot "<name>" %}...{% /set_slot %}

   Block form.
   Marks a slot location inside a component template, with a fallback body used when the caller omits the slot.

Static Pipeline
---------------

.. describe:: {% collect_styles %}

   Marks the placeholder slot where collected CSS link tags are injected.
   Takes no arguments.

.. describe:: {% collect_scripts %}

   Marks the placeholder slot where collected JS and module tags are injected.
   Takes no arguments.

.. describe:: {% use_style "<url>" %}

   Registers an external CSS URL on the active collector.
   The asset is prepended so shared dependencies load before co-located styles.

.. describe:: {% use_script "<url>" %}

   Registers an external JS URL on the active collector.
   The asset is prepended the same way as ``use_style``.

.. describe:: {% #use_style %}...{% /use_style %}

   Inline CSS block.
   The body is rendered with the template context and deduplicated by content.

.. describe:: {% #use_script %}...{% /use_script %}

   Inline JS block.
   The body is rendered with the template context and deduplicated by content.

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
