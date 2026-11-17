"""Tests for doctrine ordering and the Pinned section."""

import builtins
import inspect
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from django.http import QueryDict

from aa_fleet_hub import views
from aa_fleet_hub.models import DOCTRINE_ORDER, Doctrine


def _doctrine(name, tag="", sort_order=0, pinned=False):
    d = SimpleNamespace(name=name, tag=tag, sort_order=sort_order, pinned=pinned)
    d.tag_label = Doctrine.tag_label.fget(d)
    return d


def _sections(doctrines):
    """Group plain doctrines and return [(label, [names], pinned)] plus the tags."""
    ctx = views._group_by_tag(doctrines, lambda doctrine: doctrine)
    sections = [
        (label, [d.name for d in items], pinned)
        for label, items, pinned in ctx["doctrine_groups"]
    ]
    return sections, ctx["doctrine_tags"]


def test_order_is_pinned_then_sort_order_then_name_ignoring_case():
    assert Doctrine._meta.ordering == DOCTRINE_ORDER
    sql = str(Doctrine.objects.all().query)
    order_by = sql.split("ORDER BY", 1)[1]
    assert order_by.index('"pinned" DESC') < order_by.index('"sort_order" ASC')
    assert order_by.index('"sort_order" ASC') < order_by.index(
        'LOWER("aa_fleet_hub_doctrine"."name") ASC'
    )
    assert order_by.rstrip().endswith('"id" ASC')


@pytest.mark.parametrize("officer", [True, False])
def test_manageable_doctrines_keep_the_shared_order(officer):
    # The fit manager branch adds DISTINCT, which must not drop the order.
    user = MagicMock()
    user.has_perm.return_value = officer
    user.groups.all.return_value = [1]
    sql = str(views._manageable_doctrines(user).query)
    order_by = sql.split("ORDER BY", 1)[1]
    assert order_by.lstrip().startswith('"aa_fleet_hub_doctrine"."pinned" DESC')
    assert 'LOWER("aa_fleet_hub_doctrine"."name") ASC' in order_by


def test_pinned_doctrines_get_their_own_section_first():
    sections, _ = _sections(
        [
            _doctrine("Home Defense", "Defense", pinned=True),
            _doctrine("Ferox Fleet", "Mainline"),
            _doctrine("Muninn Fleet", "Mainline"),
        ]
    )
    assert sections == [
        ("Pinned", ["Home Defense"], True),
        ("Mainline", ["Ferox Fleet", "Muninn Fleet"], False),
    ]


def test_no_pinned_section_without_pinned_doctrines():
    sections, _ = _sections([_doctrine("Ferox Fleet", "Mainline")])
    assert sections == [("Mainline", ["Ferox Fleet"], False)]


def test_tag_only_pinned_doctrines_carry_is_still_a_filter():
    _, tags = _sections(
        [
            _doctrine("Home Defense", "Defense", sort_order=5, pinned=True),
            _doctrine("Ferox Fleet", "Mainline", sort_order=1),
        ]
    )
    assert tags == ["Mainline", "Defense"]


def test_tag_groups_follow_lowest_sort_order_then_alphabetical():
    sections, tags = _sections(
        [
            _doctrine("Alpha", "zeta", sort_order=1),
            _doctrine("Bravo", "Capitals", sort_order=2),
            _doctrine("Charlie", "alpha", sort_order=2),
            _doctrine("Delta", "Capitals", sort_order=5),
        ]
    )
    assert [label for label, _, _ in sections] == ["zeta", "alpha", "Capitals"]
    assert tags == ["zeta", "alpha", "Capitals"]
    assert sections[2][1] == ["Bravo", "Delta"]


def test_pinned_doctrine_does_not_pull_its_tag_group_up():
    sections, _ = _sections(
        [
            _doctrine("Home Defense", "Defense", sort_order=0, pinned=True),
            _doctrine("Ferox Fleet", "Mainline", sort_order=1),
            _doctrine("Defense Backup", "Defense", sort_order=3),
        ]
    )
    assert [label for label, _, _ in sections] == ["Pinned", "Mainline", "Defense"]


def test_untagged_is_always_last():
    sections, tags = _sections(
        [
            _doctrine("Loose", "  ", sort_order=0),
            _doctrine("Ferox Fleet", "Mainline", sort_order=9),
        ]
    )
    assert [label for label, _, _ in sections] == ["Mainline", "Untagged"]
    assert tags == ["Mainline", "Untagged"]


def test_pinned_doctrine_is_not_repeated_in_its_tag_section():
    sections, _ = _sections(
        [
            _doctrine("Home Defense", "Defense", pinned=True),
            _doctrine("Defense Backup", "Defense", sort_order=1),
        ]
    )
    assert sections == [
        ("Pinned", ["Home Defense"], True),
        ("Defense", ["Defense Backup"], False),
    ]


def test_items_keep_their_query_order_inside_a_section():
    # Already ordered by Sort Order then name, as the query returns them.
    sections, _ = _sections(
        [
            _doctrine("Zealot Fleet", "Mainline", sort_order=1),
            _doctrine("Ferox Fleet", "Mainline", sort_order=4),
            _doctrine("Apoc Fleet", "Mainline", sort_order=9),
        ]
    )
    assert sections[0][1] == ["Zealot Fleet", "Ferox Fleet", "Apoc Fleet"]


@pytest.mark.parametrize("reverse", [False, True])
def test_tags_differing_only_by_case_have_a_fixed_order(monkeypatch, reverse):
    # Tags reach sorted() from a set, whose order follows the per-process string
    # hash. Feeding sorted() both ways round makes any tie in the key show up.
    def sorted_from(iterable, key):
        items = list(iterable)
        return builtins.sorted(items[::-1] if reverse else items, key=key)

    monkeypatch.setattr(views, "sorted", sorted_from, raising=False)
    sections, tags = _sections(
        [
            _doctrine("Ferox Fleet", "mainline", sort_order=1),
            _doctrine("Muninn Fleet", "Mainline", sort_order=1),
        ]
    )
    assert tags == ["Mainline", "mainline"]
    assert [label for label, _, _ in sections] == ["Mainline", "mainline"]


def test_save_doctrine_reads_the_pinned_checkbox():
    def save(post):
        request = MagicMock()
        request.POST = QueryDict(post)
        doctrine = MagicMock()
        with patch("aa_fleet_hub.tasks.invalidate_readiness_cache"):
            return inspect.unwrap(views._save_doctrine)(request, doctrine)

    assert save("name=Home+Defense&pinned=on").pinned is True
    assert save("name=Home+Defense").pinned is False
