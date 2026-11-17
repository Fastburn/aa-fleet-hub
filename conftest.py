"""
Pytest configuration for aa-fleet-hub.

Alliance Auth's dependency chain (django-esi → eveuniverse → memberaudit) pulls in
models that require the full AA INSTALLED_APPS stack. Since we're testing pure
service functions (no DB), we stub those packages in sys.modules before Django
sets up so the app can import cleanly without needing a live AA install.
"""

import sys
from types import ModuleType
from unittest.mock import MagicMock


def _stub(name):
    mod = ModuleType(name)
    sys.modules[name] = mod
    return mod


def _make_stubs():
    for pkg in (
        "allianceauth",
        "allianceauth.authentication",
        "allianceauth.authentication.models",
        "allianceauth.hooks",
        "allianceauth.menu",
        "allianceauth.menu.hooks",
        "allianceauth.services",
        "allianceauth.services.hooks",
        "esi",
        "esi.models",
        "esi.views",
        "eveuniverse",
        "eveuniverse.models",
        "memberaudit",
        "memberaudit.models",
    ):
        if pkg not in sys.modules:
            sys.modules[pkg] = MagicMock()

    # Provide real-enough hook stubs so auth_hooks.py imports don't crash
    hooks_mod = sys.modules["allianceauth.hooks"]
    hooks_mod.register = lambda name: (lambda fn: fn)

    class _MenuItemHook:
        def __init__(self, *a, **kw):
            pass

        def render(self, request):
            return ""

    class _DashboardItemHook:
        def __init__(self, *a, **kw):
            pass

    class _UrlHook:
        def __init__(self, *a, **kw):
            pass

    sys.modules["allianceauth.menu.hooks"].MenuItemHook = _MenuItemHook
    sys.modules["allianceauth.hooks"].DashboardItemHook = _DashboardItemHook
    sys.modules["allianceauth.services.hooks"].UrlHook = _UrlHook

    # EveType stub so _cached_eve_type_name doesn't crash on DoesNotExist
    EveType = MagicMock()
    EveType.DoesNotExist = type("DoesNotExist", (Exception,), {})
    EveType.objects = MagicMock()
    sys.modules["eveuniverse.models"].EveType = EveType


_make_stubs()


def pytest_configure(config):
    from django.conf import settings

    if not settings.configured:
        settings.configure(
            SECRET_KEY="test-secret-key",
            INSTALLED_APPS=[
                "django.contrib.contenttypes",
                "django.contrib.auth",
                "aa_fleet_hub",
            ],
            DATABASES={
                "default": {
                    "ENGINE": "django.db.backends.sqlite3",
                    "NAME": ":memory:",
                }
            },
            DEFAULT_AUTO_FIELD="django.db.models.AutoField",
            # Build the test tables from the models: one data migration depends
            # on eveuniverse, which is stubbed above.
            MIGRATION_MODULES={"aa_fleet_hub": None},
            CACHES={
                "default": {
                    "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
                }
            },
        )
