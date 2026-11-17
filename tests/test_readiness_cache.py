"""Tests for the per-doctrine readiness cache split."""

from unittest.mock import MagicMock, patch

import pytest
from django.core.cache import cache

from aa_fleet_hub import tasks
from aa_fleet_hub.tasks import (
    LEGACY_READINESS_CACHE_KEY,
    _doctrine_readiness_key,
    _store_readiness,
    get_all_doctrine_readiness,
    get_doctrine_readiness,
    get_readiness_summary,
)


def _doctrine(name, chars):
    return {
        "name": name,
        "total": len(chars),
        "fits": [
            {
                "fit_id": 1,
                "fit_name": f"{name} fit",
                "tier_labels": ["Min"],
                "tier_counts": {"Min": len(chars), "none": 0},
                "display_tiers": [],
                "per_char": [{"character_name": c} for c in chars],
                "gap_analysis": [{"name": "Some Skill", "count": 1}],
            }
        ],
    }


@pytest.fixture(autouse=True)
def _clear_cache():
    cache.clear()
    yield
    cache.clear()


def test_cold_cache_reports_not_computed():
    assert get_readiness_summary() is None
    assert get_doctrine_readiness(1) == (None, False)


def test_summary_drops_per_character_rows():
    _store_readiness({1: _doctrine("Ferox", ["A", "B"])})
    fit = get_readiness_summary()[1]["fits"][0]
    assert "per_char" not in fit
    assert "gap_analysis" not in fit
    assert fit["tier_counts"] == {"Min": 2, "none": 0}
    assert get_readiness_summary()[1]["total"] == 2


def test_doctrine_entry_keeps_full_data():
    _store_readiness({1: _doctrine("Ferox", ["A", "B"])})
    data, computed = get_doctrine_readiness(1)
    assert computed
    assert [c["character_name"] for c in data["fits"][0]["per_char"]] == ["A", "B"]


def test_doctrine_page_reads_only_its_own_entry():
    _store_readiness({1: _doctrine("Ferox", ["A"]), 2: _doctrine("Muninn", ["B"])})
    # Wrap the module's cache reference (not the backend), so only the calls
    # tasks.py makes are recorded, not the backend's own internal get()s.
    with patch.object(tasks, "cache", MagicMock(wraps=cache)) as spy:
        data, _ = get_doctrine_readiness(2)
    assert data["name"] == "Muninn"
    spy.get.assert_not_called()
    (keys,), _ = spy.get_many.call_args
    assert set(keys) == {_doctrine_readiness_key(2), tasks.READINESS_COMPUTED_KEY}


def test_skipped_doctrine_on_warm_cache_is_computed_but_empty():
    # e.g. a draft: the task never caches it, so its page must not queue a
    # recompute on every visit.
    _store_readiness({1: _doctrine("Ferox", ["A"])})
    assert get_doctrine_readiness(99) == (None, True)


def test_empty_result_still_counts_as_computed():
    _store_readiness({})
    assert get_readiness_summary() == {}
    assert get_doctrine_readiness(1) == (None, True)


def test_dropped_doctrines_and_legacy_entry_are_removed():
    cache.set(LEGACY_READINESS_CACHE_KEY, {"old": "blob"})
    _store_readiness({1: _doctrine("Ferox", ["A"]), 2: _doctrine("Muninn", ["B"])})
    _store_readiness({1: _doctrine("Ferox", ["A"])})
    assert cache.get(_doctrine_readiness_key(2)) is None
    assert cache.get(LEGACY_READINESS_CACHE_KEY) is None
    assert list(get_readiness_summary()) == [1]


def test_all_doctrine_readiness_returns_full_entries_in_summary_order():
    _store_readiness({2: _doctrine("Muninn", ["B"]), 1: _doctrine("Ferox", ["A"])})
    everything = get_all_doctrine_readiness()
    assert list(everything) == [2, 1]
    assert everything[1]["fits"][0]["per_char"] == [{"character_name": "A"}]
