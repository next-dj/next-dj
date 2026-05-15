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

   {% form @action="create_note" method="post" %}
     {{ form.title }}
     {{ form.body }}
     <button type="submit">Save</button>
   {% endform %}

The tag does five things.

1. Looks up the action name in the registry and resolves its UID.
2. Emits a ``<form>`` element with ``action`` set to ``/_next/form/<uid>/``.
3. Injects a CSRF token through ``{% csrf_token %}``.
4. Emits a hidden ``_next_form_page`` field with the absolute path to the current ``page.py``.
5. Publishes a ``form`` variable inside the block, either the unbound form on a GET or the bound form on a re-rendered failure.

The Action Reference
--------------------

The ``@action`` argument names the action.
It accepts either a plain name or a namespaced name.

.. code-block:: jinja
   :caption: plain vs namespaced

   {% form @action="create_note" %}...{% endform %}
   {% form @action="notes:save" %}...{% endform %}

A name that does not exist in the registry raises ``TemplateSyntaxError`` at render time.
Templates therefore catch typos immediately during development.

Method, Class, and Extra Attributes
-----------------------------------

Every keyword on the opening tag becomes an attribute on the ``<form>`` element.

.. code-block:: jinja
   :caption: tag attributes

   {% form @action="upload" method="post" enctype="multipart/form-data" class="card" %}
     {{ form.file }}
     <button type="submit">Upload</button>
   {% endform %}

The dispatcher always processes POST submissions.
Set ``method="post"`` explicitly to keep the rendered HTML self documenting.

Captured URL Parameters
-----------------------

Pass captured URL parameters to the tag when the handler needs them.

.. code-block:: jinja
   :caption: notes/routes/notes/[id]/template.djx

   {% form @action="update_note" method="post" id=note.id %}
     {{ form.title }}
     <button type="submit">Save</button>
   {% endform %}

The ``id=note.id`` argument is encoded into the dispatch URL.
The handler receives the same value through ``DUrl[int]`` or any other URL marker.

The form Variable
-----------------

The block body has access to a variable named ``form``.
The framework decides what to publish based on the request lifecycle.

Initial render on GET.
   The variable is an unbound form created by ``form_class.get_initial(request, **kwargs)``.

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
   :caption: index with create and search

   {% form @action="search" method="get" class="search-form" %}
     {{ form.query }}
     <button type="submit">Search</button>
   {% endform %}

   {% form @action="create_note" method="post" class="create-form" %}
     {{ form.title }}
     {{ form.body }}
     <button type="submit">Create</button>
   {% endform %}

A page can also publish multiple bound forms by registering several ``@context("...")`` functions with distinct keys.

Custom Variable Names
~~~~~~~~~~~~~~~~~~~~~

The block accepts a ``form_var`` argument to rename the published variable.
Use it when two forms render in the same block.

.. code-block:: jinja
   :caption: side by side forms

   {% form @action="search" form_var="search_form" %}
     {{ search_form.query }}
   {% endform %}

   {% form @action="create_note" form_var="create_form" %}
     {{ create_form.title }}
   {% endform %}

Manual CSRF
-----------

The tag injects ``{% csrf_token %}`` automatically.
Add it manually only when you build the form element without the tag, for example in a plain ``<form>``.
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

   {% form @action="create_note" method="post" %}
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

   {% form @action="create_note" method="post" %}
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

A tag block can be empty when the action does not need form fields.

.. code-block:: jinja
   :caption: confirmation button

   {% form @action="delete_note" method="post" id=note.id %}
     <button type="submit" class="danger">Delete</button>
   {% endform %}

The submission still triggers the dispatcher and runs the handler.

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
