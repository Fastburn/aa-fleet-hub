"""Install-level settings for Fleet Hub, read from the AA project's local.py."""

import logging

from django.conf import settings

logger = logging.getLogger(__name__)

READINESS_INTERVAL_DEFAULT = 60


def readiness_interval_minutes():
    """
    Minutes between scheduled Fleet Command readiness recomputes.

    Set FLEET_HUB_READINESS_INTERVAL in local.py; anything that isn't a
    positive whole number falls back to the default with a warning rather
    than breaking app startup.
    """
    value = getattr(
        settings, "FLEET_HUB_READINESS_INTERVAL", READINESS_INTERVAL_DEFAULT
    )
    try:
        minutes = int(value)
    except (TypeError, ValueError):
        minutes = 0
    if minutes < 1:
        logger.warning(
            "FLEET_HUB_READINESS_INTERVAL=%r is not a positive number of minutes; using %d",
            value,
            READINESS_INTERVAL_DEFAULT,
        )
        return READINESS_INTERVAL_DEFAULT
    return minutes


def readiness_cache_ttl():
    """Cache lifetime in seconds: twice the interval, so one delayed run doesn't empty it."""
    return readiness_interval_minutes() * 60 * 2
