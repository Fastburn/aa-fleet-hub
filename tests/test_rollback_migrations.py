"""Migrations that keep a rollback to 0.1.1 working: model db_default (as set by 0027), 0021."""

import importlib
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from django.contrib.auth.models import User
from django.db import connection

from aa_fleet_hub.models import Doctrine, FleetHubPreference

repair = importlib.import_module(
    "aa_fleet_hub.migrations.0021_repair_eft_headers_missing_ship"
).repair_eft_headers


# --- 0027: inserts that leave out the newer columns -------------------------


def _insert_without(obj, *left_out):
    """INSERT obj's row the way 0.1.1 would: without the columns it doesn't know."""
    fields = [
        f
        for f in obj._meta.concrete_fields
        if not f.primary_key and f.attname not in left_out
    ]
    columns = ", ".join(connection.ops.quote_name(f.column) for f in fields)
    values = [f.get_db_prep_save(f.pre_save(obj, True), connection) for f in fields]
    with connection.cursor() as cursor:
        cursor.execute(
            f"INSERT INTO {connection.ops.quote_name(obj._meta.db_table)} "
            f"({columns}) VALUES ({', '.join(['%s'] * len(fields))})",
            values,
        )


@pytest.mark.django_db
def test_doctrine_insert_without_pinned_gets_the_default():
    _insert_without(Doctrine(name="Rolled Back"), "pinned")
    assert Doctrine.objects.get(name="Rolled Back").pinned is False


@pytest.mark.django_db
def test_preference_insert_without_readiness_filters_gets_the_defaults():
    user = User.objects.create_user("pilot")
    _insert_without(
        FleetHubPreference(user=user), "readiness_hide_alts", "readiness_hide_cant_fly"
    )
    pref = FleetHubPreference.objects.get(user=user)
    assert pref.readiness_hide_alts is True
    assert pref.readiness_hide_cant_fly is True


# --- 0021: EFT header repair keeps the line endings -------------------------


def _run_repair(eft, ship_name="Ferox"):
    fit = SimpleNamespace(eft=eft, ship_type_id=37480, save=MagicMock())
    fit_model, eve_type = MagicMock(), MagicMock()
    fit_model.objects.exclude.return_value.filter.return_value = [fit]
    eve_type.objects.filter.return_value.first.return_value = SimpleNamespace(
        name=ship_name
    )
    apps = MagicMock()
    apps.get_model.side_effect = lambda app, model: (
        fit_model if model == "Fit" else eve_type
    )
    repair(apps, None)
    return fit


@pytest.mark.parametrize("nl", ["\n", "\r\n"])
def test_repair_adds_the_ship_and_keeps_the_line_endings(nl):
    fit = _run_repair(f"[Brawler]{nl}Gyrostabilizer II{nl}{nl}Heavy Neutron Blaster II")
    assert fit.eft == (
        f"[Ferox, Brawler]{nl}Gyrostabilizer II{nl}{nl}Heavy Neutron Blaster II"
    )
    fit.save.assert_called_once_with(update_fields=["eft"])


def test_repair_leaves_a_complete_header_alone():
    fit = _run_repair("[Ferox, Brawler]\r\nGyrostabilizer II")
    assert fit.eft == "[Ferox, Brawler]\r\nGyrostabilizer II"
    fit.save.assert_not_called()
