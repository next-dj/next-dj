.. _howto-resolve-feature-flags-with-di:

Resolve Feature Flags With DI
=============================

Problem
-------

A model row gates content across many pages and components.
You want each page context, render function, and component to receive the resolved row as a plain parameter instead of querying the database at every call site.

Solution
--------

Write a custom DI marker and provider.
The marker ``DFlag[Flag]`` annotates a parameter.
The provider subclasses ``RegisteredParameterProvider``, looks the flag up through a cache-backed helper, and returns the resolved row.
A :doc:`post_save signal receiver <django:topics/signals>` drops the cached entry on write so the cache and the database never drift apart.

Walkthrough
-----------

Define the marker and the provider
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

The marker is a Python 3.12 generic that subclasses ``DDependencyBase``.
The provider claims any parameter whose annotation origin is ``DFlag`` and reads the flag name from the URL kwargs or the template context.

.. code-block:: python
   :caption: flags/providers.py

   import inspect
   from typing import get_args, get_origin
   from next.deps import DDependencyBase, RegisteredParameterProvider
   from next.deps.context import ResolutionContext
   from .cache import get_cached_flag

   class DFlag[T](DDependencyBase[T]):
       """Annotate a parameter with `DFlag[Flag]` to inject the matching `Flag`."""

       __slots__ = ()

   class FlagProvider(RegisteredParameterProvider):
       """Resolve `DFlag[...]` parameters by looking up `flag_name`."""

       def can_handle(self, param: inspect.Parameter, _context: ResolutionContext) -> bool:
           return get_origin(param.annotation) is DFlag

       def resolve(self, param: inspect.Parameter, context: ResolutionContext) -> object:
           (model_cls,) = get_args(param.annotation)
           name = context.url_kwargs.get("name") or context.context_data.get("flag_name")
           if not name:
               msg = "DFlag requires `name` URL kwarg or `flag_name` template context key"
               raise LookupError(msg)
           return get_cached_flag(str(name)) or model_cls(name=str(name), enabled=False)

``can_handle`` returns ``True`` only for ``DFlag[...]`` subscripts.
``resolve`` checks two sources.
A page captures the name in the URL through ``context.url_kwargs``.
A component receives it as a template prop through ``context.context_data``.
When the flag does not exist the provider returns a disabled placeholder instead of ``None``.
Guard code then checks ``flag.enabled`` without a three-way branch.

Back the lookup with a cache
~~~~~~~~~~~~~~~~~~~~~~~~~~~~

The provider never touches the model directly.
A read-through helper stores both hits and a missing sentinel, so a repeated lookup for an unknown name never re-queries the database.

.. code-block:: python
   :caption: flags/cache.py

   from django.core.cache import cache
   from .models import Flag

   FLAG_PREFIX = "flags:flag:"
   MISSING_SENTINEL = "__missing__"
   FLAG_CACHE_TTL = 300

   def _key(name: str) -> str:
       return f"{FLAG_PREFIX}{name}"

   def get_cached_flag(name: str) -> Flag | None:
       """Fetch a `Flag` through LocMemCache. Return `None` when absent."""
       key = _key(name)
       cached = cache.get(key)
       if cached == MISSING_SENTINEL:
           return None
       if cached is not None:
           return cached
       try:
           flag = Flag.objects.get(name=name)
       except Flag.DoesNotExist:
           cache.set(key, MISSING_SENTINEL, FLAG_CACHE_TTL)
           return None
       cache.set(key, flag, FLAG_CACHE_TTL)
       return flag

   def invalidate_flag(name: str) -> None:
       """Drop the cached entry for `name` so the next read refetches from DB."""
       cache.delete(_key(name))

Invalidate the cache on write
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

A :doc:`Django signal receiver <django:topics/signals>` drops the cached entry whenever a flag row is saved or deleted.
The next ``get_cached_flag`` call refetches from the database.

.. code-block:: python
   :caption: flags/receivers.py

   from django.db.models.signals import post_delete, post_save
   from .cache import invalidate_flag
   from .models import Flag

   def _invalidate_on_save(instance: Flag, **_: object) -> None:
       """Drop the cached entry so the next read reflects the updated row."""
       invalidate_flag(instance.name)

   def _invalidate_on_delete(instance: Flag, **_: object) -> None:
       """Drop the cached entry when the flag is removed from the database."""
       invalidate_flag(instance.name)

   def connect() -> None:
       post_save.connect(_invalidate_on_save, sender=Flag)
       post_delete.connect(_invalidate_on_delete, sender=Flag)

Wire the provider and receivers at app ready
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

``flags.providers`` registers ``FlagProvider`` with the resolver as a side effect of import, so a top-level import of the module is enough.
``flags.receivers`` exposes a ``connect`` helper that ``AppConfig.ready`` calls, which keeps the actual signal wiring out of import time.

.. code-block:: python
   :caption: flags/apps.py

   from django.apps import AppConfig
   from flags import providers, receivers

   _ = providers

   class FlagsConfig(AppConfig):
       default_auto_field = "django.db.models.BigAutoField"
       name = "flags"

       def ready(self) -> None:
           """Connect receivers once the app registry is populated."""
           receivers.connect()

The ``_ = providers`` line documents the intentional side-effect import.
``receivers.connect`` runs at ready time, so the signal connections happen after the app registry knows ``Flag``.

Consume the marker in a component
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

A composite component with a ``render`` function asks for ``flag: DFlag[Flag]``.
When the flag is off ``render`` returns an empty string, so the gated block collapses with no wrapper markup.

.. code-block:: python
   :caption: flags/panels/_chunks/feature_guard/component.py

   from django.template import Context, Template
   from flags.models import Flag
   from flags.providers import DFlag

   _BANNER = Template(
       '<article data-feature-guard="{{ flag.name }}"'
       ' class="rounded-xl border border-emerald-300 bg-emerald-50 p-4">'
       "<h3>{{ label }}</h3>"
       '<p class="mt-1 text-sm">{{ description }}</p>'
       "</article>",
   )

   def render(flag: DFlag[Flag]) -> str:
       """Return the gated banner when the flag is enabled, otherwise empty."""
       if not flag.enabled:
           return ""
       return _BANNER.render(
           Context(
               {
                   "flag": flag,
                   "label": flag.label or flag.name,
                   "description": flag.description or "No description provided.",
               },
           ),
       )

The component module does not start with ``from __future__ import annotations``.
The resolver inspects the real annotation ``DFlag[Flag]``.
PEP 563 would string-ify it and ``get_origin`` would return ``None``.

Pass the flag name as a template prop
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Each ``{% component %}`` tag carries a literal ``flag_name``.
The string flows into the component context and the provider reads it from ``context.context_data``.

.. code-block:: jinja
   :caption: flags/panels/demo/template.djx

   {% component "feature_guard" flag_name="beta_checkout" %}
   {% component "feature_guard" flag_name="dark_sidebar" %}
   {% component "feature_guard" flag_name="ai_suggestions" %}

Inside a loop, bind the prop to the iteration variable instead of a literal, for example ``flag_name=flag.name``.
Django evaluates the expression during template rendering.

Verification
------------

Toggle a flag and confirm the next request reflects it without restarting the server.

Seed a flag from the Django shell.

.. code-block:: python
   :caption: shell

   from flags.models import Flag

   Flag.objects.create(name="beta_checkout", label="Beta checkout", enabled=True)

Request the page that renders ``feature_guard``.
The banner appears.
Set ``enabled`` to ``False`` and save.
The ``post_save`` receiver drops the cached entry, the next ``get_cached_flag`` call refetches, and the banner collapses to an empty string.

A second context function that also asks for ``DFlag[Flag]`` triggers the provider again on the same request.
``get_cached_flag`` shares Django's :class:`~django.core.cache.backends.locmem.LocMemCache` entry, so the lookup is served from process memory rather than the database.
The framework's per-resolution cache only memoises ``Depends("name")`` callables, so identity across calls comes from the LocMem cache the helper builds.

See Also
--------

.. seealso::

   :doc:`/content/topics/dependency-injection` for markers, providers, and the request-scoped cache.
   :doc:`/content/topics/components` for composite components and the ``render`` function.
   ``examples/feature-flags`` for the complete runnable project.
