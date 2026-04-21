"""Discover and inject co-located static assets for pages and components.

Each `.djx` file may have a matching `.css` and `.js` file in the same
directory. Pages and components may also declare `styles` and `scripts`
list variables in their Python modules. During rendering, a shared
`StaticCollector` gathers every referenced asset. After rendering,
`StaticManager.inject` replaces the `{% collect_styles %}` and
`{% collect_scripts %}` placeholders with the actual `<link>` and
`<script>` tags. Public URLs are resolved through Django staticfiles.

The `Next` JavaScript runtime is automatically injected on every page by
default. `StaticManager.inject` prepends `next.min.js` as the first
script and follows it with an inline init script that passes the
serialized JS context to `Next._init`. Context values opt into
JavaScript exposure by using `serialize=True` on their `@context`
decorator. A preload hint is injected immediately before `</head>` so
the browser downloads the file during HTML parsing. Users may switch to
`ScriptInjectionPolicy.DISABLED` or `ScriptInjectionPolicy.MANUAL` to
opt out.

The subsystem is structured as a small package of single-responsibility
modules. The public surface is narrow on purpose. Internal plumbing such
as dedup strategies, placeholder registries, and path helpers lives in
submodules and is reachable with deep imports of the form
`from next.static.collector import UrlDedup`.
"""

from __future__ import annotations

from . import signals
from .assets import KindRegistry, StaticAsset, default_kinds
from .backends import StaticBackend, StaticFilesBackend, StaticsFactory
from .collector import StaticCollector
from .discovery import AssetDiscovery
from .finders import NextStaticFilesFinder
from .manager import StaticManager, default_manager, reset_default_manager
from .scripts import NextScriptBuilder, ScriptInjectionPolicy


__all__ = [
    "AssetDiscovery",
    "KindRegistry",
    "NextScriptBuilder",
    "NextStaticFilesFinder",
    "ScriptInjectionPolicy",
    "StaticAsset",
    "StaticBackend",
    "StaticCollector",
    "StaticFilesBackend",
    "StaticManager",
    "StaticsFactory",
    "default_kinds",
    "default_manager",
    "reset_default_manager",
    "signals",
]
