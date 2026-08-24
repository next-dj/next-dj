from __future__ import annotations

import copy
import json
import sys
from collections import defaultdict
from typing import Any, NamedTuple

import pytest
from django.core.exceptions import ImproperlyConfigured

from next.conf.frozen import FrozenDict, FrozenList, _immutable, freeze


def sample() -> list[dict[str, Any]]:
    """Build a fresh nested settings value, so no test hands another a used one."""
    return [
        {"BACKEND": "next.urls.FileRouterBackend", "OPTIONS": {"SSE": {"MS": 3000}}}
    ]


def cyclic_list() -> list[Any]:
    """Build a list holding itself."""
    value: list[Any] = []
    value.append(value)
    return value


def cyclic_dict() -> dict[str, Any]:
    """Build a dict holding itself."""
    value: dict[str, Any] = {}
    value["self"] = value
    return value


class Point(NamedTuple):
    x: int
    y: int


LIST_MUTATIONS = [
    ("append", lambda x: x.append(1)),
    ("clear", lambda x: x.clear()),
    ("extend", lambda x: x.extend([1])),
    ("insert", lambda x: x.insert(0, 1)),
    ("pop", lambda x: x.pop()),
    ("remove", lambda x: x.remove("a")),
    ("reverse", lambda x: x.reverse()),
    ("sort", lambda x: x.sort()),
    ("__setitem__", lambda x: x.__setitem__(0, "z")),
    ("__delitem__", lambda x: x.__delitem__(0)),
    ("__iadd__", lambda x: x.__iadd__(["z"])),
    ("__imul__", lambda x: x.__imul__(2)),
]

DICT_MUTATIONS = [
    ("clear", lambda x: x.clear()),
    ("pop", lambda x: x.pop("a")),
    ("popitem", lambda x: x.popitem()),
    ("setdefault", lambda x: x.setdefault("b", 1)),
    ("update", lambda x: x.update({"a": 2})),
    ("__setitem__", lambda x: x.__setitem__("a", 2)),
    ("__delitem__", lambda x: x.__delitem__("a")),
    ("__ior__", lambda x: x.__ior__({"a": 2})),
]

# The mutating half of the builtin method inventory, guarded by the subclass.
LIST_GUARDED = frozenset(name for name, _ in LIST_MUTATIONS)
DICT_GUARDED = frozenset(name for name, _ in DICT_MUTATIONS)

# The rest of the inventory, inherited as is. `__init__` is in here because the
# constructor needs it, which leaves re-invoking it by hand as an escape hatch.
LIST_INHERITED = frozenset(
    {
        "__add__",
        "__class__",
        "__class_getitem__",
        "__contains__",
        "__delattr__",
        "__dir__",
        "__doc__",
        "__eq__",
        "__format__",
        "__ge__",
        "__getattribute__",
        "__getitem__",
        "__getstate__",
        "__gt__",
        "__hash__",
        "__init__",
        "__init_subclass__",
        "__iter__",
        "__le__",
        "__len__",
        "__lt__",
        "__mul__",
        "__ne__",
        "__new__",
        "__reduce__",
        "__reduce_ex__",
        "__repr__",
        "__reversed__",
        "__rmul__",
        "__setattr__",
        "__sizeof__",
        "__str__",
        "__subclasshook__",
        "copy",
        "count",
        "index",
    }
)

DICT_INHERITED = frozenset(
    {
        "__class__",
        "__class_getitem__",
        "__contains__",
        "__delattr__",
        "__dir__",
        "__doc__",
        "__eq__",
        "__format__",
        "__ge__",
        "__getattribute__",
        "__getitem__",
        "__getstate__",
        "__gt__",
        "__hash__",
        "__init__",
        "__init_subclass__",
        "__iter__",
        "__le__",
        "__len__",
        "__lt__",
        "__ne__",
        "__new__",
        "__or__",
        "__reduce__",
        "__reduce_ex__",
        "__repr__",
        "__reversed__",
        "__ror__",
        "__setattr__",
        "__sizeof__",
        "__str__",
        "__subclasshook__",
        "copy",
        "fromkeys",
        "get",
        "items",
        "keys",
        "values",
    }
)

INVENTORIES = [
    pytest.param(list, LIST_GUARDED, LIST_INHERITED, id="list"),
    pytest.param(dict, DICT_GUARDED, DICT_INHERITED, id="dict"),
]

GUARD_SETS = [
    pytest.param(FrozenList, LIST_GUARDED, id="list"),
    pytest.param(FrozenDict, DICT_GUARDED, id="dict"),
]

CYCLES = [pytest.param(cyclic_list, id="list"), pytest.param(cyclic_dict, id="dict")]


class TestFrozenListMutators:
    """Every list mutator raises TypeError and leaves the contents alone."""

    @pytest.mark.parametrize(
        ("name", "mutate"), LIST_MUTATIONS, ids=[name for name, _ in LIST_MUTATIONS]
    )
    def test_mutation_raises(self, name, mutate) -> None:
        frozen = FrozenList(["a", "b"])
        with pytest.raises(TypeError, match="immutable"):
            mutate(frozen)
        assert frozen == ["a", "b"]

    def test_augmented_assignment_raises(self) -> None:
        frozen = FrozenList(["a"])
        with pytest.raises(TypeError, match="immutable"):
            frozen += ["b"]

    def test_reads_still_work(self) -> None:
        frozen = FrozenList(["a", "b"])
        assert frozen[0] == "a"
        assert len(frozen) == 2
        assert list(reversed(frozen)) == ["b", "a"]
        assert frozen.index("b") == 1
        assert frozen.count("a") == 1


class TestFrozenDictMutators:
    """Every dict mutator raises TypeError and leaves the contents alone."""

    @pytest.mark.parametrize(
        ("name", "mutate"), DICT_MUTATIONS, ids=[name for name, _ in DICT_MUTATIONS]
    )
    def test_mutation_raises(self, name, mutate) -> None:
        frozen = FrozenDict({"a": 1})
        with pytest.raises(TypeError, match="immutable"):
            mutate(frozen)
        assert frozen == {"a": 1}

    def test_augmented_or_raises(self) -> None:
        frozen = FrozenDict({"a": 1})
        with pytest.raises(TypeError, match="immutable"):
            frozen |= {"b": 2}

    def test_reads_still_work(self) -> None:
        frozen = FrozenDict({"a": 1})
        assert frozen["a"] == 1
        assert frozen.get("b", "fallback") == "fallback"
        assert list(frozen.items()) == [("a", 1)]
        assert frozen | {"b": 2} == {"a": 1, "b": 2}

    def test_fromkeys_cannot_fill_the_subclass(self) -> None:
        with pytest.raises(TypeError, match="immutable"):
            FrozenDict.fromkeys(["a"])


class TestMutatorInventory:
    """The guarded mutators are pinned against the whole builtin inventory.

    A method CPython adds later lands in neither half and fails this, so it
    reaches a person instead of silently widening the hole.
    """

    @pytest.mark.parametrize(("builtin", "guarded", "inherited"), INVENTORIES)
    def test_inventory_covers_every_builtin_method(
        self, builtin, guarded, inherited
    ) -> None:
        assert guarded | inherited == set(dir(builtin))

    @pytest.mark.parametrize(("frozen_type", "guarded"), GUARD_SETS)
    def test_exactly_the_pinned_mutators_are_guarded(
        self, frozen_type, guarded
    ) -> None:
        overridden = {
            name for name, value in vars(frozen_type).items() if value is _immutable
        }
        assert overridden == guarded


class TestFrozenContainersStayBuiltins:
    """Frozen containers pass builtin isinstance guards and compare as builtins."""

    def test_isinstance_of_builtins(self) -> None:
        frozen = freeze(sample())
        assert isinstance(frozen, list)
        assert isinstance(frozen[0], dict)

    def test_equality_with_plain_containers(self) -> None:
        source = sample()
        frozen = freeze(source)
        assert frozen == source
        assert source.__eq__(frozen) is True
        assert frozen[0] == source[0]

    def test_repr_matches_plain_containers(self) -> None:
        source = sample()
        assert repr(freeze(source)) == repr(source)

    def test_json_serialisable(self) -> None:
        source = sample()
        assert json.dumps(freeze(source)) == json.dumps(source)

    def test_memory_footprint_matches_the_builtins(self) -> None:
        assert sys.getsizeof(FrozenList([1, 2, 3])) == sys.getsizeof([1, 2, 3])
        assert sys.getsizeof(FrozenDict({"a": 1})) == sys.getsizeof({"a": 1})

    def test_plain_copies_are_mutable(self) -> None:
        frozen = freeze(sample())
        loose = list(frozen)
        loose.append({})
        entry = dict(frozen[0])
        entry["BACKEND"] = "other"
        assert len(loose) == 2
        assert entry["BACKEND"] == "other"

    def test_slices_and_copy_hand_back_plain_containers(self) -> None:
        source = sample()
        frozen = freeze(source)
        assert type(frozen[:1]) is list
        assert type(frozen.copy()) is list
        assert type(frozen[0].copy()) is dict
        frozen[:1].append({})
        frozen[0].copy()["BACKEND"] = "other"
        assert frozen == source


class TestFrozenContainersRoundTrip:
    """copy, deepcopy, and pickle rebuild through the constructor."""

    def test_shallow_copy(self) -> None:
        source = sample()
        frozen = freeze(source)
        clone = copy.copy(frozen)
        assert clone == source
        assert clone[0] is frozen[0]

    def test_deep_copy(self) -> None:
        source = sample()
        frozen = freeze(source)
        clone = copy.deepcopy(frozen)
        assert clone == source
        assert clone[0] is not frozen[0]
        assert isinstance(clone[0], FrozenDict)

    def test_reduce_names_the_constructor(self) -> None:
        source = sample()
        factory, args = freeze(source).__reduce__()
        assert factory is FrozenList
        assert factory(*args) == source
        assert type(args[0]) is list

    def test_dict_reduce_names_the_constructor(self) -> None:
        factory, args = FrozenDict({"a": 1}).__reduce__()
        assert factory is FrozenDict
        assert factory(*args) == {"a": 1}
        assert type(args[0]) is dict

    def test_copies_stay_frozen(self) -> None:
        clone = copy.deepcopy(freeze(sample()))
        with pytest.raises(TypeError, match="immutable"):
            clone.append({})


class TestFreeze:
    """freeze rebuilds containers and passes scalars through."""

    @pytest.mark.parametrize(
        "value", ["text", 3, True, None, 1.5], ids=["str", "int", "bool", "none", "flo"]
    )
    def test_scalars_pass_through(self, value) -> None:
        assert freeze(value) is value

    def test_every_level_is_a_new_container(self) -> None:
        source = sample()
        frozen = freeze(source)
        assert frozen is not source
        assert frozen[0] is not source[0]
        assert frozen[0]["OPTIONS"] is not source[0]["OPTIONS"]
        assert frozen[0]["OPTIONS"]["SSE"] is not source[0]["OPTIONS"]["SSE"]

    def test_source_mutation_does_not_reach_the_frozen_copy(self) -> None:
        source = [{"OPTIONS": {"A": 1}}]
        frozen = freeze(source)
        source[0]["OPTIONS"]["A"] = 2
        source.append({})
        assert frozen == [{"OPTIONS": {"A": 1}}]

    def test_mutable_leaf_is_copied_not_aliased(self) -> None:
        tags = {"a"}
        frozen = freeze({"OPTIONS": {"TAGS": tags}})
        tags.add("b")
        assert frozen["OPTIONS"]["TAGS"] == {"a"}
        assert frozen["OPTIONS"]["TAGS"] is not tags

    def test_mutable_leaf_stays_editable_inside_the_frozen_value(self) -> None:
        """The freeze reaches lists and dicts only, so a set leaf still edits.

        Nothing leaks back to the caller, but the leaf itself is not frozen,
        and this is where that boundary sits.
        """
        tags = {"a"}
        frozen = freeze({"OPTIONS": {"TAGS": tags}})
        frozen["OPTIONS"]["TAGS"].add("LEAKED")
        assert frozen["OPTIONS"]["TAGS"] == {"a", "LEAKED"}
        assert tags == {"a"}

    def test_tuple_is_rebuilt_with_frozen_items(self) -> None:
        source = ({"A": 1},)
        frozen = freeze(source)
        assert isinstance(frozen, tuple)
        assert frozen[0] is not source[0]
        with pytest.raises(TypeError, match="immutable"):
            frozen[0]["A"] = 2

    def test_container_subclasses_keep_their_type(self) -> None:
        counts: defaultdict[str, list[int]] = defaultdict(list)
        counts["a"].append(1)
        frozen = freeze({"p": Point(1, 2), "d": counts})
        assert type(frozen["p"]) is Point
        assert frozen["p"].x == 1
        assert type(frozen["d"]) is defaultdict
        assert frozen["d"].default_factory is list

    def test_subclass_leaf_is_still_copied(self) -> None:
        counts: defaultdict[str, list[int]] = defaultdict(list)
        frozen = freeze({"d": counts})
        counts["a"].append(1)
        assert frozen["d"] == {}

    def test_shared_subtree_stays_shared(self) -> None:
        shared = {"A": 1}
        frozen = freeze([shared, shared])
        assert frozen[0] is frozen[1]

    @pytest.mark.parametrize("build", CYCLES)
    def test_self_referential_value_is_rejected(self, build) -> None:
        with pytest.raises(ImproperlyConfigured, match="self-referential"):
            freeze(build())
