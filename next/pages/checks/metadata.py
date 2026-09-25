"""System checks for the metadata of the settings tier and of every routed `page.py`.

The settings scope owns `next.E035` and `next.E098` to `next.E101` with `next.W084`,
the page tier owns `next.E102` to `next.E109` with `next.W085` to `next.W088`, and
the opt-in SEO audits behind `--deploy --tag seo` own `next.W089` to `next.W096`.
"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import TYPE_CHECKING, Any, Final, NamedTuple, cast
from urllib.parse import urlsplit

from django.conf import settings
from django.conf.urls.i18n import is_language_prefix_patterns_used
from django.core.checks import (
    CheckMessage,
    Error,
    Tags,
    Warning as DjangoWarning,
    register,
)
from django.template import Template, TemplateDoesNotExist, TemplateSyntaxError
from django.urls import Resolver404, resolve
from django.utils import translation

from next.checks import NEXT, SEO
from next.checks.common import (
    RegistrationSubject,
    errors_for_unknown_keys,
    registration_file_errors,
)
from next.components.sources import get_components_manager
from next.conf.defaults import USER_SETTING
from next.introspect import callable_name
from next.pages.errors import (
    PageMetadataConflictError,
    PageMetadataShapeError,
    PageMetadataTemplateError,
)
from next.pages.loaders import _load_python_module_memo
from next.pages.manager import page
from next.pages.metadata import (
    Metadata,
    Segment,
    Text,
    chain_entry,
    metadata_options,
    normalize_metadata,
    template_has_title,
)
from next.pages.metadata.defaults import SITE_SOURCE
from next.pages.paths import page_path_info
from next.templatetags.components import ComponentNode
from next.templatetags.pages import MetadataNode

from .contexts import annotation_is_dict_like, load_routed_pages, return_annotation


if TYPE_CHECKING:
    from collections.abc import Iterator

    from django.template.base import NodeList

    from next.components.manager import ComponentsManager
    from next.pages.metadata import PageMetadataEntry


_SCOPE_PREFIX: Final = "NEXT_FRAMEWORK['METADATA']"
_SCOPE_KEYS: Final = frozenset({"DEFAULTS", "NOINDEX", "CANONICAL_QUERY", "CHECKS"})
_SCHEMES: Final = frozenset({"http", "https"})
_TWITTER_CARDS: Final = frozenset({"summary", "summary_large_image", "app", "player"})
_X_DEFAULT: Final = "x-default"
_DESCRIPTION_MIN: Final = 50
_DUPLICATE_GROUP: Final = 2
_MAX_COMPONENT_DEPTH: Final = 8
_TEMPLATE_HINT: Final = (
    "Write {title}, the %s placeholder of Next.js is not substituted here."
)

_METADATA_SUBJECT = RegistrationSubject(
    decorator="@page.metadata",
    anchor_name="page.py",
    render="page render",
    code="next.E106",
)


class MetadataPage(NamedTuple):
    """A routed `page.py` with its raw metadata, its own segment and its static fold.

    `segment` is the page's own dict normalised and `static` the fold of the whole
    chain, either `None` where the schema refused the source. `raw` is `None` when the
    module attribute is the registered callable itself, which is the callable form.
    """

    url_path: str
    page_path: Path
    raw: object
    entry: PageMetadataEntry | None
    segment: Segment | None
    shape_error: PageMetadataShapeError | None
    static: Metadata | None
    declared: bool
    dynamic: bool


def loaded_metadata_pages() -> tuple[list[CheckMessage], list[MetadataPage]]:
    """Return every routed `page.py` with what it declares and what it folds to."""
    init_errors, loaded = load_routed_pages()
    return init_errors, [
        _metadata_page(url_path, page_path) for url_path, page_path in loaded
    ]


def _metadata_page(url_path: str, page_path: Path) -> MetadataPage:
    module = _load_python_module_memo(page_path)
    raw = None if module is None else getattr(module, "metadata", None)
    entry = page._metadata_registry.entry(page_path)
    if entry is not None and raw is entry.func:
        raw = None
    segment: Segment | None = None
    shape_error: PageMetadataShapeError | None = None
    if isinstance(raw, Mapping):
        try:
            segment = normalize_metadata(raw, source=str(page_path))
        except PageMetadataShapeError as exc:
            shape_error = exc
    static, declared, dynamic = _static_fold(page_path)
    return MetadataPage(
        url_path=url_path,
        page_path=page_path,
        raw=raw,
        entry=entry,
        segment=segment,
        shape_error=shape_error,
        static=static,
        declared=declared,
        dynamic=dynamic,
    )


def _static_fold(page_path: Path) -> tuple[Metadata | None, bool, bool]:
    """Return the static fold, whether the chain has a source, and whether it is live.

    A dynamic chain carries a callable, so the static fold is not what a render shows.
    """
    try:
        entry = chain_entry(page._metadata_registry, page_path)
    except (PageMetadataShapeError, PageMetadataConflictError):
        return None, False, False
    return entry.static, bool(entry.sources), entry.folded is None


def _folded_pages(pages: list[MetadataPage]) -> Iterator[tuple[MetadataPage, Metadata]]:
    """Yield the pages whose chain folded, paired with the fold."""
    for entry in pages:
        if entry.static is not None:
            yield entry, entry.static


def _static_pages(pages: list[MetadataPage]) -> Iterator[tuple[MetadataPage, Metadata]]:
    """Yield the folded pages no callable rewrites, the ones the static fold renders."""
    for entry, meta in _folded_pages(pages):
        if not entry.dynamic:
            yield entry, meta


def _metadata_scope() -> dict[str, Any] | None:
    """Return the raw `METADATA` scope, or `None` where `next.E076` reports it."""
    raw = getattr(settings, USER_SETTING, None)
    if not isinstance(raw, dict):
        return None
    scope = raw.get("METADATA")
    return scope if isinstance(scope, dict) else None


def _site_defaults(scope: dict[str, Any]) -> tuple[Segment | None, CheckMessage | None]:
    """Normalise the raw `DEFAULTS`, answering the `next.E098` it earns instead."""
    defaults = scope.get("DEFAULTS", {})
    if not isinstance(defaults, Mapping):
        return None, Error(
            f"{SITE_SOURCE} must be a mapping of metadata keys, got "
            f"{type(defaults).__name__!r}. The chain ignores it as written.",
            obj=settings,
            id="next.E098",
        )
    try:
        return normalize_metadata(defaults, source=SITE_SOURCE, site=True), None
    except PageMetadataShapeError as exc:
        return None, Error(
            f"{exc}. The keys of DEFAULTS are the lower-case metadata keys a "
            "page.py declares, and the title takes only the template and "
            "default form.",
            obj=settings,
            id="next.E098",
        )


def _is_origin(base: str) -> bool:
    parts = urlsplit(base)
    return (
        parts.scheme in _SCHEMES
        and bool(parts.netloc)
        and parts.path in {"", "/"}
        and not parts.query
        and not parts.fragment
    )


def _segment_errors(
    segment: Segment, *, source: str, obj: object
) -> list[CheckMessage]:
    """Return `next.E100`, `next.E101` and `next.E105` for one normalised segment."""
    errors: list[CheckMessage] = []
    spec = segment.title
    if spec is not None and spec.template is not None and spec.default is None:
        errors.append(
            Error(
                f"{source} declares a title template without a default, so a page "
                "without a title of its own renders none. Add title.default.",
                obj=obj,
                id="next.E100",
            )
        )
    if spec is not None and any(
        isinstance(value, str) and not value
        for value in (spec.text, spec.default, spec.absolute)
    ):
        errors.append(
            Error(
                f"{source} declares an empty title, which renders an empty <title>. "
                "Give the title text, or drop the key so the chain default applies.",
                obj=obj,
                id="next.E105",
            )
        )
    if segment.base is not None and not _is_origin(segment.base):
        errors.append(
            Error(
                f"{source} declares base {segment.base!r}, which is not an origin. "
                "Write an absolute http or https URL with a host and no path, "
                "query or fragment, like 'https://acme.example'.",
                obj=obj,
                id="next.E101",
            )
        )
    return errors


@register(Tags.templates, NEXT)
def check_metadata_settings_scope(*args, **kwargs) -> list[CheckMessage]:
    """Validate the `METADATA` scope and its `DEFAULTS` tier.

    An unknown option is `next.E035`, a `DEFAULTS` the schema refuses is `next.E098`,
    and the settings tier draws `next.E100`, `next.E101` and `next.E105` like a page.
    """
    scope = _metadata_scope()
    if scope is None:
        return []
    errors = errors_for_unknown_keys(scope, allowed=_SCOPE_KEYS, prefix=_SCOPE_PREFIX)
    segment, error = _site_defaults(scope)
    if error is not None:
        errors.append(error)
    elif segment is not None:
        errors.extend(_segment_errors(segment, source=SITE_SOURCE, obj=settings))
    return errors


class _TitleTemplate(NamedTuple):
    """One title template with the source declaring it and the object it reports on."""

    source: str
    obj: object
    template: Text


def _title_templates(pages: list[MetadataPage]) -> list[_TitleTemplate]:
    """Return the title templates of the settings tier and of every page's own dict."""
    found: list[_TitleTemplate] = []
    scope = _metadata_scope()
    site = None if scope is None else _site_defaults(scope)[0]
    sources: list[tuple[str, object, Segment | None]] = [
        (SITE_SOURCE, settings, site),
        *(
            (str(entry.page_path), str(entry.page_path), entry.segment)
            for entry in pages
        ),
    ]
    for source, obj, segment in sources:
        template = (
            None if segment is None or segment.title is None else segment.title.template
        )
        if template is not None:
            found.append(_TitleTemplate(source, obj, template))
    return found


def _language_codes() -> tuple[str | None, ...]:
    """Return the codes a template is evaluated under, or `None` alone without i18n."""
    if not settings.USE_I18N:
        return (None,)
    return tuple(code for code, _name in settings.LANGUAGES)


def _evaluated(template: Text, code: str | None) -> str:
    if code is None:
        return str(template)
    with translation.override(code):
        return str(template)


class _TemplateFinding(NamedTuple):
    """One failure or warning of a title template, keyed without the language."""

    item: _TitleTemplate
    text: str
    detail: str | None


def _template_findings(
    items: list[_TitleTemplate],
) -> dict[_TemplateFinding, list[str | None]]:
    """Evaluate every template under every language, grouping equal findings.

    A `detail` names the parse failure, and `None` marks a template without `{title}`.
    """
    findings: dict[_TemplateFinding, list[str | None]] = {}
    for item in items:
        for code in _language_codes():
            text = _evaluated(item.template, code)
            try:
                has_title = template_has_title(text)
            except PageMetadataTemplateError as exc:
                findings.setdefault(
                    _TemplateFinding(item, text, exc.detail), []
                ).append(code)
                continue
            if not has_title:
                findings.setdefault(_TemplateFinding(item, text, None), []).append(code)
    return findings


def _under_languages(codes: list[str | None]) -> str:
    """Name the languages a finding held under, unless it held under every one."""
    named = [repr(code) for code in codes if code is not None]
    if not named or len(named) == len(_language_codes()):
        return ""
    noun = "language" if len(named) == 1 else "languages"
    return f" under the {noun} {', '.join(named)}"


def _template_message(
    finding: _TemplateFinding, codes: list[str | None]
) -> CheckMessage:
    item = finding.item
    where = _under_languages(codes)
    if finding.detail is not None:
        return Error(
            f"{item.source} declares the title template {finding.text!r}{where}, "
            f"which {finding.detail}. Only {{title}} and {{site_name}} are "
            "substituted, as bare placeholders.",
            obj=item.obj,
            id="next.E099",
        )
    return DjangoWarning(
        f"{item.source} declares the title template {finding.text!r}{where}, which "
        "never names {title}, so every page under it renders the same title.",
        hint=_TEMPLATE_HINT,
        obj=item.obj,
        id="next.W084",
    )


@register(Tags.templates, NEXT)
def check_metadata_title_templates(*args, **kwargs) -> list[CheckMessage]:
    """Parse every title template under every language (`next.E099`, `next.W084`).

    The settings tier and each page's own dict are read, one finding per template.
    """
    init_errors, pages = loaded_metadata_pages()
    messages = list(init_errors)
    for finding, codes in _template_findings(_title_templates(pages)).items():
        messages.append(_template_message(finding, codes))
    return messages


def _page_shape_errors(entry: MetadataPage) -> list[CheckMessage]:
    """Return `next.E102` to `next.E105` and the segment errors of one page."""
    errors: list[CheckMessage] = []
    page_path = entry.page_path
    obj = str(page_path)
    if entry.raw is not None and entry.entry is not None:
        errors.append(
            Error(
                f"{page_path} declares both a metadata dict and an @page.metadata "
                "callable. Keep one, the callable when the values depend on the "
                "request and the dict otherwise.",
                obj=obj,
                id="next.E102",
            )
        )
    if entry.raw is not None and not isinstance(entry.raw, Mapping):
        errors.append(
            Error(
                f"{page_path} declares metadata as {type(entry.raw).__name__!r}, "
                "expected a mapping. Write metadata = {...} with the metadata keys.",
                obj=obj,
                id="next.E103",
            )
        )
    if entry.shape_error is not None:
        errors.append(
            Error(
                f"{entry.shape_error}. Fix the key or the value so the page renders "
                "its metadata.",
                obj=obj,
                id="next.E104",
            )
        )
    segment = entry.segment
    if segment is None:
        return errors
    card = None if segment.twitter is None else segment.twitter.card
    if card is not None and card not in _TWITTER_CARDS:
        allowed = ", ".join(sorted(_TWITTER_CARDS))
        errors.append(
            Error(
                f"{page_path} declares twitter.card {card!r}, expected one of "
                f"{allowed}.",
                obj=obj,
                id="next.E104",
            )
        )
    errors.extend(_segment_errors(segment, source=str(page_path), obj=obj))
    return errors


@register(Tags.templates, NEXT)
def check_page_metadata_shape(*args, **kwargs) -> list[CheckMessage]:
    """Validate the metadata dict each routed `page.py` declares.

    Both forms at once is `next.E102`, a non-mapping is `next.E103`, a key or a value
    the schema refuses is `next.E104`, and an empty title is `next.E105`.
    """
    init_errors, pages = loaded_metadata_pages()
    errors = list(init_errors)
    for entry in pages:
        errors.extend(_page_shape_errors(entry))
    return errors


@register(Tags.templates, NEXT)
def check_metadata_registration_files(*args, **kwargs) -> list[CheckMessage]:
    """Flag a `@page.metadata` no page render collects (`next.E106`).

    A registration keys on the file declaring the callable, so an imported helper
    binds to its own module, and a sibling page's callable binds to that other page.
    """
    init_errors, _loaded = load_routed_pages()
    if init_errors:
        return init_errors
    return registration_file_errors(
        _METADATA_SUBJECT,
        registrations=page.metadata_names(),
        misattributed=page._metadata_registry.misattributed(),
    )


@register(Tags.templates, NEXT)
def check_single_metadata_callable(*args, **kwargs) -> list[CheckMessage]:
    """Flag a `page.py` registering more than one `@page.metadata` (`next.E107`).

    One slot holds the callable, so only the last registration runs.
    """
    init_errors, loaded = load_routed_pages()
    errors = list(init_errors)
    conflicts = page._metadata_registry.conflicts()
    for _url_path, page_path in loaded:
        names = conflicts.get(page_path)
        if not names:
            continue
        joined = ", ".join(names)
        errors.append(
            Error(
                f"page.py at {page_path} registers several @page.metadata callables "
                f"({joined}). Only the last one runs, so the earlier ones are "
                "ignored. Merge them into a single callable.",
                obj=str(page_path),
                id="next.E107",
            )
        )
    return errors


@register(Tags.templates, NEXT)
def check_metadata_callable_returns_mapping(*args, **kwargs) -> list[CheckMessage]:
    """Require a `@page.metadata` callable to be annotated dict-like (`next.E108`).

    Static on purpose, since running user code at check time can hit an unmigrated DB.
    """
    init_errors, pages = loaded_metadata_pages()
    errors = list(init_errors)
    for entry in pages:
        if entry.entry is None:
            continue
        func = entry.entry.func
        annotation = return_annotation(func)
        if annotation_is_dict_like(annotation):
            continue
        annotation_name = getattr(annotation, "__name__", None) or repr(annotation)
        errors.append(
            Error(
                f"Metadata callable {callable_name(func)} in {entry.page_path} must "
                "return a mapping of metadata keys (got return annotation "
                f"{annotation_name}). Annotate it '-> dict' or '-> MetadataDict'.",
                obj=str(entry.page_path),
                id="next.E108",
            )
        )
    return errors


def _image_urls(meta: Metadata) -> Iterator[tuple[str, str]]:
    """Yield the image URL fields of a fold with their dotted names."""
    if meta.og is not None:
        for index, image in enumerate(meta.og.images):
            if image.url is not None:
                yield f"og.images[{index}].url", image.url
    if meta.twitter is not None:
        for index, url in enumerate(meta.twitter.images):
            yield f"twitter.images[{index}]", url


def _link_urls(meta: Metadata) -> Iterator[tuple[str, str]]:
    """Yield the link URL fields of a fold with their dotted names."""
    if isinstance(meta.canonical, str):
        yield "canonical", meta.canonical
    if meta.og is not None and meta.og.url is not None:
        yield "og.url", meta.og.url
    alternates = meta.alternates
    if alternates is None:
        return
    if alternates.x_default is not None:
        yield "alternates.x_default", alternates.x_default
    if isinstance(alternates.languages, Mapping):
        for code, url in alternates.languages.items():
            yield f"alternates.languages.{code}", url


def _url_fields(meta: Metadata) -> Iterator[tuple[str, str]]:
    yield from _link_urls(meta)
    yield from _image_urls(meta)


def _root_relative(url: str) -> bool:
    return url.startswith("/") and not url.startswith("//")


def _is_absolute(url: str) -> bool:
    return urlsplit(url).scheme in _SCHEMES


@register(Tags.templates, NEXT)
def check_metadata_url_schemes(*args, **kwargs) -> list[CheckMessage]:
    """Flag a URL field whose scheme is neither http nor https (`next.E109`)."""
    init_errors, pages = loaded_metadata_pages()
    errors = list(init_errors)
    for entry, meta in _folded_pages(pages):
        for field, url in _url_fields(meta):
            scheme = urlsplit(url).scheme
            if not scheme or scheme in _SCHEMES:
                continue
            errors.append(
                Error(
                    f"{entry.page_path} folds metadata key {field!r} to {url!r}, "
                    f"whose scheme {scheme!r} is neither http nor https. Write an "
                    "absolute http(s) URL or a root-relative path.",
                    obj=str(entry.page_path),
                    id="next.E109",
                )
            )
    return errors


def _composed_template(page_path: Path) -> Template | None:
    """Return the compiled composition of a static page, or `None` to skip it.

    A `render()` page is skipped, as is a composition `next.E072` reports.
    """
    module = _load_python_module_memo(page_path)
    if module is not None and callable(getattr(module, "render", None)):
        return None
    if not page.has_template(page_path, module):
        return None
    try:
        return page.composed_template_for(page_path)
    except (TemplateSyntaxError, TemplateDoesNotExist, OSError, ValueError):
        return None


def _renders_metadata(
    nodelist: NodeList, template_path: Path, memo: dict[Path, bool | None], depth: int
) -> bool | None:
    """Tell whether the nodes render `{% metadata %}`, or `None` when unknowable.

    A component the area cannot resolve or a descent past the depth cap answers
    `None`, since a false negative costs less than a false positive here.
    """
    if nodelist.get_nodes_by_type(MetadataNode):
        return True
    if depth >= _MAX_COMPONENT_DEPTH:
        return None
    manager = get_components_manager()
    unknown = False
    nodes = cast("list[ComponentNode]", nodelist.get_nodes_by_type(ComponentNode))
    for node in nodes:
        found = _component_renders_metadata(
            manager, node.name, template_path, memo, depth
        )
        if found:
            return True
        if found is None:
            unknown = True
    return None if unknown else False


def _component_renders_metadata(
    manager: ComponentsManager,
    name: str,
    template_path: Path,
    memo: dict[Path, bool | None],
    depth: int,
) -> bool | None:
    """Resolve one component the way the tag does and descend into its template."""
    info = manager.get_component(name, template_path)
    source = None if info is None else manager.template_loader.load_source(info)
    if source is None:
        return None
    if source.path in memo:
        return memo[source.path]
    memo[source.path] = None
    try:
        compiled = Template(source.text)
    except TemplateSyntaxError:
        return None
    result = _renders_metadata(compiled.nodelist, template_path, memo, depth + 1)
    memo[source.path] = result
    return result


@register(Tags.templates, NEXT)
def check_metadata_tag_rendered(*args, **kwargs) -> list[CheckMessage]:
    """Warn when a page declares metadata its composition never renders (`next.W085`).

    The composed template and every component it reaches are searched for the tag.
    """
    init_errors, pages = loaded_metadata_pages()
    warnings = list(init_errors)
    memo: dict[Path, bool | None] = {}
    for entry in pages:
        if not entry.declared:
            continue
        template = _composed_template(entry.page_path)
        if template is None:
            continue
        template_path = Path(page_path_info(entry.page_path).template_path).resolve()
        if _renders_metadata(template.nodelist, template_path, memo, 0) is not False:
            continue
        warnings.append(
            DjangoWarning(
                f"{entry.page_path} declares metadata, but its composed template "
                "renders no {% metadata %}, so the title and the head tags never "
                "reach the page. Add {% metadata %} to the <head> of a layout.djx "
                "in its chain.",
                obj=str(entry.page_path),
                id="next.W085",
            )
        )
    return warnings


@register(Tags.templates, NEXT)
def check_metadata_absolute_urls(*args, **kwargs) -> list[CheckMessage]:
    """Warn when a root-relative canonical or image has no base (`next.W086`).

    Only outside `DEBUG`, where the request host is what a crawler or a card sees.
    """
    if settings.DEBUG:
        return []
    init_errors, pages = loaded_metadata_pages()
    warnings = list(init_errors)
    for entry, meta in _folded_pages(pages):
        if meta.base is not None:
            continue
        fields = [
            field
            for field, url in _url_fields(meta)
            if field.startswith(("canonical", "og.images", "twitter.images"))
            and _root_relative(url)
        ]
        if not fields:
            continue
        warnings.append(
            DjangoWarning(
                f"{entry.page_path} folds {', '.join(fields)} to root-relative URLs "
                "while no base is set, so their absolute form follows the request "
                "host. Set base in NEXT_FRAMEWORK['METADATA']['DEFAULTS'] so "
                "crawlers and social cards see the canonical origin.",
                obj=str(entry.page_path),
                id="next.W086",
            )
        )
    return warnings


@register(Tags.templates, NEXT)
def check_metadata_hreflang_patterns(*args, **kwargs) -> list[CheckMessage]:
    """Warn when `alternates.languages=True` has no `i18n_patterns()` (`next.W087`).

    Without a language prefix every code translates to the same URL.
    """
    init_errors, pages = loaded_metadata_pages()
    warnings = list(init_errors)
    urlconf = str(getattr(settings, "ROOT_URLCONF", ""))
    prefixed: bool | None = None
    for entry, meta in _folded_pages(pages):
        if meta.alternates is None or meta.alternates.languages is not True:
            continue
        if prefixed is None:
            prefixed = is_language_prefix_patterns_used(urlconf)[0]
        if prefixed:
            continue
        warnings.append(
            DjangoWarning(
                f"{entry.page_path} asks for hreflang alternates with "
                f"alternates.languages=True, but ROOT_URLCONF "
                f"{urlconf!r} uses no i18n_patterns(), so every "
                "language points at the same URL. Wrap the page routes in "
                "i18n_patterns(), or list the alternates as a mapping.",
                obj=str(entry.page_path),
                id="next.W087",
            )
        )
    return warnings


def _noindex(meta: Metadata) -> bool:
    robots = meta.robots
    if isinstance(robots, str):
        return "noindex" in robots
    return robots is not None and robots.index is False


def _foreign_origin(url: str, base: str | None) -> bool:
    """Whether an absolute `url` sits on another host than `base`, or `base` is None."""
    if not _is_absolute(url):
        return False
    return base is None or urlsplit(url).netloc != urlsplit(base).netloc


@register(Tags.templates, NEXT)
def check_metadata_noindex_canonical(*args, **kwargs) -> list[CheckMessage]:
    """Warn when a noindex page points its canonical at another origin (`next.W088`)."""
    init_errors, pages = loaded_metadata_pages()
    warnings = list(init_errors)
    for entry, meta in _folded_pages(pages):
        canonical = meta.canonical
        if not isinstance(canonical, str) or not _noindex(meta):
            continue
        if not _foreign_origin(canonical, meta.base):
            continue
        warnings.append(
            DjangoWarning(
                f"{entry.page_path} is noindex and points its canonical at "
                f"{canonical!r}, another origin. A noindex page passes no signal to "
                "a cross-domain canonical, so drop the canonical or let the page be "
                "indexed.",
                obj=str(entry.page_path),
                id="next.W088",
            )
        )
    return warnings


def _threshold(name: str, default: int) -> int:
    """Return an integer option of `METADATA['CHECKS']`, or `default` when unusable."""
    value = metadata_options().checks.get(name, default)
    if isinstance(value, bool) or not isinstance(value, int):
        return default
    return value


def _require_description() -> bool:
    return bool(metadata_options().checks.get("REQUIRE_DESCRIPTION", True))


@register(Tags.templates, NEXT, SEO, deploy=True)
def check_seo_description(*args, **kwargs) -> list[CheckMessage]:
    """Audit the folded description of every static page (`next.W089`, `next.W092`).

    Skips a page a callable rewrites, and measures under the default language.
    """
    init_errors, pages = loaded_metadata_pages()
    warnings = list(init_errors)
    longest = _threshold("DESCRIPTION_MAX", 160)
    required = _require_description()
    with translation.override(settings.LANGUAGE_CODE):
        for entry, meta in _static_pages(pages):
            if meta.description is None:
                if required:
                    warnings.append(_missing_description(entry))
                continue
            length = len(str(meta.description))
            if _DESCRIPTION_MIN <= length <= longest:
                continue
            warnings.append(
                DjangoWarning(
                    f"{entry.page_path} folds to a description of {length} "
                    f"characters, outside {_DESCRIPTION_MIN} to {longest}. Search "
                    "results truncate a long one and pad a short one with page "
                    "text.",
                    obj=str(entry.page_path),
                    id="next.W092",
                )
            )
    return warnings


def _missing_description(entry: MetadataPage) -> CheckMessage:
    return DjangoWarning(
        f"{entry.page_path} folds to no description, so search results write "
        "their own snippet. Declare description on the page or in "
        "NEXT_FRAMEWORK['METADATA']['DEFAULTS'], or set "
        "METADATA['CHECKS']['REQUIRE_DESCRIPTION'] to False.",
        obj=str(entry.page_path),
        id="next.W089",
    )


def _trail(url_path: str) -> str:
    return f"/{url_path}"


@register(Tags.templates, NEXT, SEO, deploy=True)
def check_seo_titles(*args, **kwargs) -> list[CheckMessage]:
    """Audit the folded titles of every static page (`next.W090`, `next.W091`).

    Skips a page a callable rewrites and compares the rest under the default language.
    """
    init_errors, pages = loaded_metadata_pages()
    warnings = list(init_errors)
    longest = _threshold("TITLE_MAX", 60)
    groups: dict[str, list[MetadataPage]] = {}
    with translation.override(settings.LANGUAGE_CODE):
        for entry, meta in _static_pages(pages):
            if meta.title is None:
                continue
            text = str(meta.title)
            groups.setdefault(text, []).append(entry)
            if len(text) <= longest:
                continue
            warnings.append(
                DjangoWarning(
                    f"{entry.page_path} folds to the title {text!r} of {len(text)} "
                    f"characters, over {longest}. Search results cut it short, so "
                    "shorten the title or the template around it.",
                    obj=str(entry.page_path),
                    id="next.W091",
                )
            )
    for text, group in groups.items():
        if len(group) < _DUPLICATE_GROUP:
            continue
        trails = ", ".join(repr(_trail(entry.url_path)) for entry in group)
        warnings.append(
            DjangoWarning(
                f"Pages at {trails} fold to the same title {text!r}, so search "
                "results cannot tell them apart. Give each page a title of its own.",
                obj=str(group[0].page_path),
                id="next.W090",
            )
        )
    return warnings


def _same_origin(url: str, base: str | None) -> bool:
    if _root_relative(url):
        return True
    return _is_absolute(url) and not _foreign_origin(url, base)


def _resolves(path: str) -> bool:
    try:
        resolve(path or "/")
    except Resolver404:
        return False
    return True


@register(Tags.templates, NEXT, SEO, deploy=True)
def check_seo_canonical(*args, **kwargs) -> list[CheckMessage]:
    """Audit literal canonicals (`next.W093`, `next.W094`).

    A literal on a dynamic route is one URL for every match, and a same-origin
    canonical is expected to resolve through the URLconf.
    """
    init_errors, pages = loaded_metadata_pages()
    warnings = list(init_errors)
    for entry, meta in _folded_pages(pages):
        canonical = meta.canonical
        if not isinstance(canonical, str):
            continue
        if "[" in entry.url_path:
            warnings.append(
                DjangoWarning(
                    f"{entry.page_path} on the dynamic route {_trail(entry.url_path)!r}"
                    f" declares the literal canonical {canonical!r}, so every match "
                    "claims the same URL. Set canonical=True for the self URL, or "
                    "compute it in a @page.metadata callable.",
                    obj=str(entry.page_path),
                    id="next.W094",
                )
            )
            continue
        if not _same_origin(canonical, meta.base):
            continue
        if _resolves(urlsplit(canonical).path):
            continue
        warnings.append(
            DjangoWarning(
                f"{entry.page_path} declares the canonical {canonical!r}, which "
                "resolves to no URL of this project. Point it at a routed path, or "
                "set canonical=True for the self URL.",
                obj=str(entry.page_path),
                id="next.W093",
            )
        )
    return warnings


@register(Tags.templates, NEXT, SEO, deploy=True)
def check_seo_alternates(*args, **kwargs) -> list[CheckMessage]:
    """Audit an hreflang mapping (`next.W095`, `next.W096`).

    A mapping is expected to name a fallback and only codes `LANGUAGES` lists.
    """
    init_errors, pages = loaded_metadata_pages()
    warnings = list(init_errors)
    known = {code for code, _name in settings.LANGUAGES}
    for entry, meta in _folded_pages(pages):
        alternates = meta.alternates
        if alternates is None or not isinstance(alternates.languages, Mapping):
            continue
        languages = alternates.languages
        if _X_DEFAULT not in languages and alternates.x_default is None:
            warnings.append(
                DjangoWarning(
                    f"{entry.page_path} lists hreflang alternates without an "
                    "x-default, so a visitor outside those languages lands on none. "
                    "Add alternates.x_default, or an 'x-default' key to the mapping.",
                    obj=str(entry.page_path),
                    id="next.W095",
                )
            )
        unknown = sorted(
            code for code in languages if code != _X_DEFAULT and code not in known
        )
        if not unknown:
            continue
        joined = ", ".join(repr(code) for code in unknown)
        warnings.append(
            DjangoWarning(
                f"{entry.page_path} lists hreflang alternates for {joined}, which "
                "settings.LANGUAGES does not name. Add the language, or drop the "
                "alternate.",
                obj=str(entry.page_path),
                id="next.W096",
            )
        )
    return warnings


__all__ = [
    "MetadataPage",
    "check_metadata_absolute_urls",
    "check_metadata_callable_returns_mapping",
    "check_metadata_hreflang_patterns",
    "check_metadata_noindex_canonical",
    "check_metadata_registration_files",
    "check_metadata_settings_scope",
    "check_metadata_tag_rendered",
    "check_metadata_title_templates",
    "check_metadata_url_schemes",
    "check_page_metadata_shape",
    "check_seo_alternates",
    "check_seo_canonical",
    "check_seo_description",
    "check_seo_titles",
    "check_single_metadata_callable",
    "loaded_metadata_pages",
]
