"""AppConfig for Fleet Hub — registers hooks, periodic tasks, and ESI scopes."""

import logging

from django.apps import AppConfig

logger = logging.getLogger(__name__)


class AaFleetHubConfig(AppConfig):
    name = "aa_fleet_hub"
    label = "aa_fleet_hub"
    verbose_name = "Fleet Hub"

    # ESI scopes required for the fleet structure push feature.
    # Not part of LOGIN_TOKEN_SCOPES — requested per-FC via the "Authorize
    # Fleet Access" link (fleet_authorize, gated to manage_fits), the same
    # on-demand pattern the Structures app uses for "Add Structure Owner".
    # Installers must ensure the EVE SSO app registered at
    # developers.eveonline.com has both scopes enabled, or that consent
    # screen will fail with invalid_scope.
    REQUIRED_FLEET_SCOPES = [
        "esi-fleets.read_fleet.v1",
        "esi-fleets.write_fleet.v1",
    ]

    def ready(self):
        # Load EVE type data (skills cat 16, ships cat 6) for autocomplete.
        # Only fires the Celery task when types are not already present, to avoid
        # hammering ESI on every worker restart.
        try:
            from eveuniverse.models import EveType
            from eveuniverse.tasks import load_eve_types

            # Trigger a load if we have suspiciously few ships or skills — catches
            # partial/corrupt initial loads, not just a completely empty table.
            ship_count = EveType.objects.filter(
                eve_group__eve_category_id=6, published=True
            ).count()
            skill_count = EveType.objects.filter(
                eve_group__eve_category_id=16, published=True
            ).count()
            if ship_count < 400 or skill_count < 400:
                load_eve_types.delay(category_ids=[16, 6])
        except Exception:
            pass

        # On a fresh install the first `migrate` boots the app before the
        # celery-beat tables exist: register right after migrations instead of
        # logging a traceback. Every other boot registers here.
        try:
            from django.db import connection
            from django.db.models.signals import post_migrate

            if (
                "django_celery_beat_periodictask"
                in connection.introspection.table_names()
            ):
                register_beat_tasks()
            else:
                post_migrate.connect(_register_beat_tasks_after_migrate, sender=self)
        except Exception:
            logger.exception("Fleet Hub: failed to register Celery beat tasks")


def _register_beat_tasks_after_migrate(sender, **kwargs):
    from django.db import connection
    from django.db.models.signals import post_migrate

    # post_migrate also fires on flush and on migrate runs that don't create
    # the beat tables, so only act once they exist, then stop listening.
    if "django_celery_beat_periodictask" not in connection.introspection.table_names():
        return
    post_migrate.disconnect(_register_beat_tasks_after_migrate, sender=sender)
    try:
        register_beat_tasks()
    except Exception:
        logger.exception("Fleet Hub: failed to register Celery beat tasks")


def register_beat_tasks():
    """Create or update Fleet Hub's two django-celery-beat entries.

    The readiness cache update runs every FLEET_HUB_READINESS_INTERVAL minutes.
    Uses update_or_create so it is idempotent on every boot; the setting is the
    source of truth, so an edit made in Django admin is reset on the next restart.
    """
    from django_celery_beat.models import (
        CrontabSchedule,
        IntervalSchedule,
        PeriodicTask,
    )

    from .app_settings import readiness_interval_minutes

    schedule, _ = IntervalSchedule.objects.get_or_create(
        every=readiness_interval_minutes(),
        period=IntervalSchedule.MINUTES,
    )
    PeriodicTask.objects.update_or_create(
        name="fleet_hub_update_readiness_cache",
        defaults={
            "task": "aa_fleet_hub.tasks.update_readiness_cache",
            "interval": schedule,
            # Older installs registered this task on an hourly crontab;
            # PeriodicTask.save() rejects a row with two schedule types.
            "crontab": None,
            "enabled": True,
        },
    )

    # Weekly refresh of the ship/skill EveType catalog so new hulls and
    # skills CCP releases show up in autocompletes without manual action.
    # Weekly (not monthly) so partial/corrupt loads self-heal quickly.
    weekly_schedule, _ = CrontabSchedule.objects.get_or_create(
        minute="0",
        hour="3",
        day_of_month="*",
        month_of_year="*",
        day_of_week="0",
    )
    PeriodicTask.objects.update_or_create(
        name="fleet_hub_refresh_eve_type_catalog",
        defaults={
            "task": "aa_fleet_hub.tasks.refresh_eve_type_catalog",
            "crontab": weekly_schedule,
            "enabled": True,
        },
    )
