from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from types import MappingProxyType
from typing import ClassVar

import pytest
from django.utils.translation import gettext_lazy

from next.pages import ld
from next.pages.metadata.ld import Text, node_type, plain, raw_id


class Availability(StrEnum):
    SOLD_OUT = "https://schema.org/SoldOut"


@dataclass(frozen=True, slots=True, kw_only=True)
class Organization(ld.Node):
    TYPE: ClassVar[str] = "Organization"
    URLS: ClassVar[frozenset[str]] = frozenset({"url", "logo", "same_as"})

    name: Text
    url: str | None = None
    logo: str | ld.Node | ld.Ref | None = None
    same_as: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True, kw_only=True)
class Person(ld.Node):
    TYPE: ClassVar[str] = "Person"
    URLS: ClassVar[frozenset[str]] = frozenset({"url"})

    name: Text
    url: str | None = None


@dataclass(frozen=True, slots=True, kw_only=True)
class Article(ld.Node):
    TYPE: ClassVar[str] = "Article"
    URLS: ClassVar[frozenset[str]] = frozenset({"image"})

    headline: Text
    image: tuple[str, ...] = ()
    author: tuple[Person | ld.Ref, ...] = ()
    publisher: ld.Ref | None = None


@dataclass(frozen=True, slots=True, kw_only=True)
class Question(ld.Node):
    TYPE: ClassVar[str] = "Question"
    KEYS: ClassVar[Mapping[str, str]] = MappingProxyType({"answer": "acceptedAnswer"})
    TEXTS: ClassVar[Mapping[str, str]] = MappingProxyType({"answer": "Answer"})

    name: Text
    answer: Text


def _absolute(url: str) -> str:
    return f"https://acme.example{url}" if url.startswith("/") else url


class TestNode:
    """A node emits camelCase properties, drops what is unset and adds `extra` last."""

    def test_properties_are_camel_cased_and_unset_ones_dropped(self) -> None:
        node = Organization(
            id="#org", name="Acme", same_as=("https://github.com/acme",)
        )
        assert node.as_jsonld() == {
            "@type": "Organization",
            "@id": "#org",
            "name": "Acme",
            "sameAs": ["https://github.com/acme"],
        }

    def test_a_type_override_names_the_subtype(self) -> None:
        node = Organization(name="Shop", type="LocalBusiness")
        assert node.as_jsonld()["@type"] == "LocalBusiness"

    def test_extra_is_merged_last_and_verbatim(self) -> None:
        node = Organization(name="Shop", extra={"name": "Override", "priceRange": "$$"})
        assert node.as_jsonld() == {
            "@type": "Organization",
            "name": "Override",
            "priceRange": "$$",
        }

    def test_a_missing_required_property_is_a_type_error(self) -> None:
        build: type = ld.BreadcrumbList
        with pytest.raises(TypeError, match="items"):
            build()

    def test_a_node_takes_keywords_only(self) -> None:
        build: type = ld.ListItem
        with pytest.raises(TypeError):
            build("Home", 1)

    def test_a_bare_node_is_a_thing(self) -> None:
        assert ld.Node(id="#x").as_jsonld() == {"@type": "Thing", "@id": "#x"}

    def test_a_text_property_nests_under_its_type(self) -> None:
        question = Question(name="Ships to EU?", answer="Yes, in 3 days.")
        assert question.as_jsonld() == {
            "@type": "Question",
            "name": "Ships to EU?",
            "acceptedAnswer": {"@type": "Answer", "text": "Yes, in 3 days."},
        }


class TestUrls:
    """`url` maps the `@id`, every URL property and each nested node."""

    def test_the_id_and_the_url_properties_are_mapped(self) -> None:
        organization = Organization(id="/#org", name="Acme", url="/")
        assert organization.as_jsonld(_absolute) == {
            "@type": "Organization",
            "@id": "https://acme.example/#org",
            "name": "Acme",
            "url": "https://acme.example/",
        }

    def test_a_sequence_of_urls_is_mapped_item_by_item(self) -> None:
        article = Article(headline="H", image=("/a.png", "https://cdn.example/b.png"))
        assert article.as_jsonld(_absolute)["image"] == [
            "https://acme.example/a.png",
            "https://cdn.example/b.png",
        ]

    def test_a_nested_node_and_a_reference_are_rendered_in_place(self) -> None:
        article = Article(
            headline="H",
            author=(Person(name="Ann", url="/ann/"), ld.Ref("#bob")),
            publisher=ld.Ref("/#org"),
        )
        rendered = article.as_jsonld(_absolute)
        assert rendered["author"] == [
            {"@type": "Person", "name": "Ann", "url": "https://acme.example/ann/"},
            {"@id": "#bob"},
        ]
        assert rendered["publisher"] == {"@id": "https://acme.example/#org"}

    def test_a_node_under_a_url_property_renders_through_plain(self) -> None:
        logo = ld.Node(type="ImageObject", id="/#logo", extra={"url": "/logo.png"})
        organization = Organization(name="A", logo=logo)
        assert organization.as_jsonld(_absolute)["logo"] == {
            "@type": "ImageObject",
            "@id": "https://acme.example/#logo",
            "url": "/logo.png",
        }


class TestBreadcrumbList:
    """The breadcrumb list spells the schema.org trail rich results read."""

    def test_the_module_exports_only_the_base_and_the_trail(self) -> None:
        assert set(ld.__all__) == {"BreadcrumbList", "ListItem", "Node", "Ref"}

    def test_a_breadcrumb_list_names_its_elements(self) -> None:
        trail = ld.BreadcrumbList(
            items=(
                ld.ListItem(name="Home", position=1, item="/"),
                ld.ListItem(name="Post", position=2),
            )
        )
        assert trail.as_jsonld(_absolute)["itemListElement"] == [
            {
                "@type": "ListItem",
                "name": "Home",
                "position": 1,
                "item": "https://acme.example/",
            },
            {"@type": "ListItem", "name": "Post", "position": 2},
        ]

    def test_a_lazy_text_survives_until_serialised(self) -> None:
        label = gettext_lazy("Home")
        assert ld.ListItem(name=label, position=1).as_jsonld()["name"] is label


class TestHelpers:
    """The fold and the checks read ids, types and plain forms off either shape."""

    @pytest.mark.parametrize(
        ("item", "expected"),
        [
            (Person(id="#a", name="A"), "#a"),
            (Person(name="A"), None),
            ({"@id": "#b"}, "#b"),
            ({"@id": 1}, None),
            ("text", None),
        ],
        ids=["node", "node_without_id", "mapping", "mapping_non_str", "other"],
    )
    def test_raw_id(self, item: object, expected: str | None) -> None:
        assert raw_id(item) == expected

    @pytest.mark.parametrize(
        ("item", "expected"),
        [
            (Person(name="A"), "Person"),
            (Person(name="A", type="Patient"), "Patient"),
            ({"@type": "Thing"}, "Thing"),
            ({"@type": ["A", "B"]}, None),
            (1, None),
        ],
        ids=["node", "override", "mapping", "mapping_list", "other"],
    )
    def test_node_type(self, item: object, expected: str | None) -> None:
        assert node_type(item) == expected

    def test_plain_renders_nested_nodes_in_containers(self) -> None:
        value = {"a": [ld.Ref("#x"), (Availability.SOLD_OUT,)], "b": 1}
        assert plain(value) == {
            "a": [{"@id": "#x"}, ["https://schema.org/SoldOut"]],
            "b": 1,
        }
