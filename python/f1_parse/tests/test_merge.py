"""deep_merge semantics, pinned one by one — these ARE the contract layer
two will build its running snapshots on (see the merge module docstring)."""

from __future__ import annotations

from pydantic import JsonValue

from f1_parse import deep_merge


def test_nested_partial_update_keeps_untouched_fields():
    base: JsonValue = {
        "44": {"GapToLeader": "+2.259", "InPit": False, "Best": {"Value": "1:27.1"}},
        "1": {"Position": "1"},
    }
    delta: JsonValue = {"44": {"GapToLeader": "+1.902"}}

    merged = deep_merge(base, delta)

    assert merged == {
        "44": {"GapToLeader": "+1.902", "InPit": False, "Best": {"Value": "1:27.1"}},
        "1": {"Position": "1"},
    }


def test_shallow_update_would_have_lost_data_merge_does_not():
    # The exact failure mode the feed punishes: driver "44"'s delta has one
    # key; a dict.update at the top level would nuke the sibling fields.
    base: JsonValue = {"44": {"A": 1, "B": 2, "C": 3}}
    merged = deep_merge(base, {"44": {"B": 99}})
    assert merged == {"44": {"A": 1, "B": 99, "C": 3}}


def test_null_is_an_explicit_reset_kept_as_none():
    base: JsonValue = {"44": {"GapToLeader": "+2.2", "InPit": True}}
    merged = deep_merge(base, {"44": {"GapToLeader": None}})
    assert merged == {"44": {"GapToLeader": None, "InPit": True}}


def test_empty_dict_resets_the_container():
    base: JsonValue = {"Sectors": {"0": {"Value": "26.1"}, "1": {"Value": "31.0"}}}
    merged = deep_merge(base, {"Sectors": {}})
    assert merged == {"Sectors": {}}


def test_empty_list_resets_the_container():
    base: JsonValue = {"Messages": [{"Category": "Flag"}]}
    merged = deep_merge(base, {"Messages": []})
    assert merged == {"Messages": []}


def test_non_empty_list_replaces_outright():
    base: JsonValue = {"Items": [1, 2, 3]}
    merged = deep_merge(base, {"Items": [9]})
    assert merged == {"Items": [9]}


def test_sparse_list_delta_merges_into_list_by_index():
    # TimingData's sectors: a list in the snapshot, then dict-keyed-by-
    # index deltas ("2" = third sector) that must land in place.
    base: JsonValue = {"Sectors": [{"Value": "26.1"}, {"Value": "31.0"}, {"Value": ""}]}
    delta: JsonValue = {"Sectors": {"2": {"Value": "29.456", "PersonalFastest": True}}}

    merged = deep_merge(base, delta)

    assert merged == {
        "Sectors": [
            {"Value": "26.1"},
            {"Value": "31.0"},
            {"Value": "29.456", "PersonalFastest": True},
        ]
    }


def test_sparse_list_delta_pads_unseen_indices_with_none():
    base: JsonValue = {"Sectors": [{"Value": "26.1"}]}
    merged = deep_merge(base, {"Sectors": {"2": {"Value": "29.0"}}})
    assert merged == {"Sectors": [{"Value": "26.1"}, None, {"Value": "29.0"}]}


def test_delta_onto_missing_key_builds_structure():
    merged = deep_merge({}, {"63": {"KnockedOut": True}})
    assert merged == {"63": {"KnockedOut": True}}


def test_scalar_replaces_scalar():
    assert deep_merge({"NumberOfLaps": 12}, {"NumberOfLaps": 13}) == {"NumberOfLaps": 13}


def test_scalar_replaces_container_when_feed_says_so():
    assert deep_merge({"X": {"a": 1}}, {"X": "gone"}) == {"X": "gone"}


def test_base_is_never_mutated():
    base: JsonValue = {"44": {"A": 1, "Nested": {"B": 2}}}
    deep_merge(base, {"44": {"Nested": {"B": 3}, "A": None}})
    assert base == {"44": {"A": 1, "Nested": {"B": 2}}}


def test_successive_deltas_accumulate():
    state: JsonValue = {}
    state = deep_merge(state, {"44": {"Position": "3", "InPit": False}})
    state = deep_merge(state, {"44": {"GapToLeader": "+1.1"}})
    state = deep_merge(state, {"44": {"Position": "2"}, "1": {"Position": "3"}})
    assert state == {
        "44": {"Position": "2", "InPit": False, "GapToLeader": "+1.1"},
        "1": {"Position": "3"},
    }
