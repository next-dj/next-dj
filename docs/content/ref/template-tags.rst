.. _ref-template-tags:

Template Tags
=============

Module Summary
--------------

The framework registers its template tags as Django builtins through ``next.apps.templates.install``.
Templates therefore use them without an explicit ``{% load %}`` statement.

Forms
-----

.. describe:: {% form "<name>" %}...{% endform %}

   Renders a form bound to a registered action.
   Takes exactly one positional argument: the quoted action name string.
   Injects the CSRF token and the hidden ``_next_form_page`` origin field.
   The block body has access to the bound or unbound form through ``{{ form }}``.

   The HTTP method is always ``post``. It cannot be passed as an argument.
   HTML attributes such as ``enctype`` cannot be passed as tag keywords.
   Set them on a wrapping ``<form>`` element if you need attributes the tag does not emit.

   Captured URL parameters from ``request.resolver_match.kwargs`` are emitted automatically as ``_url_param_<name>`` hidden inputs.

   The tag requires ``request`` in the template context for the CSRF token.
   It also uses ``current_page_module_path`` when present to scope the action lookup to the origin page, which is how the file router renders it.
   That context value is not strictly required: when it is absent the action lookup falls back to the name index.

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

Multiline tag bodies
~~~~~~~~~~~~~~~~~~~~

The framework reinstalls Django's template tag pattern with the ``re.DOTALL`` flag so a single ``{% ... %}`` token may span several lines.
That allows readable block components and slots when the inner markup is long.

.. caution::

   This changes template parsing for **every** template the process loads, not only DJX files.
   If you rely on Django's stock behaviour where a newline inside ``{% ... %}`` ends the tag, adjust those templates before adopting next.dj.

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

Layouts
-------

.. describe:: {% block template %}{% endblock %}

   Marks the slot inside a ``layout.djx`` where the page template is composed.
   The layout loader replaces the empty block with the wrapped page body when it builds the final template string.
   Both ``{% endblock %}`` and ``{% endblock template %}`` are accepted as the closing tag.

   A ``layout.djx`` without this block raises ``next.W001`` during ``manage.py check``, since the page body would have nowhere to render.
   Nested layouts each carry their own ``{% block template %}`` and compose from innermost to outermost.

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
