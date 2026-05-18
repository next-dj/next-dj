.. _topics-forms-templates:

Form Templates
==============

The ``{% form %}`` template tag renders a form bound to a registered action.
It produces the HTML form element, injects the CSRF token, emits the hidden ``_next_form_page`` origin field, and exposes the bound form to its block body.
This page covers every shape of the tag, the variables it publishes, and the rendering patterns for single and multi form pages.

.. contents::
   :local:
   :depth: 2

The form Tag
------------

The block form is the standard shape.

.. code-block:: jinja
   :caption: notes/routes/template.djx

   {% form @action="create_note" %}
     {{ form.title }}
     {{ form.body }}
     <button type="submit">Save</button>
   {% endform %}

The tag does six things.

1. Looks up the action name in the registry and resolves its UID.
2. Emits a ``<form method="post">`` element with ``action`` set to ``/_next/form/<uid>/``.
3. Emits a hidden ``csrfmiddlewaretoken`` input with the current CSRF token.
4. Emits a hidden ``_next_form_page`` field with the absolute path to the current ``page.py``.
5. Emits a hidden ``_next_form_origin`` field with the request path of the rendering page, consumed by ``redirect_to_origin``.
6. Publishes a ``form`` variable inside the block, either the unbound form on a GET or the bound form on a re-rendered failure.

The Action Reference
--------------------

The ``@action`` argument names the action.
It accepts either a plain name or a namespaced name.

.. code-block:: jinja
   :caption: plain vs namespaced

   {% form @action="create_note" %}...{% endform %}
   {% form @action="notes:save" %}...{% endform %}

An opening tag without an ``@action`` argument raises ``TemplateSyntaxError`` at parse time.
A name that is not in the registry resolves to an empty ``action`` attribute rather than raising.

Render-Time Failures
--------------------

The tag raises ``ImproperlyConfigured`` at render time in two cases.

Missing ``request``.
   The tag reads ``request`` from the template context to build the CSRF token and the hidden fields.
   Add ``django.template.context_processors.request`` to ``TEMPLATES[*].OPTIONS.context_processors`` so the request reaches the context.

Missing ``current_page_module_path``.
   The tag reads ``current_page_module_path`` to emit the ``_next_form_page`` origin field.
   The file router sets this variable on every rendered page.
   Render the form through the file router rather than a hand-built view so the variable is present.

The ``next.E019`` system check, described in :doc:`/content/security/csrf-and-forms`, catches the missing context processor before a request reaches the tag.

Class and Extra Attributes
--------------------------

Every ``key=value`` pair on the opening tag other than ``@action`` and ``method`` becomes a literal attribute on the ``<form>`` element.
The tag does not interpret extra pairs as URL parameters.

.. code-block:: jinja
   :caption: tag attributes

   {% form @action="upload" enctype="multipart/form-data" class="card" %}
     {{ form.file }}
     <button type="submit">Upload</button>
   {% endform %}

The dispatcher always processes POST submissions and the tag always renders ``method="post"``.
A ``method`` argument on the tag is ignored.

Captured URL Parameters
-----------------------

The tag does not need any argument to forward captured URL parameters.
When the page URL captures a parameter the tag emits a hidden ``_url_param_<name>`` field for every captured kwarg in ``request.resolver_match.kwargs``, skipping the dispatch ``uid`` and any name reserved by the dependency resolver.

.. code-block:: jinja
   :caption: notes/routes/notes/[id]/template.djx

   {% form @action="update_note" %}
     {{ form.title }}
     <button type="submit">Save</button>
   {% endform %}

A page whose URL captures ``id`` therefore posts a hidden ``_url_param_id`` field automatically.
The handler receives the same value through ``DUrl["id", int]`` or any other URL marker.

The form Variable
-----------------

The block body has access to a variable named ``form``.
The framework decides what to publish based on the request lifecycle.

Initial render on GET.
   The variable is an unbound form.
   The framework calls ``get_initial`` through the dependency resolver, then constructs the form from the returned initial data or model instance.

Re-rendered page after a failing POST.
   The variable is the bound form with errors.
   The template renders the user input plus any field errors.

A page can override the default by publishing its own ``form`` context.

.. code-block:: python
   :caption: pre filled form

   from notes.forms import NoteForm
   from notes.models import Note

   from next.pages import context


   @context("form")
   def edit_form(note: Note) -> NoteForm:
       return NoteForm(instance=note)

The dispatcher reuses the same ``form`` key for the re-rendered failure case.
The template therefore does not need to branch on bound vs unbound.

Multiple Forms on One Page
--------------------------

Each call to ``{% form %}`` references a different action.
The dispatcher routes submissions through the URL alone, so different forms do not interfere with each other.

.. code-block:: jinja
   :caption: index with search and create

   {% form @action="search" class="search-form" %}
     {{ form.query }}
     <button type="submit">Search</button>
   {% endform %}

   {% form @action="create_note" class="create-form" %}
     {{ form.title }}
     {{ form.body }}
     <button type="submit">Create</button>
   {% endform %}

A page can also publish multiple bound forms by registering several ``@context("...")`` functions with distinct keys.
The block always publishes the bound form under the name ``form``, so each ``{% form %}`` block sees its own form even when two blocks render on the same page.

Manual CSRF
-----------

The tag emits the hidden ``csrfmiddlewaretoken`` input automatically.
Add Django's :doc:`{% csrf_token %} <django:ref/csrf>` manually only when you build the form element without the tag, for example in a plain ``<form>``.
Even then, the dispatcher still requires the hidden ``_next_form_page`` field so a hand crafted form must include it.

.. code-block:: jinja
   :caption: hand crafted form

   <form action="/_next/form/{{ action_uid }}/" method="post">
     {% csrf_token %}
     <input type="hidden" name="_next_form_page" value="{{ current_page_module_path }}">
     <button type="submit">Send</button>
   </form>

The ``current_page_module_path`` variable is published by the framework on every rendered page.

Rendering Field Errors
----------------------

Errors live on the bound form.
Render them inline with each field or as a list at the top of the form.

.. code-block:: jinja
   :caption: inline errors

   {% form @action="create_note" %}
     <div>
       {{ form.title }}
       {% if form.title.errors %}
         <p class="error">{{ form.title.errors|first }}</p>
       {% endif %}
     </div>
     <button type="submit">Save</button>
   {% endform %}

.. code-block:: jinja
   :caption: error list

   {% form @action="create_note" %}
     {% if form.errors %}
       <ul class="errors">
         {% for field, messages in form.errors.items %}
           {% for message in messages %}<li>{{ field }} {{ message }}</li>{% endfor %}
         {% endfor %}
       </ul>
     {% endif %}
     {{ form.title }}
     {{ form.body }}
     <button type="submit">Save</button>
   {% endform %}

Empty Form Bodies
-----------------

A tag block with only a submit button is the confirmation pattern covered in :ref:`Actions Without form_class <topics-forms-actions>`.

Common Patterns
---------------

Form in a Component
~~~~~~~~~~~~~~~~~~~

A component template can host a ``{% form %}`` tag.
The framework injects the same ``current_page_module_path`` because the surrounding page provides it.

Form in a Layout
~~~~~~~~~~~~~~~~

Layouts also receive ``current_page_module_path``.
A login form rendered from the root layout therefore re-renders the original page on validation failure instead of dropping back to the layout.

Inline Form with Plain HTML
~~~~~~~~~~~~~~~~~~~~~~~~~~~

Use plain HTML when you need full control over markup or aria attributes.
Include both ``{% csrf_token %}`` and the hidden ``_next_form_page`` field to keep the dispatcher happy.

See Also
--------

.. seealso::

   :doc:`actions` for the handler side of the contract.
   :doc:`validation-rerender` for what runs after a failing submission.
   :doc:`/content/ref/template-tags` for every template tag the framework registers.
