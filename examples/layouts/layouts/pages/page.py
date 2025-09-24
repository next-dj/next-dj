from django.http import HttpRequest

from next.pages import context


@context("custom_variable", inherit_context=True)
def custom_variable_context_with_inherit(_request: HttpRequest) -> str:
    return (
        "Hello everyone! I'm a context with inherit_context=True."
        "Should be inherited by child pages."
    )


@context("custom_variable_2")
def custom_variable_2_context(_request: HttpRequest) -> str:
    return (
        "Hello everyone! I'm a context without inherit_context=True."
        "Should not be inherited by child pages."
    )
