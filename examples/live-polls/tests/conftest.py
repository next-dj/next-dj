import pytest
from polls.models import Choice, Poll


DEMO_CHOICES = {
    "Tabs or spaces?": ("Tabs", "Spaces"),
    "Vim or Emacs?": ("Vim", "Emacs"),
}


def _demo_poll(question: str) -> Poll:
    """Return the seeded poll, restoring any choice the caller's test dropped."""
    poll, _ = Poll.objects.get_or_create(question=question)
    for text in DEMO_CHOICES[question]:
        Choice.objects.get_or_create(poll=poll, text=text, defaults={"votes": 0})
    return poll


@pytest.fixture()
def poll(db) -> Poll:
    """Return the first demo poll with its two choices.

    The fixture dedupes against rows seeded by the data migration so the
    total poll count stays at two and assertions on the inherited
    `active_polls_count` stay deterministic.
    """
    return _demo_poll("Tabs or spaces?")


@pytest.fixture()
def second_poll(db) -> Poll:
    """Return the second seeded poll, used to exercise cross-poll validation."""
    return _demo_poll("Vim or Emacs?")
