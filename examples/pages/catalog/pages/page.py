from next.pages import context


@context
def landing_page_context(*args, **kwargs):
    return {
        "title": "Welcome to the Catalog!",
    }


@context("landing")
def landing_context_custom_name_with_args_kwargs(*args, **kwargs):
    return {
        "title": "Welcome to the Catalog",
        "description": "Discover our collection of amazing products and find what suits you best. Fast, simple, and convenient shopping experience.",
    }
