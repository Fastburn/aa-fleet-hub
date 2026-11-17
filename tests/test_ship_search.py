"""Tests for the doctrine search hull lookup."""

from unittest.mock import MagicMock, patch

import pytest
from django.core.cache import cache

from aa_fleet_hub.services import (
    SHIP_SEARCH_MISS_TTL,
    _ship_search_key,
    role_key,
    role_labels,
    ship_search_info,
)

# A slice of EVE's ship market tree: Ships > Cruisers > Advanced Cruisers > ...
MARKET_GROUPS = {
    4: ("Ships", None),
    8: ("Cruisers", 4),
    9: ("Advanced Cruisers", 8),
    10: ("Logistics", 9),
    11: ("Heavy Interdiction Cruisers", 9),
    1381: ("Capital Ships", 4),
    1382: ("Dreadnoughts", 1381),
}


@pytest.fixture(autouse=True)
def load_task():
    """Fresh cache per test, and a fake market group task to catch queued loads."""
    cache.clear()
    with patch("aa_fleet_hub.tasks.load_ship_market_groups") as task:
        yield task
    cache.clear()


def _queued(load_task):
    return [c.args[0] for c in load_task.delay.call_args_list]


def _fake_orm(types, groups=MARKET_GROUPS):
    """Patch EveType/EveMarketGroup lookups; returns the market group mock to count queries."""
    eve_type = MagicMock()
    eve_type.objects.filter.return_value.values_list.return_value = types

    def market_filter(id__in):
        rows = [(mg, *groups[mg]) for mg in id__in if mg in groups]
        result = MagicMock()
        result.values_list.return_value = rows
        return result

    market = MagicMock()
    market.objects.filter.side_effect = market_filter
    return eve_type, market


def _run(types, groups=MARKET_GROUPS, type_ids=None):
    eve_type, market = _fake_orm(types, groups)
    with (
        patch("aa_fleet_hub.services.EveType", eve_type),
        patch("aa_fleet_hub.services.EveMarketGroup", market),
    ):
        ids = [t[0] for t in types] if type_ids is None else type_ids
        return ship_search_info(ids), market


def test_class_is_the_group_just_below_ships():
    info, _ = _run([(11995, "Onyx", 11), (19720, "Revelation", 1382)])
    assert info == {11995: ("Onyx", "Cruisers"), 19720: ("Revelation", "Capital Ships")}


def test_hull_without_market_group_has_no_class():
    info, _ = _run([(2015, "Some Hull", None)])
    assert info == {2015: ("Some Hull", "")}


def test_broken_chain_has_no_class():
    # Parent group 9 missing from the table: don't guess a class.
    groups = {k: v for k, v in MARKET_GROUPS.items() if k != 9}
    info, _ = _run([(11995, "Onyx", 11)], groups)
    assert info == {11995: ("Onyx", "")}


def test_queries_follow_tree_depth_not_hull_count():
    hulls = [(1000 + i, f"Hull {i}", 10 if i % 2 else 11) for i in range(50)]
    info, market = _run(hulls)
    assert all(ship_class == "Cruisers" for _, ship_class in info.values())
    # Logistics/HIC -> Advanced Cruisers -> Cruisers -> Ships: one query per level.
    assert market.objects.filter.call_count == 4


def test_type_whose_group_is_the_root_has_no_class():
    info, _ = _run([(1, "Odd Hull", 4)])
    assert info == {1: ("Odd Hull", "")}


def test_classed_hulls_are_cached(load_task):
    hulls = [(11995, "Onyx", 11)]
    _run(hulls)
    info, market = _run(hulls)
    assert info == {11995: ("Onyx", "Cruisers")}
    assert market.objects.filter.call_count == 0
    assert _queued(load_task) == []


def test_unclassed_and_unknown_hulls_queue_one_market_group_load(load_task):
    # 2015 has no market group; 999 isn't in the EveType table at all.
    info, _ = _run([(2015, "Some Hull", None)], type_ids=[2015, 999, 0])
    assert info == {2015: ("Some Hull", ""), 999: ("", "")}
    assert _queued(load_task) == [[999, 2015]]


def test_unclassed_hulls_are_cached_briefly(load_task):
    hulls = [(2015, "Some Hull", None)]
    with patch(
        "aa_fleet_hub.services.cache.set_many", wraps=cache.set_many
    ) as set_many:
        _run(hulls, type_ids=[2015, 999])
    entries, ttl = set_many.call_args_list[-1].args
    assert sorted(entries) == sorted(_ship_search_key(t) for t in (2015, 999))
    assert ttl == SHIP_SEARCH_MISS_TTL
    # A second view inside the window is served from the cache.
    info, market = _run(hulls, type_ids=[2015, 999])
    assert info == {2015: ("Some Hull", ""), 999: ("", "")}
    assert market.objects.filter.call_count == 0
    assert _queued(load_task) == [[999, 2015]]


def test_load_is_not_queued_again_within_six_hours(load_task):
    hulls = [(2015, "Some Hull", None)]
    _run(hulls)
    cache.delete(_ship_search_key(2015))  # miss entry expired, load lock still held
    _run(hulls)
    assert _queued(load_task) == [[2015]]


def test_failed_queue_releases_the_lock(load_task):
    hulls = [(2015, "Some Hull", None)]
    load_task.delay.side_effect = ConnectionError("broker down")
    _run(hulls)
    load_task.delay.side_effect = None
    cache.delete(_ship_search_key(2015))
    _run(hulls)
    assert _queued(load_task) == [[2015], [2015]]


def test_role_key_ignores_case_and_spacing():
    assert role_key("Logi") == role_key(" logi ") == role_key("LOGI") == "logi"
    assert role_key("Heavy  Tackle") == "heavy tackle"
    assert role_key("") == role_key(None) == ""


def test_role_labels_group_variants_under_most_common_spelling():
    labels = role_labels(["Logi", "logi", "Logi ", "DPS", "", "  ", "Logistics"])
    # Logistics stays separate: only capitals and spacing are folded.
    assert labels == {"dps": "DPS", "logi": "Logi", "logistics": "Logistics"}
    assert list(labels) == ["dps", "logi", "logistics"]
