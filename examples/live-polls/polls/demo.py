from polls.models import Choice, Poll


DEMO_POLLS = [
    {"question": "Tabs or spaces?", "choices": ["Tabs", "Spaces"]},
    {"question": "Vim or Emacs?", "choices": ["Vim", "Emacs"]},
]


def seed_demo() -> None:
    """Create two demo polls so the index is never empty on a fresh database."""
    for poll_data in DEMO_POLLS:
        poll, created = Poll.objects.get_or_create(question=poll_data["question"])
        if not created:
            continue
        for text in poll_data["choices"]:
            Choice.objects.create(poll=poll, text=text)
