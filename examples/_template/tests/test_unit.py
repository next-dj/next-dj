from myapp.models import Placeholder


def test_placeholder_str_returns_label() -> None:
    row = Placeholder(label="example")
    assert str(row) == "example"
