import pytest
from django.contrib.auth.models import Group
from django.core.paginator import Paginator
from django.db import connection
from django.test.utils import CaptureQueriesContext

from next.defer.connections import fresh_querysets


@pytest.mark.django_db()
class TestFreshQuerysets:
    """Each job gets lazy clones of the shell's querysets, at no query."""

    def test_unevaluated_querysets_and_pages_are_cloned_without_a_query(self) -> None:
        Group.objects.create(name="one")
        evaluated = Group.objects.all()
        list(evaluated)
        lazy = Group.objects.filter(name="one")
        page = Paginator(Group.objects.order_by("pk"), 10).page(1)
        done = Paginator(Group.objects.order_by("pk"), 10).page(1)
        list(done)
        data = {
            "lazy": lazy,
            "evaluated": evaluated,
            "page": page,
            "done": done,
            "n": 1,
        }
        with CaptureQueriesContext(connection) as queries:
            fresh = fresh_querysets(data)
        assert queries.captured_queries == []
        assert fresh["lazy"] is not lazy
        assert str(fresh["lazy"].query) == str(lazy.query)
        assert fresh["evaluated"] is evaluated
        assert fresh["page"] is not page
        assert fresh["page"].object_list is not page.object_list
        assert fresh["page"].number == page.number
        assert fresh["page"].paginator is page.paginator
        assert fresh["done"] is done
        assert fresh["n"] == 1
        assert data["lazy"] is lazy
        assert [group.name for group in fresh["page"]] == ["one"]

    def test_a_page_of_a_list_is_kept(self) -> None:
        page = Paginator(["a", "b"], 1).page(1)
        assert fresh_querysets({"page": page})["page"] is page
