.. _howto-scope-requests-per-tenant:

Scope requests per tenant
=========================

Problem
-------

Several tenants share one Django project, one page tree, and one static pipeline, and every request must see only the data, theme, and asset URLs of the tenant it belongs to.

Solution
--------

Resolve the tenant once in :doc:`middleware <django:topics/http/middleware>` and stash it on the request.
A dependency provider, a :doc:`context processor <django:ref/templates/api>`, and a custom static backend each read it back from there.

.. warning::

   A request header is attacker-controlled.
   Any visitor can send an ``X-Tenant`` header of their choosing with curl, so middleware that trusts that header on its own hands one tenant's rows to anybody who guesses another tenant's slug.
   Derive the tenant from the authenticated user's membership or from the request host, and read it from a header only behind a trusted proxy that sets the header itself and strips every inbound copy.

The ``examples/multi-tenant/`` project in the repository reads the tenant from a header, so it belongs to the proxy-fronted case below and needs that proxy in front of it.
See :doc:`/content/misc/examples`.

Walkthrough
-----------

Pick a tenant source the client cannot choose
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

The middleware decides which tenant a request belongs to, so its input decides whether the project has isolation at all.
Three sources carry different amounts of trust.

- The membership rows that join users to tenants, which the database owns and no request can rewrite.
- The request host, which ``ALLOWED_HOSTS`` narrows to the names the project publishes.
- A request header, which is evidence only while a proxy in front of the application owns it.

Whichever source the project picks, every error response carries a fixed body.
A body that quotes the submitted slug reflects client input back into the page and turns the endpoint into an oracle for enumerating tenant names.

The tenant model and the membership table carry the mapping.

.. code-block:: python
   :caption: notes/models.py

   from django.conf import settings
   from django.db import models

   class Tenant(models.Model):
       slug = models.SlugField(unique=True)
       subdomain = models.SlugField(unique=True)
       primary_color = models.CharField(max_length=7)

   class Membership(models.Model):
       user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
       tenant = models.ForeignKey(Tenant, on_delete=models.CASCADE)

       class Meta:
           constraints = [
               models.UniqueConstraint(fields=["user", "tenant"], name="one_membership"),
           ]

Resolve the tenant from the user's membership
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

The middleware reads the active tenant out of the membership rows of the signed-in user.
The request carries no tenant identifier, so there is nothing to forge.
A user who belongs to several tenants picks one and the choice lives in the session, yet the lookup still starts from the membership rows, so a tampered session value matches no row and the request falls back to the first membership.

.. code-block:: python
   :caption: notes/middleware.py

   from django.http import HttpResponse
   from notes.models import Membership

   def _active_membership(request):
       """Return the membership the request acts under, or `None`."""
       memberships = Membership.objects.filter(user=request.user).select_related("tenant")
       chosen = request.session.get("tenant_id")
       if chosen is not None:
           return memberships.filter(tenant_id=chosen).first()
       return memberships.first()

   class TenantMiddleware:
       def __init__(self, get_response):
           self._get_response = get_response

       def __call__(self, request):
           request.tenant = None
           user = getattr(request, "user", None)
           if user is not None and user.is_authenticated:
               membership = _active_membership(request)
               if membership is None:
                   return HttpResponse("No tenant available.", status=403)
               request.tenant = membership.tenant
           return self._get_response(request)

An anonymous request keeps ``request.tenant`` at ``None`` and reaches the login page, and a signed-in user with no membership is refused with ``403``.
The endpoint that switches tenants writes ``request.session["tenant_id"]`` only after it finds a membership row for that pair, so the session never holds a tenant the user cannot reach.

Resolve the tenant from the request host
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

A tenant per subdomain serves anonymous visitors, which the membership shape cannot do.
:meth:`~django.http.HttpRequest.get_host` checks the host against ``ALLOWED_HOSTS`` first and raises the ``DisallowedHost`` subclass of :exc:`~django.core.exceptions.SuspiciousOperation` for anything outside it, which Django answers with ``400``.
The middleware therefore reads a name the project published rather than an arbitrary string.

.. code-block:: python
   :caption: notes/middleware.py

   from django.http import HttpResponse
   from notes.models import Tenant

   class TenantMiddleware:
       def __init__(self, get_response):
           self._get_response = get_response

       def __call__(self, request):
           subdomain = request.get_host().partition(":")[0].split(".")[0]
           try:
               tenant = Tenant.objects.get(subdomain=subdomain)
           except Tenant.DoesNotExist:
               return HttpResponse("Unknown tenant.", status=404)
           request.tenant = tenant
           return self._get_response(request)

.. code-block:: python
   :caption: config/settings.py

   ALLOWED_HOSTS = [".example.com"]

A leading-dot entry admits every subdomain of that domain, so the ``Tenant`` row decides which of them the project answers and an unmatched subdomain is a ``404``.
Never pair a host-derived tenant with ``ALLOWED_HOSTS = ["*"]``, because the wildcard hands the host back to the client and the tenant becomes forgeable again.
Pair the shape with the membership check as well when the page is behind a login, so a signed-in user who reaches a host they hold no membership for is refused rather than served.

Read the tenant from a header behind a trusted proxy
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

A gateway that already knows the tenant can pass the slug down in a header, which keeps the mapping in one place and out of the application.
The header identifies a tenant only while every one of these holds.

- The proxy is the only route to the application, and the application binds to an address the public network cannot reach.
- The proxy derives the value from something it owns, a host mapping, a client certificate, or its own session.
- The proxy sets the header unconditionally on every request it forwards, which overwrites any copy the client sent.
- The application refuses a request whose header is absent instead of falling back to a default tenant.

The third rule is the one that carries the isolation.
A proxy that adds the header only when it is missing leaves the client's own value in place.

.. code-block:: nginx
   :caption: nginx.conf

   location / {
       proxy_set_header X-Tenant $tenant;
       proxy_pass http://127.0.0.1:8000;
   }

.. code-block:: python
   :caption: notes/middleware.py

   from django.http import HttpResponse, HttpResponseBadRequest
   from notes.models import Tenant

   HEADER_NAME = "HTTP_X_TENANT"

   class TenantMiddleware:
       def __init__(self, get_response):
           self._get_response = get_response

       def __call__(self, request):
           slug = request.META.get(HEADER_NAME, "").strip()
           if not slug:
               return HttpResponseBadRequest("Tenant header missing.")
           try:
               tenant = Tenant.objects.get(slug=slug)
           except Tenant.DoesNotExist:
               return HttpResponse("Unknown tenant.", status=404)
           request.tenant = tenant
           return self._get_response(request)

A missing header is a ``400`` and an unknown slug is a ``404``, and neither body repeats what the client sent.
Cross-check the membership rows on top of the header whenever the request also carries an authenticated user, so a proxy misconfiguration alone cannot cross tenants.

Register the middleware last
~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Register it last in ``MIDDLEWARE`` so it runs after sessions and authentication, which both the membership shape and the cross-check depend on.

.. code-block:: python
   :caption: config/settings.py

   MIDDLEWARE = [
       "django.middleware.security.SecurityMiddleware",
       "django.contrib.sessions.middleware.SessionMiddleware",
       "django.middleware.common.CommonMiddleware",
       "django.middleware.csrf.CsrfViewMiddleware",
       "django.contrib.auth.middleware.AuthenticationMiddleware",
       "django.contrib.messages.middleware.MessageMiddleware",
       "django.middleware.clickjacking.XFrameOptionsMiddleware",
       "notes.middleware.TenantMiddleware",
   ]

Read the tenant through one helper
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Every consumer reads the tenant through a single accessor instead of touching ``request.tenant`` directly.
On error pages where the middleware short-circuited, the attribute is absent and the helper returns ``None``.

.. code-block:: python
   :caption: notes/access.py

   def get_active_tenant(request):
       """Return the tenant attached to `request` by `TenantMiddleware`."""
       return getattr(request, "tenant", None)

Inject the tenant into pages
~~~~~~~~~~~~~~~~~~~~~~~~~~~~

A ``DDependencyBase`` marker plus a ``RegisteredParameterProvider`` lets page and action callables ask for the tenant by type.
The provider matches the bare ``DTenant`` annotation when a request carries a tenant.

.. code-block:: python
   :caption: notes/providers.py

   from notes.access import get_active_tenant

   from next.deps import DDependencyBase, RegisteredParameterProvider

   class DTenant(DDependencyBase["Tenant"]):
       """DI marker that resolves to the active `Tenant` for the request."""

       __slots__ = ()

   class TenantProvider(RegisteredParameterProvider):
       def can_handle(self, param, context):
           if param.annotation is not DTenant:
               return False
           request = context.request
           if request is None:
               return False
           return get_active_tenant(request) is not None

       def static_can_handle(self, param):
           return param.annotation is DTenant

       def resolve(self, _param, context):
           return get_active_tenant(context.request)

Unlike ``DFlag[Flag]``, ``DTenant`` is matched by class identity rather than ``get_origin``, so it carries no type parameter and the provider compares ``param.annotation`` to the class directly.
``static_can_handle`` settles the parameter from the annotation alone, so the plan claims it at compile time and no other provider is consulted for it per request.

Import the module from ``AppConfig.ready`` so the auto-registry wires the provider once the app registry is populated.
``RegisteredParameterProvider`` registers the provider as a side effect of class definition, so importing the module is the whole registration step.
A top-level import in ``apps.py`` also registers the provider, but only when the provider module reaches no model, so ``ready`` is the placement that always holds.

.. code-block:: python
   :caption: notes/apps.py

   from django.apps import AppConfig

   class NotesConfig(AppConfig):
       default_auto_field = "django.db.models.BigAutoField"
       name = "notes"

       def ready(self) -> None:
           from notes import providers  # imported for its registration side effect

           _ = providers

The ``_ = providers`` line documents the intentional side-effect import so a linter does not flag it as unused.

A page context function now requests the tenant by name and type, and the resolver hands back the model instance.
Keep real annotations in these modules, because the resolver compares parameter annotations by identity.

.. code-block:: python
   :caption: notes/workspaces/notes/page.py

   from notes.models import Note
   from notes.providers import DTenant

   from next import context

   @context("notes")
   def notes(active_tenant: DTenant) -> list[Note]:
       """Return every note that belongs to the active tenant."""
       return list(Note.objects.filter(tenant=active_tenant))

Every query that reaches tenant-owned rows filters on the resolved tenant.
A query that omits the filter reads the whole table, and no amount of care in the middleware repairs that.

Share the tenant through a named dependency
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

When several callables in one render need the tenant, register the accessor as a named dependency instead.

.. code-block:: python
   :caption: notes/deps.py

   from django.http import HttpRequest
   from notes.access import get_active_tenant
   from notes.models import Tenant

   from next.deps import resolver

   @resolver.dependency("active_tenant")
   def active_tenant(request: HttpRequest) -> Tenant | None:
       return get_active_tenant(request)

Any callable then asks for the value by name.

.. code-block:: python
   :caption: notes/workspaces/notes/page.py

   from notes.models import Note, Tenant

   from next import Depends, context

   @context("note_count")
   def note_count(tenant: Tenant | None = Depends("active_tenant")) -> int:
       if tenant is None:
           return 0
       return Note.objects.filter(tenant=tenant).count()

Import ``notes.deps`` from ``AppConfig.ready`` alongside the provider module so the decorator runs before the first request.
The marker route re-resolves for every parameter that carries ``DTenant``, while the named dependency is resolved once and memoised for the rest of the pass.
Keep the marker for callables that prefer to ask by type.

Lift the tenant to every descendant page
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

A ``@context(..., inherit_context=True)`` callable on the workspace root publishes the tenant once, and every nested page reads it without re-resolving.

.. code-block:: python
   :caption: notes/workspaces/page.py

   from notes.models import Tenant
   from notes.providers import DTenant

   from next import context

   @context("tenant", inherit_context=True)
   def tenant(active_tenant: DTenant) -> Tenant:
       """Expose the active tenant under `tenant` to every workspace page."""
       return active_tenant

Theme the chrome with a context processor
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

A context processor turns the tenant's color into a CSS variable for every template.
List it in the page backend ``OPTIONS`` so the file router runs it.

.. code-block:: python
   :caption: notes/context_processors.py

   from notes.access import get_active_tenant

   def tenant_theme(request):
       """Surface per-tenant CSS variables to every page template."""
       tenant = get_active_tenant(request)
       if tenant is None:
           return {"tenant_theme": {}, "tenant_theme_css": ""}
       css_vars = {"--tenant-accent": tenant.primary_color}
       css = ";".join(f"{name}:{value}" for name, value in css_vars.items())
       return {"tenant_theme": css_vars, "tenant_theme_css": css}

.. code-block:: python
   :caption: config/settings.py

   NEXT_FRAMEWORK = {
       "PAGE_BACKENDS": [
           {
               "BACKEND": "next.urls.FileRouterBackend",
               "DIRS": [],
               "APP_DIRS": True,
               "PAGES_DIR": "workspaces",
               "OPTIONS": {
                   "context_processors": [
                       "django.template.context_processors.request",
                       "notes.context_processors.tenant_theme",
                   ],
               },
           },
       ],
   }

Prefix asset URLs per tenant
~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Scope the static backend the same way.
Subclass ``StaticFilesBackend`` and override ``asset_url``.
Read the tenant from the ``request`` keyword argument that the static manager passes to that hook, then prepend the tenant slug to each URL.
The one override reaches the co-located assets and the ``next.min.js`` runtime alike.
Register the subclass in ``STATIC_BACKENDS``.

See :doc:`write-a-static-backend` under *Tenant URL prefix* for the full implementation.

Verification
------------

Send the same path under two tenant hosts and confirm the responses diverge.

.. code-block:: bash
   :caption: confirm tenant isolation

   curl -H 'Host: acme.example.com' http://127.0.0.1:8000/notes/
   curl -H 'Host: globex.example.com' http://127.0.0.1:8000/notes/

The Acme response lists only Acme notes.
The Globex response lists only Globex notes.
A host that matches no tenant row returns ``404``.

Under the membership shape the same check signs in as an Acme member, reads the notes page, then signs in as a Globex member and confirms that neither response carries the other tenant's rows.

One negative check belongs in the suite as well.
Send a request that forges the tenant source and confirm it does not cross tenants.
A signed-in Acme member who adds an ``X-Tenant`` header still sees Acme notes under the membership shape, because the database decides.
Under the proxy-fronted shape, send the forged header through the public entry point rather than straight at the application, so the check exercises the stripping rule the isolation rests on.

See also
--------

.. seealso::

   :doc:`/content/topics/dependency-injection` for the request-scoped provider pattern.
   :doc:`/content/topics/static-assets/backends` for the request-aware backend contract.
   :doc:`/content/topics/context` for ``inherit_context`` and context processors.
   :doc:`/content/internals/request-lifecycle` for where middleware sits in the request path.
   :doc:`/content/security/overview` for the wider trust boundary the tenant source sits on.
