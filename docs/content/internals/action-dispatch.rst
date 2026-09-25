.. _internals-action-dispatch:

Action dispatch
===============

This page covers the form dispatch pipeline.
It traces a submission from the template ``{% form %}`` tag through the validation chain to the handler and the re-render path.

.. contents::
   :local:
   :depth: 2

Overview
--------

The dispatcher runs at ``/_next/form/<uid>/`` where the UID is the first 16 hex characters of a SHA-256 digest of the scope key and the action name.
The dispatcher loads the action handler, enforces the declared access guard and the authorization of the origin page, builds the form, runs the validation chain, and either calls the handler or re-renders the origin page.
A verb outside GET and POST is refused with HTTP 405 before the UID is looked up.
A GET reaches the lookup, so an unknown UID answers HTTP 404 and a registered one answers HTTP 405.
The UID is an address rather than a secret, derived from public inputs and rendered into every page that carries the form, so knowing one grants nothing and the guards described below are the whole access boundary, see :doc:`/content/security/overview`.

Pipeline
--------

.. mermaid::

   flowchart TB
       Template["form tag in template"] --> Endpoint["form dispatch endpoint"]
       Endpoint -- "not GET or POST" --> NotAllowed["HTTP 405"]
       Endpoint --> Lookup["Resolve action by UID"]
       Lookup -- unknown UID --> NotFound["HTTP 404"]
       Lookup -- "found, non-POST" --> NotAllowed
       Lookup -- "found, POST" --> Guard{"Static access guard"}
       Guard -- anonymous --> LoginRedirect["HTTP 302 to LOGIN_URL"]
       Guard -- "missing permission" --> Forbidden["HTTP 403"]
       Guard -- pass --> PageAuth{"Origin page authorizes"}
       PageAuth -- denies --> PageDenied["Origin page short-circuit response"]
       PageAuth -- "allows, no form_class" --> HandlerOnly["Run handler only"]
       PageAuth -- "allows, form_class" --> ViewHook{"check_permissions hook"}
       PageAuth -- "allows, wizard_class" --> WizardOrigin{"Origin resolves"}
       WizardOrigin -- no --> BadRequest["HTTP 400"]
       WizardOrigin -- yes --> ViewHook
       ViewHook -- "deny" --> HookDenied["HTTP 403 or response"]
       ViewHook -- "deny" --> AccessDenied["form_access_denied signal"]
       ViewHook -- "allow, form_class" --> Build["Build form"]
       ViewHook -- "allow, wizard_class" --> WizardStep["Bind current wizard step"]
       HandlerOnly --> HandlerOnlyResponse["Handler response or HTTP 204"]
       HandlerOnly --> ActionDispatched["action_dispatched signal"]
       Build --> ObjectHook{"has_object_permission hook"}
       WizardStep --> ObjectHook
       ObjectHook -- "deny" --> ObjectDenied["HTTP 403 or response"]
       ObjectHook -- "deny" --> AccessDenied
       ObjectHook -- allow --> ValidateOnly{"Validate-only intent"}
       ValidateOnly -- yes --> ValidateEnvelope["Validation envelope, no handler"]
       ValidateOnly -- "no, form_class" --> Validate{"Form valid"}
       ValidateOnly -- "no, wizard_class" --> WizardValid{"Step valid"}
       Validate -- yes --> Handler["Run handler"]
       Handler --> Response["Handler response"]
       Handler --> ActionDispatched
       Validate -- no --> Origin{"Origin resolves"}
       Origin -- no --> BadRequest["HTTP 400"]
       Origin -- yes --> ShareCache["Reuse dep cache on request"]
       ShareCache --> RenderOrigin["Render origin page"]
       RenderOrigin --> RerenderHTML["HTTP 200 with bound form"]
       Validate -- no --> FormFailed["form_validation_failed signal"]
       WizardValid -- no --> Origin
       WizardValid -- no --> FormFailed
       WizardValid -- yes --> SaveStep["Save step draft"]
       SaveStep --> StepSubmitted["wizard_step_submitted signal"]
       SaveStep --> StepsLeft{"Steps remaining"}
       StepsLeft -- yes --> Advance["HTTP 302 to next step"]
       StepsLeft -- no --> Done["Run done with merged data"]
       Done -- "status < 400" --> Completed["Clear drafts, wizard_completed signal"]
       Done -- "status >= 400" --> KeepDrafts["Drafts kept for retry"]
       Advance --> ActionDispatched
       Done --> ActionDispatched

Modules
-------

``next.forms.decorators``.
   ``@action`` decorator implementation.
   An ``@action`` may also live in a ``component.py``, which the components backend imports during component discovery, so the action registry is populated before the first request regardless of where the decorator runs.
   See :doc:`/content/internals/component-pipeline` for the discovery walk.

``next.forms.manager``.
   ``FormActionManager`` aggregates the configured backends and yields their URL patterns.
   The per-action registry lives on each ``RegistryFormActionBackend``, not on the manager.

``next.forms.dispatch``.
   ``FormActionDispatch`` runs the pipeline per request.
   Manages the bound form, the dependency cache reuse, and the response selection.
   It is also the sender of ``form_access_denied``, ``action_dispatched``, and ``form_validation_failed``.

``next.forms.dispatch.build``, ``next.forms.dispatch.permissions``, ``next.forms.dispatch.responses``, ``next.forms.dispatch.wizard``.
   The pipeline bodies behind ``FormActionDispatch``, split by concern.
   Form construction and hook invocation, guard and permission enforcement, outcome types and response coercion, and wizard step dispatch.

``next.forms.backends``.
   ``FormActionBackend`` abstract contract and ``RegistryFormActionBackend`` default implementation.
   Turning the configured entries into instances is not the module's job.
   ``FormActionManager`` delegates that to the shared ``load_backends`` helper every backend family uses.

``next.forms.errors``.
   ``FormActionNotFoundError``, ``UnstorableWizardValueError``, and ``UnregisteredComponentError``, re-exported from the package.

``next.forms.base``.
   The form base classes, the ``__init_subclass__`` auto-registration gate, and the permission-hook presence flags the dispatcher reads.

``next.forms.checks``.
   A package of one-word submodules, ``actions``, ``config``, ``sources``, ``widgets``, and ``wizards``, holding the system checks that read the registration diagnostics and walk every configured backend.

``next.forms.uid``.
   ``redirect_to_origin``, ``redirect_or_fallback``, ``reverse_form_action``, ``current_origin_path``, ``is_path_only``, ``validated_origin_path``, and ``posted_origin_path`` helpers for the origin page round trip, plus the ``MAX_ORIGIN_LENGTH`` cap, the ``ORIGIN_FIELD_NAME`` wire constant, and the ``FORM_ORIGIN_OVERRIDE_KEY`` render-context key the partial shaping layer sets on a wizard advance.

``next.forms.origin``.
   Resolution of the posted origin path into the page module and the typed URL kwargs, memoised per request.

``next.forms.wizard``.
   ``FormWizard`` base class, the ``FormWizardBackend`` contract with the session and cache implementations, and the ``wizard_backend_manager`` holder.

``next.forms.widgets``.
   ``ComponentWidget`` and the ``bind_component_widgets`` binder the ``{% form %}`` tag calls before rendering.

``next.forms.markers``.
   ``DForm`` annotation plus the ``FormProvider`` and ``CleanedDataProvider`` classes.

``next.forms.nodes``.
   ``FormNode``, the template node the ``{% form %}`` tag compiles to, which lives beside the forms area so the partial checks can walk it without importing a tag library.

``next.forms.registration``.
   ``RegistrationDiagnostics`` buffers that the registration paths fill and the system checks read.

``next.forms.serializers``.
   ``FormSpec``, ``FormsetSpec``, ``FormsetRowSpec``, ``FormSectionSpec``, ``FieldSpec`` plus the builders ``form_spec``, ``formset_spec``, ``field_spec``.

``next.forms.formsets``.
   ``cleanup_extra_initial`` helper for blank extra rows.

``next.partial.shaping``.
   The package behind the bound shaper, which turns a dispatch outcome into a patch envelope for a partial request, see `Partial shaping`_.

``next.ports``.
   The ``PartialShaper`` protocol and the ``partial_shaper_slot`` the app config binds at startup.
   The dispatch pipeline asks the slot whether a request is partial and hands it the outcome, so the forms path never imports ``next.partial``.

Access guard
------------

An action that declares ``login_required`` or ``permission_required`` carries an ``ActionGuard`` in its registry metadata under the ``guard`` key.
The shared pipeline enforces this static guard right after origin resolution and ahead of the page authorization, ``get_initial``, and form binding, so no application code or database access runs for a request the static guard denies.
An anonymous user receives a redirect to ``LOGIN_URL`` whose ``next`` is the validated posted origin, and an authenticated user missing a permission raises ``PermissionDenied``.
Every backend that delegates to ``FormActionDispatch.dispatch`` inherits the enforcement.

Origin page authorization
-------------------------

The action endpoint is not the page URL, so the origin page's own view never runs on a submission and the pipeline asks that page directly instead.
Immediately after ``resolve_origin``, and before a handler, a form, or a permission hook runs, the dispatcher calls ``authorization_outcome`` on the resolved page path with the posted origin URL and the typed URL kwargs of the origin.
The call runs the page's ``render()`` under the same injection the routed view uses, so a response it returns becomes the answer to the POST verbatim, while the body it returns is discarded because every render site behind the boundary composes from ``composed_template_for``.
A page with no ``render()`` authorizes every submission, exactly as its own view serves every visitor.

One call at the boundary covers every render site behind it, and two authorizations live outside it.
A wizard step advance authorizes the page of the next step before rendering that step's zone, and ``Patches.morph_zone`` carries its own check because it is public API a handler reaches with a posted origin behind it.
A backend whose ``dispatch`` drives the pipeline by hand rather than delegating to ``FormActionDispatch.dispatch`` runs none of this.

The ``render()`` under this call does not see the POST to ``/_next/form/<uid>/``.
``next.pages.visits.visit_request`` copies the live request and restates it as a GET of the URL being authorized, rewriting ``method``, ``path``, ``path_info``, ``GET``, ``POST``, ``META`` and ``resolver_match`` while carrying over everything a middleware attached, the user and the session included.
It drops the dispatch dependency cache from the copy, and the guard resolves ``render()`` with a fresh cache, so no value the dispatch resolved reaches the answer.
The copy leaves the live request untouched, so the dispatcher reads its own POST afterwards as it always did.
A page that short-circuits on the request method, on a query parameter, or on a canonical-URL comparison therefore answers a submission exactly as it answers a visit.

Each of the four authorization sites names the URL it asks about, the posted origin for a submission and for ``Patches.morph_zone``, the next step's URL for a wizard advance.
``Patches.morph(zone=..., page=...)`` names one only when the caller addressed the foreign page by URL, and a caller addressing it by file path passes ``None``, which still asks as a GET but leaves the live path in place.

Dynamic permission hooks
------------------------

Two opt-in hooks layer per-request permission decisions on top of the static guard, and unlike the static guard they intentionally run application code.
A request the static guard denies never reaches either hook.

``check_permissions`` is a view-level classmethod.
The pipeline resolves it after origin resolution and the dependency-cache install, and after ``_resolve_form_class`` returns, so the hook reads off the resolved class and a factory ``form_class`` is covered.
It runs before ``get_initial`` and form binding.

``has_object_permission`` is an object-level instance method.
The pipeline resolves it after the form binds and before ``is_valid`` and the handler, so ``self.instance`` is the loaded target on a ``ModelForm``.
A denial here returns a bare HTTP 403 rather than re-rendering, because the form already bound.

Both hooks are dependency-injected through ``resolver.resolve_dependencies`` with the per-request ``dep_cache`` and ``dep_stack`` the dispatcher publishes under ``REQUEST_DEP_CACHE_ATTR``, so a provider resolved in a hook is shared with ``get_initial`` and ``on_valid``.
A return of ``None`` or ``True`` allows, ``False`` raises ``PermissionDenied``, an ``HttpResponse`` short-circuits, and any other type raises ``TypeError``.
On a denial the dispatcher emits ``form_access_denied`` when a receiver is connected.
A wizard enforces ``check_permissions`` once per step POST before the step binds, and the step form's ``has_object_permission`` is enforced per step after the step form binds and before ``is_valid``.
A wizard step binds without ``get_initial`` or ``Meta.instance_from_url``, so a ``ModelForm`` step reads an unbound ``self.instance`` in that hook, not the URL-addressed target the standalone path loads.
The guide covers the authoring contract at :ref:`topics-forms-actions-dynamic-guards`.

Validate-only short circuit
---------------------------

A partial request whose intent carries validate fields short-circuits the pipeline on the already bound form.
The branch fires in the form path and in the wizard step path only after the static guard, the ``check_permissions`` hook, the form binding, and the ``has_object_permission`` hook have all passed, so a guarded validator is never an anonymous oracle.
The dispatcher answers with the validation envelope, the handler never runs, the success signals never fire, and wizard storage stays untouched.
A request without validate fields falls through to the normal submit path.
See :doc:`/content/topics/partial-rendering/scenarios` for the client-side flow.

Origin resolution
-----------------

The hidden ``_next_form_origin`` field on every rendered form carries the URL path of the origin page.
At dispatch the field is validated as a same-site path, the script prefix from :func:`django.urls.get_script_prefix` is stripped, and the remainder is resolved through :func:`django.urls.resolve` with the per-request URLconf from ``request.urlconf`` when one is set.
The resolved match yields two things.
The typed URL kwargs come through the real URL converters, and the origin page source comes from the ``next_page_path`` attribute that the file router sets on every routed view, including the synthesised ``page.py`` location of virtual ``template.djx`` routes.
The result is memoised on the request, because the invalid re-render reads it from the dispatcher and from every ``{% form %}`` tag on the page.

A missing field, an off-site value, a path that does not resolve, or a resolved view without ``next_page_path`` all yield no origin match.
The invalid branch then returns ``HTTP 400 Missing or invalid _next_form_origin``.
A hand-written view opts into re-rendering by carrying its own ``next_page_path`` attribute, see :ref:`topics-forms-templates-handwritten-views`.

Backends
--------

The ``FORM_ACTION_BACKENDS`` setting lists the active backends.
Each backend is a full implementation of the ``FormActionBackend`` contract, not a step in a middleware chain.
A backend owns the registry, the URL generation, and the dispatch for every action it registers.

The default value registers ``RegistryFormActionBackend``.
Its ``dispatch`` method resolves the UID to an action and forwards the request to ``FormActionDispatch``, which resolves the posted origin, has that page authorize the request, builds the form, and runs the validation chain.

A project customises dispatch by subclassing ``RegistryFormActionBackend`` and overriding ``dispatch``.
The override calls ``super().dispatch`` to keep the standard pipeline.

.. _internals-action-dispatch-shaping:

Partial shaping
---------------

``next.partial.shaping`` is what the last step of dispatch becomes when the request is a partial one.
The base ``shape_response`` asks ``partial_shaper_slot`` about the request and hands a partial outcome to the bound shaper, which composes a patch envelope instead of a full page.
The package splits into one-word submodules, ``outcomes`` for the routing of an outcome to its envelope, ``validate`` for the validate-only pass, ``scrub`` for error cleaning, ``targets`` for origin and zone resolution, ``csrf`` for the rotation marker, and ``responses`` for the serialisation.

Outcome taxonomy
~~~~~~~~~~~~~~~~

Four shapes cover every partial answer, the three ``ActionOutcomeKind`` members plus the validate-only pass that short-circuits ahead of them.

``INVALID``.
   The failed form comes back as a morph.
   A zone the request names and the origin page declares re-renders with the bound form in the overrides, and without such a zone the envelope morphs the form by uid and asks the client to trim it out of the rendered page.
   The response carries ``X-Next-Form: invalid`` and ``X-Next-Action: <uid>`` alongside the envelope, and the machine-readable form meta rides in the patches.

``WIZARD_ADVANCE``.
   A step advance is a master-zone morph, never a redirect.
   An outcome with no target answers HTTP 204, and a wizard the shaper cannot resolve to a page falls back to a plain redirect, as does a next step whose page refuses to serve the request.
   The resolved case renders the next step's zone directly, overriding the hidden origin field with the next step URL, and pushes that URL to history only when the wizard opts in.

``RESULT``.
   A handler response that is already a patch envelope passes through untouched.
   A redirect becomes a ``visit`` patch, marked for full navigation when the target is not same-host.
   A ``None`` result runs the success funnel, which morphs the form or its zone in place and drains the pending ``contrib.messages`` into toast patches.
   Anything else falls back to the default full-page envelope.

Validate-only.
   The pass binds and validates the form, scrubs the errors, morphs the same target the invalid branch would, attaches the form meta, and sends ``field_validated``.
   No handler runs and no wizard storage is touched.

Error scrubbing
~~~~~~~~~~~~~~~

A validate pass answers for the fields it named and nothing else.
The scrubber keeps only the errors of the requested fields and deletes the rest off the bound form, so a blur on one field never surfaces an error the visitor has not reached yet.
Non-field errors follow the submit rather than the blur, so a ``clean()`` result is dropped from a validate pass, and a formset loses the non-form errors a cross-member clean produced.
File fields drop out of the requested set before the walk, because a multipart upload is never replayed on a blur, so naming one from the client cannot make the server answer for it.
A formset is scrubbed member by member, and every error name travels under the prefix of its row, the same wire name the client reads.

CSRF rotation
~~~~~~~~~~~~~

The rotation marker Django sets on ``request.META`` is read before any re-render runs.
A login on submit rotates the token, and reading the marker after a render would flag every response instead of the one that rotated.
When the marker is up, the fresh CSRF payload is stamped into the envelope, so the client runtime replaces the token it holds without a full navigation.

Target resolution
~~~~~~~~~~~~~~~~~

The origin target is the page path and URL kwargs of the resolved posted origin, shared by the validate pass and the success funnel so both work off one resolution.
The zone is the first name the partial intent asks for that the origin page actually declares, and a request naming no such zone falls through to the form-by-uid morph.
A wizard step target resolves the next step URL through the same URLconf without running that page's view, keeping the captured kwargs unfiltered so the step renders every parameter it declares.
The step page authorizes the advance before its zone renders, with the guard reading the DI-safe subset of those kwargs, and a denial answers with the plain step redirect a client without the runtime follows anyway, see :doc:`/content/topics/forms/wizard`.

Shared dependency cache
-----------------------

The dispatcher creates a fresh dependency cache on every POST and shares it across each stage of the dispatch.
``get_initial``, the factory resolution, the handler call, and any re-render after validation failure, its ``@context``, metadata, and component callables included, all read and write the same cache.
The authorization of the origin and of a wizard step stays outside it, since the guard resolves ``render()`` against a fresh cache.
Two consequences flow from this.

- Custom providers are idempotent across the dispatch stages.
- Re-render after a validation failure is cheap because layouts and context functions reuse the values cached during the initial bind.

The cache hangs on ``request`` under the attribute named ``REQUEST_DEP_CACHE_ATTR``.
Read it through ``next.deps.get_request_dep_cache(request)`` rather than the raw attribute.

The origin-page re-render also reuses the compiled page template.
The page manager caches the composed template source and its compiled ``Template`` keyed by source mtime, so a warm re-render performs no file reads and no template parsing.

Signals
-------

Eight signals fire.
``action_registered`` fires at import time, five fire per request, and the two backend-load signals fire when the settings-driven backends are built.

- ``action_registered`` fires at import time, once per registration when the registry stores the action target.
  The target is a handler, a form class, or a wizard class.
- ``form_validation_failed`` fires at request time, once per failing submission, including a failing wizard step.
- ``action_dispatched`` fires at request time, once per successful handler invocation and once per valid wizard step, with the action name, the action uid, the live request, the bound form (``None`` for form-less actions), the URL kwargs, the handler duration, the response status, and the dispatch dependency cache in the payload.
  A wizard step advance runs no handler and reports ``duration_ms`` as ``0.0``.
- ``wizard_step_submitted`` fires at request time after a wizard step validates, with the wizard class as the sender and the step name plus a copy of its cleaned data in the payload.
- ``wizard_completed`` fires at request time after the wizard ``done`` method returns a response below HTTP 400, with the wizard class as the sender and the merged cleaned data in the payload.
  An error response from ``done`` skips the signal and keeps the saved drafts.
- ``form_access_denied`` fires at request time when the origin page or a dynamic permission hook denies a request, never on the static guard path, with the action name, the action uid, the live request, the ``layer`` (``"page"``, ``"view"``, or ``"object"``), and the ``reason`` (``"raised"``, ``"denied"``, or ``"response"``) in the payload.
  A ``"page"`` denial always reports ``"response"``, because a page refuses by returning one.
- ``form_backend_loaded`` fires once per ``FORM_ACTION_BACKENDS`` entry the loader turns into an instance, and a skipped entry sends nothing.
- ``wizard_backend_loaded`` fires when the single ``FORM_WIZARD_BACKEND`` instance is built, on first use and again after a settings reload rebuilds it.

All five request-time signals carry ``uid`` and ``request``.
``uid`` is the registry identity also stamped on the ``data-next-action`` markup attribute, ``None`` for a backend whose meta stores no uid.
``request`` is the live ``HttpRequest`` and receivers must not retain it past the call.
The two load-time signals carry neither, sending the resolved backend class as the sender with ``config``, a copy of the settings entry, and ``instance`` in the payload.

Extension points
----------------

- Subclass ``RegistryFormActionBackend`` and override ``dispatch`` to wrap the standard pipeline.
- Override ``render_invalid_page`` for custom validation-error HTML, or ``shape_response`` for a custom response envelope.
  The base ``shape_response`` asks ``partial_shaper_slot`` whether the request is partial and hands a partial outcome to the bound shaper, reaching the default full-page envelope only for a plain request, so an override that never calls ``super().shape_response`` disables the patch envelopes.
- Register the custom backend through ``FORM_ACTION_BACKENDS``.
- Subscribe to ``action_dispatched`` for audit and cache invalidation.
- Subscribe to ``form_validation_failed`` for alerting on failure rates.

See also
--------

.. seealso::

   :doc:`/content/topics/forms/index` for the topic subtree.
   :doc:`/content/topics/forms/validation-rerender` for the failure flow.
