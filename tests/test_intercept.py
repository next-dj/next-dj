from pathlib import Path

import pytest

from next.intercept import Intercept, InterceptTable


def _rule(target: str, name: str, marker: str = "(.)") -> Intercept:
    return Intercept(
        Path(f"/tree/{target}/page.py"), Path(f"/tree/{name}/page.py"), marker
    )


@pytest.fixture()
def table() -> InterceptTable:
    return InterceptTable()


class TestPublish:
    """A publish replaces the rules of one page root in a single swap."""

    def test_a_new_table_is_empty(self, table: InterceptTable) -> None:
        assert table.all() == ()
        assert table.of(Path("/tree/x/page.py")) == ()

    def test_published_rules_are_found_by_target(self, table: InterceptTable) -> None:
        rule = _rule("feed", "feed/(.)photo")

        table.publish(Path("/tree"), [rule])

        assert table.of(rule.target_file) == (rule,)
        assert table.all() == (rule,)

    def test_a_miss_answers_an_empty_tuple(self, table: InterceptTable) -> None:
        table.publish(Path("/tree"), [_rule("feed", "feed/(.)photo")])

        assert table.of(Path("/tree/other/page.py")) == ()

    def test_a_republish_keeps_the_rules_other_roots_hold_over_a_target(
        self, table: InterceptTable
    ) -> None:
        one = _rule("feed", "feed/(.)alpha")
        two = _rule("feed", "feed/(.)zeta")
        table.publish(Path("/one"), [one])
        table.publish(Path("/two"), [two])

        table.publish(Path("/one"), [])

        assert table.of(one.target_file) == (two,)

    def test_a_republish_moves_a_rule_to_its_new_target(
        self, table: InterceptTable
    ) -> None:
        old = _rule("feed", "feed/(.)photo")
        new = _rule("blog", "blog/(.)post")
        table.publish(Path("/tree"), [old])

        table.publish(Path("/tree"), [new])

        assert table.of(old.target_file) == ()
        assert table.all() == (new,)

    def test_the_rules_of_one_target_follow_the_file_order(
        self, table: InterceptTable
    ) -> None:
        late = _rule("feed", "feed/(.)zeta")
        early = _rule("feed", "feed/(.)alpha")
        middle = _rule("feed", "feed/(.)mid")

        table.publish(Path("/tree"), [late, early, middle])

        assert table.of(late.target_file) == (early, middle, late)

    def test_equal_rules_publish_once(self, table: InterceptTable) -> None:
        rule = _rule("feed", "feed/(.)photo")

        table.publish(Path("/tree"), [rule, _rule("feed", "feed/(.)photo")])

        assert table.all() == (rule,)

    def test_a_second_publish_of_a_root_replaces_its_rules(
        self, table: InterceptTable
    ) -> None:
        old = _rule("feed", "feed/(.)old")
        new = _rule("feed", "feed/(.)new")
        table.publish(Path("/tree"), [old])

        table.publish(Path("/tree"), [new])

        assert table.of(new.target_file) == (new,)
        assert table.all() == (new,)

    def test_an_empty_publish_clears_the_root(self, table: InterceptTable) -> None:
        rule = _rule("feed", "feed/(.)photo")
        table.publish(Path("/tree"), [rule])

        table.publish(Path("/tree"), [])

        assert table.of(rule.target_file) == ()
        assert table.all() == ()

    def test_two_roots_merge_and_replace_independently(
        self, table: InterceptTable
    ) -> None:
        first = _rule("feed", "feed/(.)photo")
        second = _rule("blog", "blog/(.)post")
        table.publish(Path("/one"), [first])
        table.publish(Path("/two"), [second])

        assert sorted(table.all(), key=lambda rule: rule.file) == [second, first]

        table.publish(Path("/one"), [])

        assert table.all() == (second,)
        assert table.of(first.target_file) == ()

    def test_rules_of_two_roots_over_one_target_merge_in_file_order(
        self, table: InterceptTable
    ) -> None:
        beta = _rule("feed", "feed/(.)beta")
        alpha = _rule("feed", "feed/(.)alpha")
        table.publish(Path("/one"), [beta])
        table.publish(Path("/two"), [alpha])

        assert table.of(alpha.target_file) == (alpha, beta)

    def test_a_reader_keeps_the_tuple_it_took_before_a_publish(
        self, table: InterceptTable
    ) -> None:
        rule = _rule("feed", "feed/(.)photo")
        table.publish(Path("/tree"), [rule])
        held = table.of(rule.target_file)

        table.publish(Path("/tree"), [])

        assert held == (rule,)


class TestClear:
    """A clear drops every root."""

    def test_clear_drops_every_rule(self, table: InterceptTable) -> None:
        rule = _rule("feed", "feed/(.)photo")
        table.publish(Path("/one"), [rule])
        table.publish(Path("/two"), [_rule("blog", "blog/(.)post")])

        table.clear()

        assert table.all() == ()
        assert table.of(rule.target_file) == ()

    def test_clear_bumps_the_generation(self, table: InterceptTable) -> None:
        before = table.generation

        table.clear()

        assert table.generation == before + 1

    def test_a_publish_leaves_the_generation(self, table: InterceptTable) -> None:
        table.publish(Path("/tree"), [_rule("feed", "feed/(.)photo")])

        assert table.generation == 0


class TestGeneration:
    """A publish naming a stale generation is refused and changes nothing."""

    def test_no_generation_always_publishes(self, table: InterceptTable) -> None:
        table.clear()

        assert table.publish(Path("/tree"), [_rule("feed", "feed/(.)photo")]) is True
        assert len(table.all()) == 1

    def test_the_current_generation_publishes(self, table: InterceptTable) -> None:
        table.clear()

        published = table.publish(
            Path("/tree"), [_rule("feed", "feed/(.)photo")], generation=table.generation
        )

        assert published is True
        assert len(table.all()) == 1

    def test_a_stale_generation_is_refused(self, table: InterceptTable) -> None:
        stale = table.generation
        table.clear()

        published = table.publish(
            Path("/tree"), [_rule("feed", "feed/(.)photo")], generation=stale
        )

        assert published is False
        assert table.all() == ()
