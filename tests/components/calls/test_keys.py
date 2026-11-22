import pytest
from django.contrib.auth.models import AnonymousUser
from django.template import Context, Template
from django.template.base import UNKNOWN_SOURCE

from next.defer import stable_key
from next.suspense.assembly import finish, run_sequentially
from next.suspense.components import deferring
from tests.components.sites import FIRST_KEY, ComponentSite, Member, template_engine


class TestKey:
    """The key is positional, the same for every response and every user."""

    def test_two_renders_for_two_users_share_the_key(
        self, site: ComponentSite, request_for
    ) -> None:
        site.add("card", "C", loading="L")
        source = Template("{% component 'card' %}{% component 'card' %}")
        keys = []
        for user in (AnonymousUser(), Member()):
            with deferring() as held:
                source.render(Context(site.context(request=request_for(user))))
            assert held.session is not None
            keys.append([job.key for job in held.session.jobs])
        assert keys[0] == keys[1]
        assert keys[0] == [FIRST_KEY, stable_key(UNKNOWN_SOURCE, 1)]

    def test_a_keyed_row_appends_its_key(self, site: ComponentSite) -> None:
        site.add("card", "C", loading="L")
        _, session = site.deferred(
            "{% for p in items %}<li class='r' data-next-key=\"row-{{ p }}\">"
            "<span>{% component 'card' %}</span></li>{% endfor %}",
            items=["a", "b"],
        )
        assert [job.key for job in session.jobs] == [
            f"{FIRST_KEY}:row-a",
            f"{FIRST_KEY}:row-b",
        ]

    @pytest.mark.parametrize(
        "source",
        [
            "{% for p in items %}<li>{% component 'card' %}</li>{% endfor %}",
            "<li data-next-key='x'>{% component 'card' %}</li>",
            "{% for p in items %}{% endfor %}{% component 'card' %}",
            (
                "{% for p in items %}<li data-next-key='{{ p }}'></li>"
                "{% component 'card' %}{% endfor %}"
            ),
        ],
    )
    def test_a_call_outside_a_keyed_row_keeps_the_site_key(
        self, site: ComponentSite, source: str
    ) -> None:
        site.add("card", "C", loading="L")
        _, session = site.deferred(source, items=["a"])
        assert [job.key for job in session.jobs] == [FIRST_KEY]

    @pytest.mark.parametrize(
        ("row", "key"),
        [
            ("<li data-next-key='{{ p }}'></span>", "a"),
            ("<li data-next-key='const'><b></b>", "const"),
            ('<li data-next-key="{{ p }}-{{ p }}"><img src="x">', "a-a"),
            ("<li data-next-key='{{ p }}'><!-- <tr data-next-key='old'> -->", "a"),
            ("<li data-next-key='{{ p }}'><input data-next-key='void'>", "a"),
            (
                "<li data-next-key='{{ p }}'><svg><path data-next-key='closed'/></svg>",
                "a",
            ),
            ("<li data-next-key='{{ p }}'><div data-next-key='open'/>", "open"),
            (
                (
                    "<li data-next-key='{{ p }}'><span data-next-key='inner'>"
                    "<script>let s = '</span>'</script>"
                ),
                "inner",
            ),
            (
                (
                    "<li data-next-key='{{ p }}'><span data-next-key='inner'>"
                    "<title></span></title>"
                ),
                "inner",
            ),
        ],
    )
    def test_the_row_key_reads_the_open_element(
        self, site: ComponentSite, row: str, key: str
    ) -> None:
        site.add("card", "C", loading="L")
        _, session = site.deferred(
            f"{{% for p in items %}}{row}{{% component 'card' %}}{{% endfor %}}",
            items=["a"],
        )
        assert [job.key for job in session.jobs] == [f"{FIRST_KEY}:{key}"]

    def test_a_row_inside_a_block_is_found(self, site: ComponentSite) -> None:
        site.add("card", "C", loading="L")
        engine = template_engine(
            {
                "base.html": "<ul>{% block rows %}{% endblock %}</ul>",
                "page.html": "{% extends 'base.html' %}{% block rows %}"
                "{% for p in items %}<li data-next-key='{{ p }}'>"
                "{% component 'card' %}</li>{% endfor %}{% endblock %}",
            }
        )
        with deferring() as held:
            engine.get_template("page.html").render(Context(site.context(items=["a"])))
        assert held.session is not None
        assert held.session.jobs[0].key == f"{stable_key('page.html', 0)}:a"

    def test_the_row_recipe_is_kept_per_template(self, site: ComponentSite) -> None:
        site.add("card", "C", loading="L")
        template = Template(
            "{% for p in items %}<li data-next-key='{{ p }}'>"
            "{% component 'card' %}</li>{% endfor %}"
        )
        for _ in range(2):
            with deferring() as held:
                template.render(Context(site.context(items=["a"])))
            assert held.session is not None
            assert held.session.jobs[0].key == f"{FIRST_KEY}:a"


class TestAnchoredKey:
    """The key hashes the template relative to what holds it, not the host path."""

    def test_an_include_used_twice_keys_each_render(self, site: ComponentSite) -> None:
        site.add("card", "C", loading="L")
        engine = template_engine(
            {
                "page.html": "{% include 'part.html' %}{% include 'part.html' %}",
                "part.html": "{% component 'card' %}",
            },
            cached=True,
        )
        with deferring() as held:
            engine.get_template("page.html").render(Context(site.context()))
        assert held.session is not None
        assert [job.key for job in held.session.jobs] == [
            stable_key("part.html", 0),
            stable_key("part.html", "0.1"),
        ]


class TestCallSiteValues:
    """A job sees what the call site saw, over the processors and through one pass."""

    def test_a_with_value_sits_over_a_processor(
        self, site: ComponentSite, request_for
    ) -> None:
        site.add("card", "<c>{{ stamp }}</c>", loading="L")
        engine = template_engine(
            {"page.html": "{% with stamp='mine' %}{% component 'card' %}{% endwith %}"},
            cached=True,
        )
        with deferring() as held:
            shell = engine.get_template("page.html").render(
                Context(site.context(request=request_for(AnonymousUser())))
            )
        assert held.session is not None
        html = finish(shell, held.session, site.collector, run_sequentially)
        assert "<c>mine</c>" in html

    def test_a_generator_is_read_by_the_job_and_the_shell(
        self, site: ComponentSite
    ) -> None:
        site.add("gen", "<g>{% for r in rows %}{{ r }}{% endfor %}</g>", loading="L")
        html = site.finished(
            "{% component 'gen' %}{% for r in rows %}[{{ r }}]{% endfor %}",
            rows=(x for x in "ab"),
        )
        assert "<g>ab</g>" in html
        assert html.endswith("[a][b]")


class TestCapture:
    """A job reads its call site by value, as the shell left it."""

    def test_each_loop_job_sees_its_own_iteration(self, site: ComponentSite) -> None:
        site.add(
            "card",
            "[{{ forloop.counter }}.{{ forloop.parentloop.counter }}{{ item }}]",
            loading="L",
        )
        html = site.finished(
            "{% for outer in rows %}{% for item in items %}"
            "{% component 'card' %}{% endfor %}{% endfor %}",
            rows=[1, 2],
            items=["a", "b", "c"],
        )
        bodies = [part.split("]")[0] for part in html.split("[")[1:]]
        assert bodies == ["1.1a", "2.1b", "3.1c", "1.2a", "2.2b", "3.2c"]

    def test_a_slot_under_autoescape_off_stays_unescaped(
        self, site: ComponentSite
    ) -> None:
        site.add("card", "<c>{% set_slot 'x' %}</c>", loading="L")
        html = site.finished(
            "{% autoescape off %}{% #component 'card' %}{% #slot 'x' %}{{ h }}"
            "{% /slot %}{% /component %}{% endautoescape %}",
            h="<b>raw</b>",
        )
        assert "<c><b>raw</b></c>" in html

    def test_an_escaped_slot_stays_escaped(self, site: ComponentSite) -> None:
        site.add("card", "<c>{% set_slot 'x' %}</c>", loading="L")
        html = site.finished(
            "{% #component 'card' %}{% #slot 'x' %}{{ h }}{% /slot %}{% /component %}",
            h="<b>",
        )
        assert "<c>&lt;b&gt;</c>" in html

    def test_an_include_in_a_slot_finds_its_engine(self, site: ComponentSite) -> None:
        site.add("card", "<c>{% set_slot 'x' %}</c>", loading="L")
        engine = template_engine(
            {
                "part.html": "<i>{{ who }}</i>",
                "page.html": "{% #component 'card' %}{% #slot 'x' %}"
                "{% include 'part.html' %}{% /slot %}{% /component %}",
            }
        )
        with deferring() as held:
            shell = engine.get_template("page.html").render(
                Context(site.context(who="W"))
            )
        assert held.session is not None
        html = finish(shell, held.session, site.collector, run_sequentially)
        assert "<c><i>W</i></c>" in html

    def test_the_job_reruns_the_context_processors(
        self, site: ComponentSite, request_for
    ) -> None:
        site.add("card", "<c>{{ stamp }}</c>", loading="L{{ stamp }}")
        engine = template_engine({"page.html": "{% component 'card' %}"})
        with deferring() as held:
            shell = engine.get_template("page.html").render(
                Context(site.context(request=request_for(AnonymousUser())))
            )
        assert held.session is not None
        assert 'aria-busy="true">L</div>' in shell
        html = finish(shell, held.session, site.collector, run_sequentially)
        assert "<c>fresh:/</c>" in html
