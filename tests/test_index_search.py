"""Tests for the doctrine search data the Doctrines page view hands its template."""

import inspect
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from django.http import QueryDict

from aa_fleet_hub import views
from aa_fleet_hub.models import Doctrine


def _all(*items):
    return SimpleNamespace(all=lambda: list(items))


def _fit(ship_type_id, role="", is_skill_plan=False):
    return SimpleNamespace(
        ship_type_id=ship_type_id, role=role, is_skill_plan=is_skill_plan, tiers=_all()
    )


def _doctrine(name, tag, *fits):
    doctrine = SimpleNamespace(
        pk=name,
        name=name,
        tag=tag,
        pinned=False,
        sort_order=0,
        fits=_all(*fits),
        shared_skills=_all(),
        motd_groups=_all(),
        fleet_structures=_all(),
        motd_template="",
        visible_to_all_members=True,
    )
    doctrine.tag_label = Doctrine.tag_label.fget(doctrine)
    return doctrine


SHIPS = {
    37481: ("Ferox", "Battlecruisers"),
    11995: ("Onyx", "Cruisers"),
    11985: ("Basilisk", "Cruisers"),
    19720: ("Revelation", "Capital Ships"),
}


@pytest.fixture
def context():
    """Run the index view on two doctrines and return the context it renders with."""
    doctrines = [
        _doctrine(
            "Ferox Fleet",
            "Mainline",
            _fit(37481, role="DPS"),
            _fit(11995, role="Tackle"),
            _fit(11985, role="logi "),
            _fit(37481, role="Training plan", is_skill_plan=True),
        ),
        _doctrine(
            "Dreads", "Capitals", _fit(19720, role="DPS"), _fit(11985, role="Logi")
        ),
    ]
    searched = []

    def search(type_ids):
        searched.extend(type_ids)
        return {i: SHIPS[i] for i in searched}

    request = MagicMock()
    request.GET = QueryDict("")
    request.user.is_superuser = False
    request.user.groups.values_list.return_value = []
    with (
        patch.object(views, "_active_character_id", return_value=None),
        patch.object(views, "visible_doctrines", return_value=doctrines),
        patch.object(views, "EveType"),
        patch.object(views, "readiness_for_fit", return_value={"tiers": []}),
        patch.object(views, "ship_search_info", side_effect=search),
        patch.object(views, "_owned_characters", return_value=[]),
        patch.object(views, "_motds_by_doctrine", return_value={}),
        patch.object(views, "render") as render,
    ):
        inspect.unwrap(views.index)(request)
    ctx = render.call_args.args[2]
    ctx["searched_ids"] = sorted(searched)
    return ctx


def _fits(ctx, doctrine_name):
    doctrine = next(
        d for d in ctx["doctrine_data"] if d["doctrine"].name == doctrine_name
    )
    return [(f["ship_name"], f["ship_class"], f["role_key"]) for f in doctrine["fits"]]


def test_each_fit_carries_its_hull_class_and_role(context):
    assert _fits(context, "Ferox Fleet")[:3] == [
        ("Ferox", "Battlecruisers", "dps"),
        ("Onyx", "Cruisers", "tackle"),
        ("Basilisk", "Cruisers", "logi"),
    ]


def test_skill_plans_get_no_search_data(context):
    assert _fits(context, "Ferox Fleet")[3] == ("", "", "")
    # The skill plan's hull isn't looked up just for it.
    assert context["searched_ids"] == [11985, 11985, 11995, 19720, 37481]


def test_ship_class_facet_lists_each_class_once(context):
    assert context["ship_classes"] == ["Battlecruisers", "Capital Ships", "Cruisers"]


def test_role_facet_merges_spellings_and_skips_skill_plans(context):
    assert context["roles"] == {"dps": "DPS", "logi": "logi", "tackle": "Tackle"}
