from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class CompileErrorCase:
    """One `{% more %}` opening tag and a fragment of the error it compiles to."""

    id: str
    tag: str
    message: str


_REQUIRED = "requires zone= and one of next= or previous="
_MARGIN = "margin= must be"
_PRUNE = "prune= must be a whole number of rows from 1"

COMPILE_ERROR_CASES: tuple[CompileErrorCase, ...] = (
    CompileErrorCase("no-options", "{% more %}", _REQUIRED),
    CompileErrorCase("no-next", '{% more zone="z" %}', _REQUIRED),
    CompileErrorCase("no-zone", "{% more next=n %}", _REQUIRED),
    CompileErrorCase("no-zone-previous", "{% more previous=n %}", _REQUIRED),
    CompileErrorCase("both", '{% more zone="z" next=n previous=p %}', _REQUIRED),
    CompileErrorCase("no-equals", '{% more zone="z" next=n manual %}', "needs a value"),
    CompileErrorCase("no-value", '{% more zone="z" next= %}', "needs a value"),
    CompileErrorCase(
        "unknown", '{% more zone="z" next=n after="x" %}', "unknown option 'after'"
    ),
    CompileErrorCase("twice", '{% more zone="z" zone="y" next=n %}', "'zone' twice"),
    CompileErrorCase("variable-zone", "{% more zone=z next=n %}", "quoted literal"),
    CompileErrorCase("zone-not-slug", '{% more zone="a b" next=n %}', "ASCII slug"),
    CompileErrorCase("zone-blank", '{% more zone=" " next=n %}', "ASCII slug"),
    CompileErrorCase(
        "mode", '{% more zone="z" next=n mode="eager" %}', "mode= must be one of"
    ),
    CompileErrorCase(
        "merge", '{% more zone="z" next=n merge="morph" %}', "merge= must be one of"
    ),
    CompileErrorCase(
        "url", '{% more zone="z" next=n url="reload" %}', "url= must be one of"
    ),
    CompileErrorCase("variable-url", '{% more zone="z" next=n url=u %}', "quoted"),
    CompileErrorCase("prune-zero", '{% more zone="z" next=n prune=0 %}', _PRUNE),
    CompileErrorCase("prune-word", '{% more zone="z" next=n prune="many" %}', _PRUNE),
    CompileErrorCase("prune-sign", '{% more zone="z" next=n prune="-5" %}', _PRUNE),
    CompileErrorCase(
        "prune-huge", '{% more zone="z" next=n prune=' + "9" * 19 + " %}", _PRUNE
    ),
    CompileErrorCase(
        "prune-previous",
        '{% more zone="z" previous=p prune=200 %}',
        "apply to a next= tag only",
    ),
    CompileErrorCase(
        "url-previous",
        '{% more zone="z" previous=p url="push" %}',
        "apply to a next= tag only",
    ),
    CompileErrorCase(
        "variable-mode", '{% more zone="z" next=n mode=manual %}', "quoted literal"
    ),
    CompileErrorCase(
        "param-not-slug", '{% more zone="z" next=n param="p q" %}', "ASCII slug"
    ),
    CompileErrorCase(
        "param-restore", '{% more zone="z" next=n param="restore" %}', "restored"
    ),
    CompileErrorCase(
        "margin-unit", '{% more zone="z" next=n margin="4em" %}', "margin= must be"
    ),
    CompileErrorCase(
        "margin-huge", '{% more zone="z" next=n margin=' + "9" * 5000 + " %}", _MARGIN
    ),
    CompileErrorCase(
        "margin-seven-digits", '{% more zone="z" next=n margin=1234567 %}', _MARGIN
    ),
    CompileErrorCase(
        "margin-long-length",
        '{% more zone="z" next=n margin="' + "9" * 400 + 'px" %}',
        _MARGIN,
    ),
    CompileErrorCase(
        "margin-previous-manual",
        '{% more zone="z" previous=p margin="400px" %}',
        'mode="visible" only',
    ),
    CompileErrorCase(
        "margin-five",
        '{% more zone="z" next=n margin="1px 1px 1px 1px 1px" %}',
        "margin= must be",
    ),
    CompileErrorCase(
        "margin-manual",
        '{% more zone="z" next=n mode="manual" margin="400px" %}',
        'mode="visible" only',
    ),
)
