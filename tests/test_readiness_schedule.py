"""Tests for the readiness recompute schedule: interval setting, debounce, triggers."""

import sys
from types import ModuleType
from unittest.mock import MagicMock, patch

from django.apps import apps
from django.core.cache import cache
from django.db import connection
from django.db.models.signals import post_migrate, post_save
from django.test import override_settings

from aa_fleet_hub.app_settings import readiness_cache_ttl, readiness_interval_minutes
from aa_fleet_hub.apps import _register_beat_tasks_after_migrate
from aa_fleet_hub.tasks import RECOMPUTE_SCHEDULED_KEY, invalidate_readiness_cache

# ---------------------------------------------------------------------------
# FLEET_HUB_READINESS_INTERVAL
# ---------------------------------------------------------------------------


def test_interval_defaults_to_hourly():
    assert readiness_interval_minutes() == 60
    assert readiness_cache_ttl() == 7200


@override_settings(FLEET_HUB_READINESS_INTERVAL=15)
def test_interval_setting_overrides_default_and_ttl_is_double():
    assert readiness_interval_minutes() == 15
    assert readiness_cache_ttl() == 15 * 60 * 2


def test_invalid_interval_falls_back_to_default():
    for bad in (0, -5, "soon", None):
        with override_settings(FLEET_HUB_READINESS_INTERVAL=bad):
            assert readiness_interval_minutes() == 60


# ---------------------------------------------------------------------------
# Debounced recompute after doctrine/fit saves
# ---------------------------------------------------------------------------


def test_burst_of_saves_queues_one_recompute():
    cache.delete(RECOMPUTE_SCHEDULED_KEY)
    with patch("aa_fleet_hub.tasks.update_readiness_cache") as task:
        for _ in range(10):
            invalidate_readiness_cache()
    task.apply_async.assert_called_once_with(countdown=30)
    task.delay.assert_not_called()


def test_save_paths_use_debounced_recompute():
    # views.py needs the full AA stack to import (and _save_doctrine/_save_fit
    # a DB to run), so check the source instead: neither may queue an
    # undebounced recompute.
    import ast
    from pathlib import Path

    import aa_fleet_hub

    path = Path(aa_fleet_hub.__file__).parent / "views.py"
    source = path.read_text()
    funcs = {
        node.name: ast.get_source_segment(source, node)
        for node in ast.parse(source).body
        if isinstance(node, ast.FunctionDef)
    }
    for name in ("_save_doctrine", "_save_fit"):
        assert "invalidate_readiness_cache()" in funcs[name]
        assert "update_readiness_cache.delay" not in funcs[name]


# ---------------------------------------------------------------------------
# Beat schedule registration
# ---------------------------------------------------------------------------


def _fake_beat_models():
    mod = ModuleType("django_celery_beat.models")
    mod.CrontabSchedule = MagicMock()
    mod.CrontabSchedule.objects.get_or_create.return_value = (MagicMock(), True)
    mod.IntervalSchedule = MagicMock(MINUTES="minutes")
    mod.IntervalSchedule.objects.get_or_create.return_value = ("interval-row", True)
    mod.PeriodicTask = MagicMock()
    return mod


def _readiness_task_call(beat):
    for call in beat.PeriodicTask.objects.update_or_create.call_args_list:
        if call.kwargs["name"] == "fleet_hub_update_readiness_cache":
            return call
    raise AssertionError("readiness PeriodicTask was not registered")


def _beat_tables(present):
    """Pretend the celery-beat tables do or don't exist yet (no DB in these tests)."""
    tables = ["django_celery_beat_periodictask"] if present else []
    return patch.object(connection.introspection, "table_names", return_value=tables)


@override_settings(FLEET_HUB_READINESS_INTERVAL=20)
def test_ready_builds_schedule_from_setting():
    beat = _fake_beat_models()
    with (
        patch.dict(sys.modules, {"django_celery_beat.models": beat}),
        _beat_tables(True),
    ):
        apps.get_app_config("aa_fleet_hub").ready()

    beat.IntervalSchedule.objects.get_or_create.assert_called_once_with(
        every=20, period="minutes"
    )
    defaults = _readiness_task_call(beat).kwargs["defaults"]
    assert defaults["interval"] == "interval-row"
    # Clearing the old hourly crontab is what lets the row save at all.
    assert defaults["crontab"] is None


def test_fresh_install_registers_after_migrate_not_during_boot():
    # First `migrate` on a fresh install: no beat tables yet, so ready() must
    # not touch them (that logged a traceback) and must register once
    # migrations have run.
    config = apps.get_app_config("aa_fleet_hub")
    beat = _fake_beat_models()
    # Django's own post_migrate receivers need a DB, so run ours directly
    # rather than sending the signal. (disconnect returns True only if it
    # was still connected.)
    with patch.dict(sys.modules, {"django_celery_beat.models": beat}):
        try:
            with _beat_tables(False):
                config.ready()
                # A migrate (or flush) that leaves no beat tables: nothing yet.
                _register_beat_tasks_after_migrate(sender=config)
            beat.PeriodicTask.objects.update_or_create.assert_not_called()
            with _beat_tables(True):
                _register_beat_tasks_after_migrate(sender=config)
            _readiness_task_call(beat)
            # Registered once, then stops listening.
            assert not post_migrate.disconnect(
                _register_beat_tasks_after_migrate, sender=config
            )
        finally:
            post_migrate.disconnect(_register_beat_tasks_after_migrate, sender=config)


def test_ready_does_not_hook_skill_syncs():
    before = len(post_save.receivers)
    with (
        patch.dict(sys.modules, {"django_celery_beat.models": _fake_beat_models()}),
        _beat_tables(True),
    ):
        apps.get_app_config("aa_fleet_hub").ready()
    assert len(post_save.receivers) == before
    assert not any(
        "aa_fleet_hub" in getattr(ref(), "__module__", "")
        for _, ref in post_save.receivers
        if ref() is not None
    )
