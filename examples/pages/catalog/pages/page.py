from next.pages import context


@context
def landing_page_context() -> dict[str, str]:
    return {
        "title": "Welcome to the Catalog!",
    }


# Context var: keyed context (injected by name into other context functions)
@context("app_greeting")
def app_greeting() -> str:
    return "Greeting from context DI!"


# Uses global context (merged dict above) + context var (app_greeting) via param name
@context("landing")
def landing_context_custom_name_with_args_kwargs(
    app_greeting: str,
) -> dict[str, str]:
    """Param name matches context key; ContextKeyProvider injects it."""
    return {
        "title": "Welcome to the Catalog",
        "description": "Discover our collection of amazing products and find what suits you best. Fast, simple, and convenient shopping experience.",
        "app_greeting": app_greeting,
    }
