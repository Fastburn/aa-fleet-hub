"""Manage actions that copy, move, archive or delete fits and doctrines.

Every copy carries the whole fit (notes too), a failed copy leaves nothing
half made, and each action queues Fleet Command's debounced recompute once
the change is saved.
"""

from unittest.mock import patch

import pytest
from django.contrib.auth.models import Permission, User
from django.db import IntegrityError
from django.test import RequestFactory

from aa_fleet_hub import views
from aa_fleet_hub.models import Doctrine, Fit, FitTier, FitTierSkill

pytestmark = pytest.mark.django_db(transaction=True)


@pytest.fixture(autouse=True)
def _urls(settings):
    settings.ROOT_URLCONF = "tests.urls"


@pytest.fixture
def manager():
    user = User.objects.create_user("manager")
    for codename in ("basic_access", "manage_fits", "manage_doctrines"):
        user.user_permissions.add(
            Permission.objects.get(
                codename=codename, content_type__app_label="aa_fleet_hub"
            )
        )
    return User.objects.get(pk=user.pk)


@pytest.fixture
def doctrine():
    d = Doctrine.objects.create(name="Ferox Fleet", active=True, public=True)
    fit = Fit.objects.create(
        doctrine=d,
        ship_type_id=37480,
        name="Ferox",
        role="DPS",
        notes="Bring 2 spare charges",
        eft="[Ferox, Ferox]",
    )
    tier = FitTier.objects.create(fit=fit, label="Minimum")
    FitTierSkill.objects.create(tier=tier, skill_type_id=3336, required_level=4)
    FitTierSkill.objects.create(tier=tier, skill_type_id=3337, required_level=3)
    return d


@pytest.fixture
def other():
    return Doctrine.objects.create(name="Caracal Fleet", active=True, public=True)


@pytest.fixture
def recompute():
    with patch("aa_fleet_hub.tasks.invalidate_readiness_cache") as queued:
        yield queued


def _post(view, user, data=None, **kwargs):
    request = RequestFactory().post("/", data or {})
    request.user = user
    with patch.object(views, "messages"):
        return view(request, **kwargs)


def _copied(fit):
    return (
        fit.name,
        fit.role,
        fit.notes,
        fit.eft,
        [
            (
                t.label,
                sorted((s.skill_type_id, s.required_level) for s in t.skills.all()),
            )
            for t in fit.tiers.all()
        ],
    )


# --- every copy carries the whole fit ---------------------------------------


def test_duplicate_doctrine_keeps_fit_notes(manager, doctrine, recompute):
    _post(views.doctrine_duplicate, manager, doctrine_id=doctrine.pk)
    copy = Doctrine.objects.get(name="Ferox Fleet (Copy)")
    assert _copied(copy.fits.get()) == _copied(doctrine.fits.get())


def test_duplicate_fit_keeps_notes(manager, doctrine, recompute):
    src = doctrine.fits.get()
    _post(views.fit_duplicate, manager, fit_id=src.pk)
    copy = doctrine.fits.get(name="Ferox (Copy)")
    assert _copied(copy)[1:] == _copied(src)[1:]


def test_copy_fit_to_another_doctrine_keeps_notes(manager, doctrine, other, recompute):
    src = doctrine.fits.get()
    _post(
        views.fit_move_copy,
        manager,
        {"action": "copy", "target_doctrine_id": other.pk},
        fit_id=src.pk,
    )
    assert _copied(other.fits.get()) == _copied(src)


# --- all or nothing ----------------------------------------------------------


def test_failed_doctrine_duplicate_leaves_nothing_behind(manager, doctrine, recompute):
    before = (Doctrine.objects.count(), Fit.objects.count(), FitTier.objects.count())
    with (
        patch.object(
            FitTierSkill.objects, "bulk_create", side_effect=IntegrityError("boom")
        ),
        pytest.raises(IntegrityError),
    ):
        _post(views.doctrine_duplicate, manager, doctrine_id=doctrine.pk)
    after = (Doctrine.objects.count(), Fit.objects.count(), FitTier.objects.count())
    assert after == before
    recompute.assert_not_called()


def test_failed_fit_copy_leaves_nothing_behind(manager, doctrine, other, recompute):
    with (
        patch.object(
            FitTierSkill.objects, "bulk_create", side_effect=IntegrityError("boom")
        ),
        pytest.raises(IntegrityError),
    ):
        _post(
            views.fit_move_copy,
            manager,
            {"action": "copy", "target_doctrine_id": other.pk},
            fit_id=doctrine.fits.get().pk,
        )
    assert not other.fits.exists()
    assert FitTier.objects.count() == 1


# --- Fleet Command recompute after each action ------------------------------


def test_move_fit_queues_recompute(manager, doctrine, other, recompute):
    fit = doctrine.fits.get()
    _post(
        views.fit_move_copy,
        manager,
        {"action": "move", "target_doctrine_id": other.pk},
        fit_id=fit.pk,
    )
    fit.refresh_from_db()
    assert fit.doctrine == other
    recompute.assert_called_once_with()


@pytest.mark.parametrize(
    "view",
    ["doctrine_duplicate", "doctrine_archive", "doctrine_restore", "doctrine_delete"],
)
def test_doctrine_actions_queue_recompute(manager, doctrine, recompute, view):
    _post(getattr(views, view), manager, doctrine_id=doctrine.pk)
    recompute.assert_called_once_with()


@pytest.mark.parametrize("view", ["fit_duplicate", "fit_delete"])
def test_fit_actions_queue_recompute(manager, doctrine, recompute, view):
    _post(getattr(views, view), manager, fit_id=doctrine.fits.get().pk)
    recompute.assert_called_once_with()


def test_copy_fit_queues_recompute(manager, doctrine, other, recompute):
    _post(
        views.fit_move_copy,
        manager,
        {"action": "copy", "target_doctrine_id": other.pk},
        fit_id=doctrine.fits.get().pk,
    )
    recompute.assert_called_once_with()
