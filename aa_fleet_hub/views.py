import json
import logging
import re
from functools import wraps

import requests as esi_requests

from django.contrib import messages
from django.contrib.auth.decorators import login_required, permission_required
from django.contrib.auth.models import Group
from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.http import Http404, HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_POST

from allianceauth.authentication.models import CharacterOwnership
from esi.models import Token
from esi.views import receive_callback as _esi_receive_callback
from eveuniverse.models import EveType

from .models import (
    DOCTRINE_ORDER,
    TAG_COLOR_RE,
    Doctrine,
    DoctrineSharedSkill,
    Fit,
    FitChangeLog,
    FitTier,
    FitTierSkill,
    FleetStructureTemplate,
    FleetHubPreference,
    Motd,
)
from .services import (
    ROMAN,
    format_skill_export,
    get_character_skill_data,
    get_character_skills,
    get_owned_ships,
    eft_header_error,
    ensure_eft_ship_name,
    missing_skills_for_tier,
    parse_eft_for_esi,
    pip_states,
    readiness_for_fit,
    role_key,
    role_labels,
    ship_search_info,
    visible_doctrines,
)

logger = logging.getLogger(__name__)


def _active_character_id(request):
    """Return the character ID currently selected for skill checking (session or main character)."""
    try:
        main = request.user.profile.main_character
    except Exception:
        main = None
    default = main.character_id if main else None
    return request.session.get("fleet_character_id", default)


def _safe_int(value, default=0):
    """Coerce a JSON-decoded value to int, falling back to `default` on bad input."""
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _posted_doctrine(request):
    """The doctrine a manager picked on the MOTD or structure form: None for
    standalone, 404 for an id that is unknown or not a number."""
    raw = request.POST.get("doctrine_id")
    if not raw:
        return None
    return get_object_or_404(Doctrine, pk=_safe_int(raw, None))


def _owned_characters(user):
    """Return ordered queryset of all CharacterOwnership records for a user."""
    return (
        CharacterOwnership.objects.filter(user=user)
        .select_related("character")
        .order_by("character__character_name")
    )


def _can_fly(readiness):
    """True when the first (minimum) tier has skills and none are missing."""
    tiers = readiness["tiers"]
    return bool(tiers) and tiers[0]["has_skills"] and tiers[0]["missing_count"] == 0


def _character_name(owned_characters, character_id):
    """Name of the viewed character, for the page lead line."""
    for ownership in owned_characters:
        if ownership.character.character_id == character_id:
            return ownership.character.character_name
    return None


PINNED_LABEL = "Pinned"


def _group_by_tag(items, doctrine_getter=lambda item: item["doctrine"]):
    """
    Split already-ordered items into page sections, returned as context.

    doctrine_groups is a list of (label, items, pinned). Pinned doctrines (#6)
    come first in their own section. Each tag section sits at the lowest Sort
    Order among its unpinned doctrines, ties alphabetical (case-insensitive),
    and "Untagged" always last. Items keep their order inside a section.

    doctrine_tags lists every tag for the filter buttons in the same order,
    including tags only pinned doctrines carry, so filtering by a pinned
    doctrine's own tag still finds it.
    """
    pinned = []
    groups = {}
    unpinned_rank = {}
    pinned_rank = {}
    for item in items:
        doctrine = doctrine_getter(item)
        tag = doctrine.tag_label
        rank = pinned_rank if doctrine.pinned else unpinned_rank
        rank[tag] = min(rank.get(tag, doctrine.sort_order), doctrine.sort_order)
        if doctrine.pinned:
            pinned.append(item)
        else:
            groups.setdefault(tag, []).append(item)

    def order(tag):
        rank = unpinned_rank.get(tag, pinned_rank.get(tag))
        # The exact tag last, so "Mainline" and "mainline" never swap places.
        return (tag == "Untagged", rank, tag.lower(), tag)

    tags = sorted(unpinned_rank.keys() | pinned_rank.keys(), key=order)
    sections = [(PINNED_LABEL, pinned, True)] if pinned else []
    sections += [(tag, groups[tag], False) for tag in tags if tag in groups]
    return {"doctrine_groups": sections, "doctrine_tags": tags}


def _can_manage_fits(user):
    """manage_doctrines (Officer) is the higher tier and includes everything manage_fits (FC) can do."""
    return user.has_perm("aa_fleet_hub.manage_fits") or user.has_perm(
        "aa_fleet_hub.manage_doctrines"
    )


def _manageable_doctrines(user, include_archived=False):
    """Doctrines a user may manage fits on. Officers see all; fit managers see only their visible set."""
    from django.db.models import Q

    if user.has_perm("aa_fleet_hub.manage_doctrines"):
        qs = (
            Doctrine.objects.all()
            if include_archived
            else Doctrine.objects.filter(archived=False)
        )
    else:
        user_groups = user.groups.all()
        qs = (
            Doctrine.objects.filter(archived=False)
            .filter(Q(public=True) | Q(groups__in=user_groups) | Q(groups__isnull=True))
            .distinct()
        )
    return qs


def _can_manage_doctrines(user):
    """Doctrine managers (Officers). Only they attach MOTDs and fleet structures
    to doctrines, make MOTDs public, and edit other people's (#3)."""
    return user.has_perm("aa_fleet_hub.manage_doctrines")


def _owned_or_managed(model, user):
    """MOTDs or fleet structures a user may edit or delete (#3): their own, or
    any of them for a doctrine manager."""
    if _can_manage_doctrines(user):
        return model.objects.all()
    return model.objects.filter(created_by=user)


def _attachable_doctrines():
    """Doctrines a manager can attach an MOTD or fleet structure to."""
    return (
        Doctrine.objects.filter(active=True, archived=False)
        .exclude(name="")
        .order_by(*DOCTRINE_ORDER)
    )


def _visible_motds(user):
    """MOTDs a user can see, copy and push to a fleet (#3).

    Doctrine managers see all. Anyone else sees, on doctrines they can see,
    the public MOTDs and the ones their "MOTD visible to" groups allow.
    Fleet Setup users also see every standalone MOTD and their own.
    """
    from django.db.models import Q

    if _can_manage_doctrines(user):
        return Motd.objects.all()
    on_visible_doctrine = Q(
        doctrine__in=visible_doctrines(user).values_list("pk", flat=True)
    )
    allowed = on_visible_doctrine & (
        Q(public=True) | Q(doctrine__motd_groups__in=user.groups.all())
    )
    if _can_see_fleet_ops(user):
        allowed |= Q(doctrine__isnull=True) | Q(created_by=user)
    return Motd.objects.filter(allowed).distinct()


def _motds_by_doctrine(user, doctrines):
    """The MOTDs a user can see on each of these doctrines, default first."""
    by_doctrine = {}
    for motd in _visible_motds(user).filter(doctrine__in=doctrines):
        by_doctrine.setdefault(motd.doctrine_id, []).append(motd)
    return by_doctrine


def _motd_choices(doctrine, motds):
    """Apply Structure's MOTD picker (#3), as (group label, MOTDs) pairs.

    The structure's own doctrine first, then standalone MOTDs, then other
    doctrines' MOTDs under their doctrine's name.
    """
    own = [m for m in motds if doctrine and m.doctrine_id == doctrine.pk]
    groups = [(doctrine.name, own)] if own else []
    standalone = [m for m in motds if m.doctrine_id is None]
    if standalone:
        groups.append(("Standalone", standalone))
    others = {}
    for m in motds:
        if m.doctrine_id and not (doctrine and m.doctrine_id == doctrine.pk):
            others.setdefault(m.doctrine.name, []).append(m)
    groups += sorted(others.items(), key=lambda item: item[0].lower())
    return groups


def manage_access_required(view_func):
    """Allow manage_fits OR manage_doctrines — Officers can do everything fit-managers can."""

    @wraps(view_func)
    def _wrapped(request, *args, **kwargs):
        if not _can_manage_fits(request.user):
            raise PermissionDenied
        return view_func(request, *args, **kwargs)

    return _wrapped


def _can_see_fleet_ops(user):
    """manage_fits/manage_doctrines holders run fleets too, even without fleet_command (readiness)."""
    return _can_manage_fits(user) or user.has_perm("aa_fleet_hub.fleet_command")


def fleet_ops_access_required(view_func):
    """Allow manage_fits, manage_doctrines, or fleet_command — anyone who can run a fleet."""

    @wraps(view_func)
    def _wrapped(request, *args, **kwargs):
        if not _can_see_fleet_ops(request.user):
            raise PermissionDenied
        return view_func(request, *args, **kwargs)

    return _wrapped


# ---------------------------------------------------------------------------
# Member-facing views
# ---------------------------------------------------------------------------


@login_required
@permission_required("aa_fleet_hub.basic_access")
def index(request):
    """Doctrine index — member-facing list of visible doctrines with readiness pips."""
    character_id = _active_character_id(request)
    skills, queued, member_audit_registered = (
        get_character_skill_data(character_id) if character_id else ({}, {}, True)
    )
    owned_ships = get_owned_ships(character_id) if character_id else None
    doctrines = visible_doctrines(request.user)
    # Single-doctrine view (#17): ?doctrine=<id> narrows the page to one doctrine.
    # It filters the same visible queryset, so a doctrine the user can't see and
    # one that doesn't exist both come back empty and get the same "not found".
    # Nine digits keeps the id inside the 32-bit primary key, so an oversized id
    # is "not found" too rather than a database error on older Django.
    single_doctrine = "doctrine" in request.GET
    if single_doctrine:
        doctrine_id = request.GET.get("doctrine", "")
        doctrines = (
            doctrines.filter(pk=int(doctrine_id))
            if re.fullmatch(r"[0-9]{1,9}", doctrine_id)
            else doctrines.none()
        )

    # Bulk-fetch shared-skill and tier-skill names up front so the loops below
    # don't issue one EveType query per skill per fit/doctrine (N+1 on a page
    # every member hits).
    shared_skill_type_ids = {
        ss.skill_type_id
        for doctrine in doctrines
        for ss in doctrine.shared_skills.all()
    }
    tier_skill_type_ids = {
        ts.skill_type_id
        for doctrine in doctrines
        for fit in doctrine.fits.all()
        for tier in fit.tiers.all()
        for ts in tier.skills.all()
    }
    eve_names = EveType.objects.filter(
        id__in=shared_skill_type_ids | tier_skill_type_ids
    ).in_bulk()
    # Hull name and ship class per fit, for the doctrine search (#4).
    ship_info = ship_search_info(
        fit.ship_type_id
        for doctrine in doctrines
        for fit in doctrine.fits.all()
        if not fit.is_skill_plan
    )

    motds_by_doctrine = _motds_by_doctrine(request.user, doctrines)
    doctrine_data = []
    for doctrine in doctrines:
        fits_data = []
        for fit in doctrine.fits.all():
            ship_name, ship_class = (
                ("", "")
                if fit.is_skill_plan
                else ship_info.get(fit.ship_type_id, ("", ""))
            )
            if fit.is_skill_plan or owned_ships is None:
                has_hull = None
                hull_locations = []
            else:
                hull_locs = owned_ships.get(fit.ship_type_id)
                has_hull = hull_locs is not None
                hull_locations = hull_locs or []
            fits_data.append(
                {
                    "fit": fit,
                    "readiness": readiness_for_fit(fit, skills, queued, eve_names),
                    "has_hull": has_hull,
                    "hull_locations": hull_locations,
                    "ship_name": ship_name,
                    "ship_class": ship_class,
                    "role_key": "" if fit.is_skill_plan else role_key(fit.role),
                }
            )
        shared_skill_rows = []
        for ss in doctrine.shared_skills.all():
            trained = skills.get(ss.skill_type_id, 0)
            queued_max = queued.get(ss.skill_type_id, 0)
            is_queued = queued_max > trained
            met = trained >= ss.required_level
            close = not met and (trained >= ss.required_level - 1 or is_queued)
            sg_class = "met" if met else ("close" if close else "unmet")
            eve_type = eve_names.get(ss.skill_type_id)
            skill_name = eve_type.name if eve_type else f"Skill #{ss.skill_type_id}"
            shared_skill_rows.append(
                {
                    "skill_name": skill_name,
                    "required_level": ss.required_level,
                    "pips": pip_states(
                        ss.skill_type_id, ss.required_level, skills, queued
                    ),
                    "sg_class": sg_class,
                    "tooltip": f"{skill_name} — have {ROMAN[min(trained, 5)]}, need {ROMAN[min(ss.required_level, 5)]}",
                }
            )
        doctrine_data.append(
            {
                "doctrine": doctrine,
                "fits": fits_data,
                "fly_count": sum(1 for f in fits_data if _can_fly(f["readiness"])),
                "shared_skill_rows": shared_skill_rows,
                # Default first, so motds.0 is what the header copies.
                "motds": motds_by_doctrine.get(doctrine.pk, []),
                # Here the picker only offers the doctrine's own MOTDs.
                "motd_choices": [
                    (doctrine.name, motds_by_doctrine.get(doctrine.pk, []))
                ],
                "structures": doctrine.fleet_structures.all()
                if doctrine.visible_to_all_members
                else [],
            }
        )

    owned_characters = _owned_characters(request.user)
    context = {
        **_group_by_tag(doctrine_data),
        "doctrine_data": doctrine_data,
        "single_doctrine": single_doctrine,
        # Copied from JSON, not the page text: HTML would turn CRLF into LF.
        "motd_texts": {
            m.pk: m.text for motds in motds_by_doctrine.values() for m in motds
        },
        "can_see_fleet_setup": _can_see_fleet_ops(request.user),
        "ship_classes": sorted(
            {
                f["ship_class"]
                for d in doctrine_data
                for f in d["fits"]
                if f["ship_class"]
            },
            key=str.lower,
        ),
        "roles": role_labels(
            f["fit"].role
            for d in doctrine_data
            for f in d["fits"]
            if not f["fit"].is_skill_plan
        ),
        "owned_characters": owned_characters,
        "active_character_id": character_id,
        "active_character_name": _character_name(owned_characters, character_id),
        "member_audit_registered": member_audit_registered,
    }
    return render(request, "aa_fleet_hub/index.html", context)


@login_required
@permission_required("aa_fleet_hub.basic_access")
def fit_panel(request, fit_id):
    """Partial HTML for the fit detail offcanvas panel."""
    # Restrict to fits in doctrines the user can actually see.
    visible = visible_doctrines(request.user).values_list("pk", flat=True)
    fit = get_object_or_404(
        Fit.objects.prefetch_related("tiers__skills").select_related("doctrine"),
        pk=fit_id,
        doctrine__in=visible,
    )
    character_id = _active_character_id(request)
    skills, queued, member_audit_registered = (
        get_character_skill_data(character_id) if character_id else ({}, {}, True)
    )
    if fit.is_skill_plan or not character_id:
        has_hull = None
        hull_locations = []
    else:
        owned_ships = get_owned_ships(character_id)
        if owned_ships is None:
            has_hull = None
            hull_locations = []
        else:
            hull_locs = owned_ships.get(fit.ship_type_id)
            has_hull = hull_locs is not None
            hull_locations = hull_locs or []
    context = {
        "fit": fit,
        "readiness": readiness_for_fit(fit, skills, queued),
        "has_hull": has_hull,
        "hull_locations": hull_locations,
        "member_audit_registered": member_audit_registered,
        "changelog": fit.changelog.select_related(
            "changed_by__profile__main_character"
        ).all(),
    }
    return render(request, "aa_fleet_hub/partials/fit_panel.html", context)


@login_required
@permission_required("aa_fleet_hub.basic_access")
def fit_detail(request, fit_id):
    visible = visible_doctrines(request.user).values_list("pk", flat=True)
    fit = get_object_or_404(
        Fit.objects.prefetch_related("tiers__skills").select_related("doctrine"),
        pk=fit_id,
        doctrine__in=visible,
    )
    character_id = _active_character_id(request)
    skills, queued, _ = (
        get_character_skill_data(character_id) if character_id else ({}, {}, True)
    )

    context = {
        "fit": fit,
        "readiness": readiness_for_fit(fit, skills, queued),
    }
    return render(request, "aa_fleet_hub/fit_detail.html", context)


@login_required
@permission_required("aa_fleet_hub.basic_access")
def my_skills(request):
    """My Skills page — per-doctrine skill grids for the viewing character."""
    character_id = _active_character_id(request)
    skills, queued, member_audit_registered = (
        get_character_skill_data(character_id) if character_id else ({}, {}, True)
    )
    doctrines = visible_doctrines(request.user)

    # Bulk-fetch tier-skill names up front to avoid an EveType query per fit.
    tier_skill_type_ids = {
        ts.skill_type_id
        for doctrine in doctrines
        for fit in doctrine.fits.all()
        for tier in fit.tiers.all()
        for ts in tier.skills.all()
    }
    eve_names = EveType.objects.filter(id__in=tier_skill_type_ids).in_bulk()

    doctrine_data = []
    for doctrine in doctrines:
        fits_data = []
        for fit in doctrine.fits.all():
            fits_data.append(
                {
                    "fit": fit,
                    "readiness": readiness_for_fit(fit, skills, queued, eve_names),
                }
            )
        doctrine_data.append(
            {
                "doctrine": doctrine,
                "fits": fits_data,
            }
        )

    # Find doctrine the member is closest to qualifying for (fewest missing to Minimum tier)
    closest = None
    closest_missing = None
    for item in doctrine_data:
        for fit_item in item["fits"]:
            readiness = fit_item["readiness"]
            if not readiness["tiers"]:
                continue
            # Only look at first (Minimum) tier
            min_tier = readiness["tiers"][0]
            if min_tier["missing_count"] == 0:
                continue  # already qualifies
            if closest_missing is None or min_tier["missing_count"] < closest_missing:
                closest_missing = min_tier["missing_count"]
                closest = {
                    "doctrine": item["doctrine"],
                    "fit": fit_item["fit"],
                    "tier": min_tier["tier"],
                    "missing": min_tier["missing_count"],
                }

    owned_characters = _owned_characters(request.user)
    context = {
        **_group_by_tag(doctrine_data),
        "doctrine_data": doctrine_data,
        "owned_characters": owned_characters,
        "active_character_id": character_id,
        "active_character_name": _character_name(owned_characters, character_id),
        "closest": closest,
        "member_audit_registered": member_audit_registered,
    }
    return render(request, "aa_fleet_hub/my_skills.html", context)


@login_required
@permission_required("aa_fleet_hub.basic_access")
def switch_character(request):
    """Store the selected character ID in the session for the Viewing As switcher."""
    if request.method == "POST":
        char_id = request.POST.get("character_id", "")
        try:
            char_id_int = int(char_id)
        except (ValueError, TypeError):
            return redirect("aa_fleet_hub:index")
        owned_ids = list(
            _owned_characters(request.user).values_list(
                "character__character_id", flat=True
            )
        )
        if char_id_int in owned_ids:
            request.session["fleet_character_id"] = char_id_int
    next_url = request.POST.get("next", "")
    if next_url and url_has_allowed_host_and_scheme(
        next_url, allowed_hosts={request.get_host()}
    ):
        return redirect(next_url)
    return redirect("aa_fleet_hub:index")


@login_required
@permission_required("aa_fleet_hub.basic_access")
def export_tier_skills(request, fit_id, tier_id):
    """Return plain-text skill export for a single tier (EVESkillboard / SkillQ format)."""
    # Verify the fit belongs to a doctrine the user can see.
    visible = visible_doctrines(request.user).values_list("pk", flat=True)
    tier = get_object_or_404(
        FitTier, pk=tier_id, fit_id=fit_id, fit__doctrine__in=visible
    )
    character_id = _active_character_id(request)
    skills = get_character_skills(character_id) if character_id else {}
    missing = missing_skills_for_tier(tier, skills)
    text = format_skill_export(missing)
    return HttpResponse(text, content_type="text/plain")


# ---------------------------------------------------------------------------
# Readiness views (FC+)
# ---------------------------------------------------------------------------


@login_required
@permission_required("aa_fleet_hub.basic_access")
@require_POST
def fleet_widget_toggle(request):
    """Toggle the dashboard widget opt-in preference."""
    pref, _ = FleetHubPreference.objects.get_or_create(user=request.user)
    pref.show_dashboard_widget = not pref.show_dashboard_widget
    pref.save(update_fields=["show_dashboard_widget"])
    next_url = request.POST.get("next", "")
    if next_url and url_has_allowed_host_and_scheme(
        next_url, allowed_hosts={request.get_host()}
    ):
        return redirect(next_url)
    return redirect("/dashboard/")


FLEET_SCOPES = ["esi-fleets.read_fleet.v1", "esi-fleets.write_fleet.v1"]
FITTINGS_SCOPES = ["esi-fittings.write_fittings.v1"]


def dashboard_widget(request):
    """Dashboard hook view — fleet readiness summary for member + FC."""
    if not request.user.has_perm("aa_fleet_hub.basic_access"):
        return ""

    from .models import FleetHubSettings

    if not FleetHubSettings.get().dashboard_widget_enabled:
        return ""

    pref = FleetHubPreference.objects.filter(user=request.user).first()
    widget_enabled = pref.show_dashboard_widget if pref else False

    from django.template.loader import render_to_string

    if not widget_enabled:
        return render_to_string(
            "aa_fleet_hub/dashboard_optin.html", {}, request=request
        )

    character_id = _active_character_id(request)
    skills, queued, member_audit_registered = (
        get_character_skill_data(character_id) if character_id else ({}, {}, True)
    )
    doctrines = list(visible_doctrines(request.user))[:5]

    # Bulk-fetch tier-skill names up front to avoid an EveType query per fit.
    tier_skill_type_ids = {
        ts.skill_type_id
        for doctrine in doctrines
        for fit in list(doctrine.fits.all())[:3]
        for tier in fit.tiers.all()
        for ts in tier.skills.all()
    }
    eve_names = EveType.objects.filter(id__in=tier_skill_type_ids).in_bulk()

    personal_readiness = []
    closest = None
    closest_missing = None

    for doctrine in doctrines:
        fits_data = []
        for fit in list(doctrine.fits.all())[
            :3
        ]:  # cap at 3 fits per doctrine in widget
            readiness = readiness_for_fit(fit, skills, queued, eve_names)
            best_idx = readiness["best_tier_index"]
            tier_labels = [t["tier"].label for t in readiness["tiers"]]

            if best_idx is not None:
                # Reuse readiness_for_fit's own highlight (tier-count-aware) so the
                # widget agrees with the Doctrines page instead of recomputing its
                # own threshold — a fixed "best_idx >= 1" check disagreed with
                # readiness_for_fit's rec_index for 2-tier fits.
                bucket_key = {"all": "max", "recommended": "rec", "min": "min"}.get(
                    readiness["highlight"], "none"
                )
                tier_name = tier_labels[best_idx]
            else:
                bucket_key = "none"
                tier_name = None

            # Track closest
            if readiness["tiers"]:
                min_missing = readiness["tiers"][0]["missing_count"]
                if min_missing > 0 and (
                    closest_missing is None or min_missing < closest_missing
                ):
                    closest_missing = min_missing
                    closest = {
                        "fit": fit,
                        "doctrine": doctrine,
                        "tier": readiness["tiers"][0]["tier"],
                        "missing": min_missing,
                    }

            fits_data.append(
                {
                    "name": fit.name,
                    "image": fit.ship_image_url(32),
                    "bucket": bucket_key,
                    "tier": tier_name,
                }
            )

        if fits_data:
            personal_readiness.append({"doctrine": doctrine.name, "fits": fits_data})

    # FC summary from cache — alliance-wide ready/not-ready pilot counts are
    # doctrine-access data, not fit-editing data, so this is gated on
    # fleet_command or manage_doctrines. A plain manage_fits-only fit manager
    # (no doctrine-level access) should not see org-wide pilot counts.
    fc_summary = []
    if request.user.has_perm("aa_fleet_hub.fleet_command") or request.user.has_perm(
        "aa_fleet_hub.manage_doctrines"
    ):
        from .tasks import get_readiness_summary

        readiness_cache = get_readiness_summary()
        if readiness_cache:
            # The summary covers every active doctrine regardless of group
            # gating, so restrict to what this user can actually see before
            # exposing names/counts on the dashboard.
            visible_ids = set(
                visible_doctrines(request.user).values_list("pk", flat=True)
            )
            for doctrine_id, data in list(readiness_cache.items()):
                if doctrine_id not in visible_ids:
                    continue
                if len(fc_summary) >= 4:
                    break
                total = data["total"]
                if not total:
                    continue
                # "ready" = average, across this doctrine's fits, of how many pilots
                # qualify for at least the lowest tier of that fit (i.e. total minus
                # those stuck in "none"). It's a rough per-doctrine health indicator,
                # not a count of pilots ready for every fit simultaneously. Fits with
                # no skill plan (tier_labels empty) have nothing to average and are
                # excluded from both the sum and the divisor.
                tiered_fits = [fit for fit in data["fits"] if fit["tier_labels"]]
                if not tiered_fits:
                    continue
                ready = sum(
                    total - fit["tier_counts"].get("none", 0) for fit in tiered_fits
                ) // len(tiered_fits)
                # Same average, counting each pilot once. Absent on entries
                # cached before pilot counts existed, until the next recompute.
                pilot_total = data.get("pilot_total")
                pilots_ready = None
                if pilot_total is not None:
                    pilots_ready = sum(
                        pilot_total - fit["pilot_tier_counts"].get("none", 0)
                        for fit in tiered_fits
                    ) // len(tiered_fits)
                fc_summary.append(
                    {
                        "doctrine": data["name"],
                        "ready": ready,
                        "not_ready": total - ready,
                        "total": total,
                        "pilots_ready": pilots_ready,
                        "pilot_total": pilot_total,
                    }
                )

    from django.template.loader import render_to_string

    return render_to_string(
        "aa_fleet_hub/dashboard.html",
        {
            "personal_readiness": personal_readiness,
            "closest": closest,
            "fc_summary": fc_summary,
            "active_character_id": character_id,
            "member_audit_registered": member_audit_registered,
        },
        request=request,
    )


@login_required
@permission_required("aa_fleet_hub.basic_access")
def fleet_authorize(request):
    """Redirect the user through EVE SSO to authorize fleet scopes.

    Open to all members (not just manage_fits) since members can also apply
    a "visible to all members" doctrine's fleet structure to their own fleet.
    """
    from esi.decorators import token_required

    redirect_to = (
        "aa_fleet_hub:fleet_ops_index"
        if _can_see_fleet_ops(request.user)
        else "aa_fleet_hub:index"
    )

    @token_required(scopes=FLEET_SCOPES)
    def _inner(request, token):
        return redirect(redirect_to)

    return _inner(request)


@login_required
@permission_required("aa_fleet_hub.basic_access")
def fittings_authorize(request):
    """Redirect the user through EVE SSO to authorize the fittings write scope."""
    from esi.decorators import token_required

    @token_required(scopes=FITTINGS_SCOPES)
    def _inner(request, token):
        if request.user.has_perm("aa_fleet_hub.fleet_command"):
            return redirect("aa_fleet_hub:readiness_index")
        return redirect("aa_fleet_hub:index")

    return _inner(request)


@login_required
@permission_required("aa_fleet_hub.basic_access")
def fit_save_to_esi(request, fit_id):
    """Save a fit to the character's in-game fittings via ESI."""
    if request.method != "POST":
        return JsonResponse(
            {"status": "error", "message": "POST required."}, status=405
        )

    visible = visible_doctrines(request.user).values_list("pk", flat=True)
    fit = get_object_or_404(Fit, pk=fit_id, doctrine__in=visible)
    if not fit.eft:
        return JsonResponse({"status": "error", "message": "This fit has no EFT data."})

    character_id = _active_character_id(request)
    if not character_id:
        return JsonResponse(
            {"status": "error", "message": "No active character selected."}
        )

    token = Token.get_token(character_id, FITTINGS_SCOPES)
    if not token:
        return JsonResponse(
            {
                "status": "no_token",
                "authorize_url": reverse("aa_fleet_hub:fittings_authorize"),
            }
        )

    try:
        access_token = token.valid_access_token()
    except Exception:
        logger.warning(
            "Fleet Hub: fittings token refresh failed for character %s",
            character_id,
            exc_info=True,
        )
        return JsonResponse(
            {"status": "error", "message": "Token refresh failed — re-authorize."}
        )

    ship_type_id, fit_name, items, warnings = parse_eft_for_esi(fit.eft)
    if ship_type_id is None:
        return JsonResponse(
            {
                "status": "error",
                "message": warnings[0] if warnings else "Could not parse fit.",
            }
        )

    payload = {
        "description": "",
        "items": items,
        "name": fit_name or fit.name,
        "ship_type_id": ship_type_id,
    }
    try:
        r = esi_requests.post(
            "https://esi.evetech.net/latest/characters/{}/fittings/".format(
                character_id
            ),
            headers={
                "Authorization": f"Bearer {access_token}",
                "Content-Type": "application/json",
            },
            json=payload,
            timeout=10,
        )
    except Exception:
        logger.warning(
            "Fleet Hub: ESI fittings POST network error for character %s",
            character_id,
            exc_info=True,
        )
        return JsonResponse(
            {"status": "error", "message": "Could not reach ESI — try again shortly."}
        )
    if r.status_code not in (200, 201):
        logger.warning(
            "Fleet Hub: ESI fittings POST failed for character %s — %s %s",
            character_id,
            r.status_code,
            r.text,
        )
        return JsonResponse(
            {
                "status": "error",
                "message": "Failed to save fit to ESI — check your EVE client.",
            }
        )

    msg = "Fit saved to your in-game fittings."
    if warnings:
        msg += f" ({len(warnings)} item(s) skipped — not in local type DB.)"
    return JsonResponse({"status": "ok", "message": msg})


def _queue_readiness_recompute():
    """Debounced Fleet Command recompute once the current change is saved.

    For actions that change which fits count (copy, move, archive, delete);
    form saves already queue it in _save_doctrine/_save_fit."""
    from .tasks import invalidate_readiness_cache

    transaction.on_commit(invalidate_readiness_cache)


@transaction.atomic
def _copy_fit(src, doctrine, name):
    """Copy a fit with its tiers and skills into doctrine, all or nothing.

    The one copy path for duplicate fit, duplicate doctrine and move/copy, so
    they can't drift apart on which fields come along (version and changelog
    start fresh)."""
    new_fit = Fit.objects.create(
        doctrine=doctrine,
        ship_type_id=src.ship_type_id,
        name=name,
        role=src.role,
        description=src.description,
        notes=src.notes,
        eft=src.eft,
        is_skill_plan=src.is_skill_plan,
        img_endpoint=src.img_endpoint,
        pi_plans=src.pi_plans,
        sort_order=src.sort_order,
    )
    for tier in src.tiers.all():
        new_tier = FitTier.objects.create(
            fit=new_fit,
            label=tier.label,
            description=tier.description,
            warning=tier.warning,
            sort_order=tier.sort_order,
        )
        FitTierSkill.objects.bulk_create(
            FitTierSkill(
                tier=new_tier,
                skill_type_id=sk.skill_type_id,
                required_level=sk.required_level,
            )
            for sk in tier.skills.all()
        )
    return new_fit


@login_required
@manage_access_required
def fit_move_copy(request, fit_id):
    """Move or copy a fit to a different doctrine."""
    manageable = _manageable_doctrines(request.user)
    fit = get_object_or_404(
        Fit.objects.prefetch_related("tiers__skills"),
        pk=fit_id,
        doctrine__in=manageable.values_list("pk", flat=True),
    )
    doctrines = manageable.order_by(*DOCTRINE_ORDER)

    if request.method == "POST":
        action = request.POST.get("action")
        target_id = request.POST.get("target_doctrine_id")
        try:
            target = manageable.get(pk=target_id)
        except (Doctrine.DoesNotExist, ValueError, TypeError):
            return render(
                request,
                "aa_fleet_hub/management/fit_move_copy.html",
                {
                    "fit": fit,
                    "doctrines": doctrines,
                    "error": "Invalid doctrine selected.",
                },
            )

        if action == "move":
            old_name = fit.doctrine.name
            fit.doctrine = target
            fit.save(update_fields=["doctrine"])
            _queue_readiness_recompute()
            messages.success(
                request, f'"{fit.name}" moved from {old_name} to {target.name}.'
            )
            return redirect("aa_fleet_hub:manage_index")

        if action == "copy":
            _copy_fit(
                fit,
                target,
                f"{fit.name} (Copy)" if target == fit.doctrine else fit.name,
            )
            _queue_readiness_recompute()
            messages.success(request, f'"{fit.name}" copied to {target.name}.')
            return redirect("aa_fleet_hub:manage_index")

    return render(
        request,
        "aa_fleet_hub/management/fit_move_copy.html",
        {"fit": fit, "doctrines": doctrines},
    )


def _clean_wings(wings):
    """Keep only well-formed wings: {"name": str, "squads": [str, ...]}.

    The form always posts this shape; anything else (a hand-made request)
    would otherwise crash the apply later.
    """
    if not isinstance(wings, list):
        return []
    cleaned = []
    for wing in wings:
        if not isinstance(wing, dict):
            continue
        squads = wing.get("squads")
        cleaned.append(
            {
                "name": str(wing.get("name") or "Wing"),
                "squads": [str(s) for s in squads if isinstance(s, str)]
                if isinstance(squads, list)
                else [],
            }
        )
    return cleaned


def _structure_form(request, structure):
    """Create (structure=None) or edit a fleet structure on Fleet Setup (#3).

    Anyone with Fleet Setup access makes their own; only doctrine managers
    pick a doctrine. Anyone else's new structures are standalone, and their
    edits keep whatever doctrine a manager attached.
    """
    can_attach = _can_manage_doctrines(request.user)
    context = {
        "structure": structure,
        "doctrines": _attachable_doctrines() if can_attach else [],
        "can_attach": can_attach,
    }
    if request.method != "POST":
        return render(request, "aa_fleet_hub/fleet_ops/structure_form.html", context)
    name = request.POST.get("name", "").strip()
    if not name:
        context["error"] = "Template name is required."
        return render(request, "aa_fleet_hub/fleet_ops/structure_form.html", context)
    if structure is None:
        structure = FleetStructureTemplate(created_by=request.user)
    structure.name = name
    if can_attach:
        structure.doctrine = _posted_doctrine(request)
    try:
        posted = json.loads(request.POST.get("structure", "[]"))
    except (json.JSONDecodeError, ValueError):
        posted = []
    structure.structure = {"wings": _clean_wings(posted)}
    structure.is_free_move = "is_free_move" in request.POST
    structure.collapsed_by_default = "collapsed_by_default" in request.POST
    created = structure.pk is None
    structure.save()
    messages.success(
        request,
        "Fleet structure template created."
        if created
        else "Fleet structure template updated.",
    )
    return redirect("aa_fleet_hub:fleet_ops_index")


@login_required
@fleet_ops_access_required
def structure_create(request):
    return _structure_form(request, None)


@login_required
@fleet_ops_access_required
def structure_edit(request, structure_id):
    """Edit a fleet structure: your own, or any for a doctrine manager."""
    structure = get_object_or_404(
        _owned_or_managed(FleetStructureTemplate, request.user), pk=structure_id
    )
    return _structure_form(request, structure)


@login_required
@fleet_ops_access_required
def structure_delete(request, structure_id):
    structure = get_object_or_404(
        _owned_or_managed(FleetStructureTemplate, request.user), pk=structure_id
    )
    if request.method == "POST":
        structure.delete()
        messages.success(request, "Fleet structure template deleted.")
    return redirect("aa_fleet_hub:fleet_ops_index")


def _motd_form(request, motd):
    """Create (motd=None) or edit an MOTD on Fleet Setup (#3).

    The text is saved exactly as sent: never stripped, it is pasted in game.
    Only doctrine managers set the doctrine, default and public; anyone
    else's new MOTDs are standalone, and their edits leave those alone.
    """
    from .models import MotdQuickLink

    can_attach = _can_manage_doctrines(request.user)
    context = {
        "motd": motd,
        "doctrines": _attachable_doctrines() if can_attach else [],
        "can_attach": can_attach,
        "motd_quick_links": list(MotdQuickLink.objects.all()),
        "form": {"name": motd.name, "text": motd.text} if motd else {},
    }
    if request.method != "POST":
        return render(request, "aa_fleet_hub/fleet_ops/motd_form.html", context)
    name = request.POST.get("name", "").strip()
    text = request.POST.get("text", "")
    if not name:
        # Keep what they typed: the text may be long.
        context["error"] = "MOTD name is required."
        context["form"] = {"name": name, "text": text}
        return render(request, "aa_fleet_hub/fleet_ops/motd_form.html", context)
    if motd is None:
        motd = Motd(created_by=request.user)
    motd.name = name[: Motd._meta.get_field("name").max_length]
    motd.text = text
    if can_attach:
        motd.doctrine = _posted_doctrine(request)
        motd.is_default = "is_default" in request.POST
        motd.public = "public" in request.POST
    created = motd.pk is None
    motd.save()
    messages.success(request, "MOTD created." if created else "MOTD updated.")
    return redirect("aa_fleet_hub:fleet_ops_index")


@login_required
@fleet_ops_access_required
def motd_create(request):
    return _motd_form(request, None)


@login_required
@fleet_ops_access_required
def motd_edit(request, motd_id):
    """Edit an MOTD: your own, or any for a doctrine manager."""
    motd = get_object_or_404(_owned_or_managed(Motd, request.user), pk=motd_id)
    return _motd_form(request, motd)


@login_required
@fleet_ops_access_required
@require_POST
def motd_delete(request, motd_id):
    motd = get_object_or_404(_owned_or_managed(Motd, request.user), pk=motd_id)
    motd.delete()
    messages.success(request, "MOTD deleted.")
    return redirect("aa_fleet_hub:fleet_ops_index")


def _can_apply_any_structure(user, structure):
    """Whether a Fleet Setup user may apply this structure as a fleet runner:
    standalone, their own, or on a doctrine they can see. Managers: any."""
    if _can_manage_doctrines(user):
        return True
    if structure.doctrine_id is None or structure.created_by_id == user.pk:
        return True
    return visible_doctrines(user).filter(pk=structure.doctrine_id).exists()


@login_required
@permission_required("aa_fleet_hub.basic_access")
def structure_apply(request, structure_id):
    """Apply fleet structure (wings/squads/free-move/MOTD) to FC's active fleet via ESI.

    Open to Fleet Setup users, and to any member for a structure linked to a
    "visible to all members" doctrine (e.g. Home Defense), so anyone forming
    up can self-apply the layout/MOTD.

    The MOTD pushed is the one posted as motd_id ("" for none), or with no
    motd_id the doctrine's default MOTD. Either way it must be one the user
    can see, and a self-service member only gets the doctrine's own (#3).
    """
    if request.method != "POST":
        return JsonResponse(
            {"status": "error", "message": "POST required."}, status=405
        )

    req = esi_requests

    structure = get_object_or_404(FleetStructureTemplate, pk=structure_id)

    fleet_setup_user = _can_see_fleet_ops(request.user)
    # Self-service only on a doctrine this user can see: the flag alone would
    # let anyone push a hidden or archived doctrine's structure by id (opsec).
    self_service = bool(
        structure.doctrine_id
        and structure.doctrine.visible_to_all_members
        and visible_doctrines(request.user).filter(pk=structure.doctrine_id).exists()
    )
    if fleet_setup_user and not _can_apply_any_structure(request.user, structure):
        # Same set Fleet Setup lists: a structure on a doctrine hidden from
        # this user must not be pushable by guessing its id (opsec).
        fleet_setup_user = False
    if not (self_service or fleet_setup_user):
        return JsonResponse(
            {
                "status": "error",
                "message": "You don't have permission to apply this structure.",
            },
            status=403,
        )

    usable_motds = _visible_motds(request.user)
    if not fleet_setup_user:
        usable_motds = usable_motds.filter(doctrine=structure.doctrine)
    if "motd_id" in request.POST:
        raw_motd_id = request.POST["motd_id"]
        motd = None
        if raw_motd_id:
            motd = usable_motds.filter(pk=_safe_int(raw_motd_id, None)).first()
            if motd is None:
                return JsonResponse(
                    {
                        "status": "error",
                        "message": "That MOTD isn't available to you.",
                    },
                    status=403,
                )
    else:
        motd = (
            usable_motds.filter(doctrine=structure.doctrine, is_default=True).first()
            if structure.doctrine_id
            else None
        )

    # Cleaned again here for rows saved before the form cleaned them.
    wings = _clean_wings(structure.wings())
    if not wings:
        return JsonResponse(
            {"status": "error", "message": "No wings defined in this template."}
        )

    character_id = _active_character_id(request)
    if not character_id:
        return JsonResponse(
            {"status": "error", "message": "No active character selected."}
        )

    token = Token.get_token(character_id, FLEET_SCOPES)
    if not token:
        return JsonResponse(
            {
                "status": "error",
                "message": "No fleet token found — re-authorize your character with fleet scopes.",
            }
        )

    try:
        access_token = token.valid_access_token()
    except Exception:
        logger.warning(
            "Fleet Hub: fleet token refresh failed for character %s",
            character_id,
            exc_info=True,
        )
        return JsonResponse(
            {
                "status": "error",
                "message": "Token refresh failed — re-authorize your character.",
            }
        )

    headers = {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json",
    }
    base = "https://esi.evetech.net/latest"

    # Get active fleet and verify FC role
    try:
        r = req.get(
            f"{base}/characters/{character_id}/fleet/", headers=headers, timeout=10
        )
    except Exception:
        logger.warning(
            "Fleet Hub: ESI network error in structure_apply for character %s",
            character_id,
            exc_info=True,
        )
        return JsonResponse(
            {"status": "error", "message": "Could not reach ESI — try again shortly."}
        )
    if r.status_code == 404:
        return JsonResponse({"status": "error", "message": "You are not in a fleet."})
    if r.status_code != 200:
        return JsonResponse(
            {"status": "error", "message": f"ESI error getting fleet: {r.status_code}"}
        )

    fleet_data = r.json()
    fleet_id = fleet_data.get("fleet_id")
    if fleet_data.get("role") != "fleet_commander":
        return JsonResponse(
            {
                "status": "error",
                "message": "You must be Fleet Commander to apply a structure.",
            }
        )

    # Push free-move + MOTD to fleet settings
    fleet_settings = {"is_free_move": structure.is_free_move}
    if motd is not None and motd.text:
        fleet_settings["motd"] = motd.text
    r = req.put(
        f"{base}/fleets/{fleet_id}/", headers=headers, json=fleet_settings, timeout=10
    )
    if r.status_code not in (200, 204):
        logger.warning(
            "Fleet Hub: ESI fleet settings PUT failed — %s %s", r.status_code, r.text
        )
        return JsonResponse(
            {
                "status": "error",
                "message": "Failed to update fleet settings — check fleet boss status.",
            }
        )

    # Snapshot existing wings before creating new ones (so we can clear them after)
    existing_wing_ids = []
    r = req.get(f"{base}/fleets/{fleet_id}/wings/", headers=headers, timeout=10)
    if r.status_code == 200:
        existing_wing_ids = [w["id"] for w in r.json()]

    # Wing/squad creation is dozens of sequential ESI round-trips for a full
    # template; run it off the request-response cycle so a large structure
    # can't outrun the web worker's request timeout. The free-move/MOTD push
    # and old-wing snapshot above already happened synchronously since those
    # are single calls and their failure should block a false "applied".
    from .tasks import apply_fleet_structure_wings

    apply_fleet_structure_wings.delay(character_id, fleet_id, wings, existing_wing_ids)

    total_wings = len(wings)
    total_squads = sum(len(w.get("squads", [])) for w in wings)
    motd_pushed = "motd" in fleet_settings
    return JsonResponse(
        {
            "status": "ok",
            "message": (
                f"Applying: {total_wings} wing(s), {total_squads} squad(s)"
                f"{', free-move on' if structure.is_free_move else ''}"
                f"{', MOTD pushed' if motd_pushed else ''}…"
            ),
        }
    )


@login_required
@permission_required("aa_fleet_hub.fleet_command")
def readiness_index(request):
    """Fleet Command page — FC-facing readiness stats."""
    from .tasks import get_readiness_summary, invalidate_readiness_cache

    # Counts only; the per-character rows live in each doctrine's own entry.
    readiness = get_readiness_summary()
    if readiness is None:
        # Nothing cached yet (e.g. first deploy, before the hourly task has
        # ever run); request a compute through the debounced path so
        # concurrent page loads during the same gap don't each queue their
        # own recompute.
        invalidate_readiness_cache()
    # Same visibility rules as the member-facing Doctrines page: FCs shouldn't
    # see readiness for group-restricted doctrines they aren't part of.
    doctrines = visible_doctrines(request.user)
    doctrine_readiness = []
    for doctrine in doctrines:
        doctrine_readiness.append(
            {
                "doctrine": doctrine,
                "data": readiness.get(doctrine.pk) if readiness else None,
                # Readiness only covers fits that have skill tiers defined (see
                # update_readiness_cache, which skips tierless fits) — so an empty
                # data["fits"] list doesn't mean the doctrine itself has no fits.
                "has_fits": bool(doctrine.fits.all()),
            }
        )
    context = {
        **_group_by_tag(doctrine_readiness),
        "doctrine_readiness": doctrine_readiness,
        "is_computing": readiness is None,
    }
    return render(request, "aa_fleet_hub/fleet_command/index.html", context)


@login_required
@fleet_ops_access_required
def fleet_ops_index(request):
    """Fleet Setup: MOTDs and fleet structures, made, copied and applied here (#3, #7)."""
    user = request.user
    can_manage = _can_manage_doctrines(user)

    def mark_editable(objs):
        # Same rule as _owned_or_managed: their own, or any for a manager.
        for obj in objs:
            obj.editable = can_manage or obj.created_by_id == user.pk

    motds = list(_visible_motds(user).select_related("doctrine"))
    mark_editable(motds)
    by_doctrine = {}
    for motd in motds:
        if motd.doctrine_id:
            by_doctrine.setdefault(motd.doctrine_id, []).append(motd)

    # visible_doctrines() already prefetches fleet_structures.
    doctrine_ops = []
    for doctrine in visible_doctrines(user):
        doctrine_motds = by_doctrine.get(doctrine.pk, [])
        structures = list(doctrine.fleet_structures.all())
        if not doctrine_motds and not structures:
            continue
        mark_editable(structures)
        doctrine_ops.append(
            {
                "doctrine": doctrine,
                "motds": doctrine_motds,
                "default_motd": next((m for m in doctrine_motds if m.is_default), None),
                "structures": structures,
                "motd_choices": _motd_choices(doctrine, motds),
            }
        )
    standalone_structures = list(
        FleetStructureTemplate.objects.filter(doctrine__isnull=True).order_by("name")
    )
    # Ones on an archived, inactive or hidden doctrine: Manage used to list
    # these, so they stay reachable here for whoever may edit them.
    shown = [item["doctrine"].pk for item in doctrine_ops]
    other_structures = list(
        _owned_or_managed(FleetStructureTemplate, user)
        .filter(doctrine__isnull=False)
        .exclude(doctrine__in=shown)
        .select_related("doctrine")
        .order_by("name")
    )
    mark_editable(standalone_structures + other_structures)

    motd_doctrines = sorted(
        {m.doctrine for m in motds if m.doctrine_id}, key=lambda d: d.name.lower()
    )
    character_id = _active_character_id(request)
    has_fleet_token = bool(character_id and Token.get_token(character_id, FLEET_SCOPES))
    context = {
        **_group_by_tag(doctrine_ops),
        "doctrine_ops": doctrine_ops,
        "motds": motds,
        # Copied from JSON, not the page text: HTML would turn CRLF into LF.
        "motd_texts": {m.pk: m.text for m in motds},
        "motd_doctrines": motd_doctrines,
        "has_standalone_motds": any(m.doctrine_id is None for m in motds),
        "standalone_structures": standalone_structures,
        "structure_sections": [
            ("Standalone Fleet Structures", standalone_structures),
            ("Other Fleet Structures", other_structures),
        ],
        "standalone_motd_choices": _motd_choices(None, motds),
        "has_fleet_token": has_fleet_token,
    }
    return render(request, "aa_fleet_hub/fleet_ops/index.html", context)


@login_required
@permission_required("aa_fleet_hub.fleet_command")
def readiness_doctrine(request, doctrine_id):
    from .tasks import (
        filter_readiness_rows,
        get_doctrine_readiness,
        invalidate_readiness_cache,
    )

    visible = visible_doctrines(request.user).values_list("pk", flat=True)
    doctrine = get_object_or_404(Doctrine, pk=doctrine_id, pk__in=visible)
    doctrine_data, computed = get_doctrine_readiness(doctrine.pk)
    if not computed:
        # Mirrors readiness_index: a deep link straight to a doctrine's page
        # on a cold cache should also queue a (debounced) recompute rather
        # than waiting for the next scheduled run. A missing entry on a warm
        # cache (e.g. a draft, which the task skips) doesn't, or every visit
        # to it would queue a full recompute.
        invalidate_readiness_cache()
    # No row is created just for viewing; a missing one means the defaults.
    pref = FleetHubPreference.objects.filter(user=request.user).first()
    hide_alts = pref.readiness_hide_alts if pref else True
    hide_cant_fly = pref.readiness_hide_cant_fly if pref else True
    if doctrine_data:
        # Filtered here, not in the template or JS, so hidden rows never
        # reach the page.
        doctrine_data = filter_readiness_rows(doctrine_data, hide_alts, hide_cant_fly)
    context = {
        "doctrine": doctrine,
        "doctrine_data": doctrine_data,
        "hide_alts": hide_alts,
        "hide_cant_fly": hide_cant_fly,
        # Mirrors readiness_index: an empty doctrine_data.fits list only means
        # "no fits" if the doctrine really has none — distinguishes that from
        # the (now largely historical, see update_readiness_cache) case of
        # fits being present but omitted from the readiness breakdown.
        "has_fits": bool(doctrine.fits.all()),
        # Ship ownership & location is sensitive intel — gated separately from
        # fleet_command, which every regular FC already has.
        "can_view_assets": request.user.has_perm("aa_fleet_hub.view_member_assets"),
    }
    return render(request, "aa_fleet_hub/fleet_command/doctrine.html", context)


READINESS_FILTER_FIELDS = {
    "alts": "readiness_hide_alts",
    "cant_fly": "readiness_hide_cant_fly",
}


@login_required
@permission_required("aa_fleet_hub.fleet_command")
@require_POST
def readiness_filter_toggle(request):
    """
    Set one Fleet Command row filter (hide alts / hide can't fly) for this user.

    The form posts the state it wants (hide=1 or 0) rather than asking for a
    flip, so a double-click or a button from a stale tab can't do the opposite
    of what it says.
    """
    field = READINESS_FILTER_FIELDS.get(request.POST.get("filter"))
    hide = request.POST.get("hide")
    if field and hide in ("0", "1"):
        pref, _ = FleetHubPreference.objects.get_or_create(user=request.user)
        setattr(pref, field, hide == "1")
        pref.save(update_fields=[field])
    next_url = request.POST.get("next", "")
    if next_url and url_has_allowed_host_and_scheme(
        next_url, allowed_hosts={request.get_host()}
    ):
        return redirect(next_url)
    return redirect("aa_fleet_hub:readiness_index")


# ---------------------------------------------------------------------------
# Management views (FC+ for fits, Officer for doctrines)
# ---------------------------------------------------------------------------


@login_required
@manage_access_required
def manage_index(request):
    """Manage index — doctrine and fit management for FCs and officers."""
    doctrines = (
        _manageable_doctrines(request.user)
        .prefetch_related("fits__tiers", "groups")
        .order_by(*DOCTRINE_ORDER)
    )
    archived_doctrines = (
        Doctrine.objects.filter(archived=True).order_by(*DOCTRINE_ORDER)
        if request.user.has_perm("aa_fleet_hub.manage_doctrines")
        else Doctrine.objects.none()
    )
    context = {
        "doctrines": doctrines,
        **_group_by_tag(doctrines, lambda doctrine: doctrine),
        "archived_doctrines": archived_doctrines,
        "can_manage_doctrines": request.user.has_perm("aa_fleet_hub.manage_doctrines"),
    }
    return render(request, "aa_fleet_hub/management/index.html", context)


def _posted_tag_color(request):
    """The posted tag colour as lowercase #rrggbb, or None if it isn't one."""
    raw = (
        request.POST.get("tag_color", "").strip().lower() or Doctrine.DEFAULT_TAG_COLOR
    )
    return raw if TAG_COLOR_RE.fullmatch(raw) else None


def _doctrine_form_error(request):
    """Why the posted doctrine form can't be saved, or None if it can."""
    if not request.POST.get("name", "").strip():
        return "Doctrine name is required."
    if _posted_tag_color(request) is None:
        return "Tag colour must be a hex code like #3a7bd5."
    return None


@transaction.atomic
def _save_doctrine(request, doctrine=None):
    """Create or update a Doctrine from POST data. Returns the saved instance."""
    is_new = doctrine is None
    if is_new:
        doctrine = Doctrine(created_by=request.user)

    doctrine.name = request.POST.get("name", "").strip()
    doctrine.tag = request.POST.get("tag", "").strip()
    # Already checked by _doctrine_form_error, so this is a valid #rrggbb.
    doctrine.tag_color = _posted_tag_color(request)
    doctrine.description = request.POST.get("description", "").strip()
    doctrine.notes = request.POST.get("notes", "").strip()
    try:
        doctrine.sort_order = int(request.POST.get("sort_order", 0) or 0)
    except (ValueError, TypeError):
        doctrine.sort_order = 0
    doctrine.pinned = "pinned" in request.POST
    doctrine.active = "active" in request.POST
    doctrine.is_draft = "is_draft" in request.POST
    doctrine.public = "public" in request.POST
    doctrine.visible_to_all_members = "visible_to_all_members" in request.POST
    doctrine.collapsed_by_default = "collapsed_by_default" in request.POST
    doctrine.save()

    selected_groups = request.POST.getlist("groups")
    doctrine.groups.set(Group.objects.filter(pk__in=selected_groups))

    selected_motd_groups = request.POST.getlist("motd_groups")
    doctrine.motd_groups.set(Group.objects.filter(pk__in=selected_motd_groups))

    # Shared skills from JSON
    shared_raw = request.POST.get("shared_skills", "[]")
    try:
        shared_data = json.loads(shared_raw)
    except (json.JSONDecodeError, ValueError):
        shared_data = []
    doctrine.shared_skills.all().delete()
    for item in shared_data:
        type_id = _safe_int(item.get("type_id", 0))
        level = _safe_int(item.get("level", 1))
        if type_id and 1 <= level <= 5:
            DoctrineSharedSkill.objects.create(
                doctrine=doctrine,
                skill_type_id=type_id,
                required_level=level,
            )

    from .tasks import invalidate_readiness_cache

    invalidate_readiness_cache()

    return doctrine


def _doctrine_form_context(doctrine=None):
    from .models import FleetHubSettings

    all_groups = list(Group.objects.all().order_by("name"))
    settings = FleetHubSettings.get()
    return {
        "doctrine": doctrine,
        "all_groups_data": [{"pk": g.pk, "name": g.name} for g in all_groups],
        "selected_group_ids": list(doctrine.groups.values_list("pk", flat=True))
        if doctrine
        else [],
        "selected_motd_group_ids": list(
            doctrine.motd_groups.values_list("pk", flat=True)
        )
        if doctrine
        else [],
        "doctrine_default_visible": settings.doctrine_default_visible,
        "tag_color": doctrine.tag_color_hex if doctrine else Doctrine.DEFAULT_TAG_COLOR,
        "tag_color_swatches": Doctrine.TAG_COLOR_SWATCHES,
    }


@login_required
@permission_required("aa_fleet_hub.manage_doctrines")
def doctrine_create(request):
    if request.method == "POST":
        error = _doctrine_form_error(request)
        if error:
            ctx = _doctrine_form_context()
            ctx["error"] = error
            return render(request, "aa_fleet_hub/management/doctrine_form.html", ctx)
        _save_doctrine(request)
        messages.success(request, "Doctrine created.")
        return redirect("aa_fleet_hub:manage_index")
    return render(
        request, "aa_fleet_hub/management/doctrine_form.html", _doctrine_form_context()
    )


@login_required
@permission_required("aa_fleet_hub.manage_doctrines")
def doctrine_edit(request, doctrine_id):
    doctrine = get_object_or_404(Doctrine, pk=doctrine_id)
    if request.method == "POST":
        error = _doctrine_form_error(request)
        if error:
            ctx = _doctrine_form_context(doctrine)
            ctx["error"] = error
            return render(request, "aa_fleet_hub/management/doctrine_form.html", ctx)
        _save_doctrine(request, doctrine)
        messages.success(request, "Doctrine updated.")
        return redirect("aa_fleet_hub:manage_index")
    return render(
        request,
        "aa_fleet_hub/management/doctrine_form.html",
        _doctrine_form_context(doctrine),
    )


@login_required
@permission_required("aa_fleet_hub.manage_doctrines")
def doctrine_delete(request, doctrine_id):
    doctrine = get_object_or_404(Doctrine, pk=doctrine_id)
    if request.method == "POST":
        doctrine.delete()
        _queue_readiness_recompute()
        messages.success(request, "Doctrine deleted.")
        return redirect("aa_fleet_hub:manage_index")
    return render(
        request,
        "aa_fleet_hub/management/doctrine_confirm_delete.html",
        {"doctrine": doctrine},
    )


@login_required
@permission_required("aa_fleet_hub.manage_doctrines")
@require_POST
def doctrine_archive(request, doctrine_id):
    doctrine = get_object_or_404(Doctrine, pk=doctrine_id)
    doctrine.archived = True
    doctrine.save(update_fields=["archived"])
    _queue_readiness_recompute()
    messages.success(request, f"{doctrine.name} archived.")
    return redirect("aa_fleet_hub:manage_index")


@login_required
@permission_required("aa_fleet_hub.manage_doctrines")
@require_POST
def doctrine_restore(request, doctrine_id):
    doctrine = get_object_or_404(Doctrine, pk=doctrine_id)
    doctrine.archived = False
    doctrine.save(update_fields=["archived"])
    _queue_readiness_recompute()
    messages.success(request, f"{doctrine.name} restored.")
    return redirect("aa_fleet_hub:manage_index")


@login_required
@permission_required("aa_fleet_hub.manage_doctrines")
@transaction.atomic
def doctrine_duplicate(request, doctrine_id):
    if request.method != "POST":
        return redirect("aa_fleet_hub:manage_index")

    src = get_object_or_404(
        Doctrine.objects.prefetch_related(
            "groups", "motd_groups", "motds", "shared_skills", "fits__tiers__skills"
        ),
        pk=doctrine_id,
    )
    new = Doctrine.objects.create(
        name=f"{src.name} (Copy)",
        tag=src.tag,
        tag_color=src.tag_color,
        description=src.description,
        notes=src.notes,
        visible_to_all_members=src.visible_to_all_members,
        sort_order=src.sort_order,
        active=src.active,
        is_draft=src.is_draft,
        public=src.public,
        collapsed_by_default=src.collapsed_by_default,
        created_by=request.user,
    )
    new.groups.set(src.groups.all())
    new.motd_groups.set(src.motd_groups.all())
    # The copy gets its own MOTDs, made by whoever duplicated it (#3).
    Motd.objects.bulk_create(
        Motd(
            name=motd.name,
            text=motd.text,
            doctrine=new,
            is_default=motd.is_default,
            public=motd.public,
            created_by=request.user,
        )
        for motd in src.motds.all()
    )

    for ss in src.shared_skills.all():
        DoctrineSharedSkill.objects.create(
            doctrine=new,
            skill_type_id=ss.skill_type_id,
            required_level=ss.required_level,
        )
    for fit in src.fits.all():
        _copy_fit(fit, new, fit.name)
    _queue_readiness_recompute()

    messages.success(request, f'Doctrine duplicated as "{new.name}".')
    return redirect("aa_fleet_hub:manage_index")


def _eft_form_error(request, ship_type_id):
    """Header validation for the fit form; None when the EFT is usable."""
    if "is_skill_plan" in request.POST:
        return None
    ship = EveType.objects.filter(id=ship_type_id).first() if ship_type_id else None
    return eft_header_error(
        request.POST.get("eft", "").strip(), ship.name if ship else ""
    )


def _save_fit(request, fit, doctrine):
    is_new = fit.pk is None
    is_skill_plan = "is_skill_plan" in request.POST
    ship_type_id = request.POST.get("ship_type_id", "").strip()
    fit.doctrine = doctrine
    fit.name = request.POST.get("name", "").strip()
    fit.role = request.POST.get("role", "").strip()
    fit.description = request.POST.get("description", "").strip()
    fit.notes = request.POST.get("notes", "").strip()
    raw_eft = request.POST.get("eft", "").strip() if not is_skill_plan else ""
    fit.eft = raw_eft
    fit.is_skill_plan = is_skill_plan
    fit.img_endpoint = "icon" if is_skill_plan else "render"
    fit.version = request.POST.get("version", fit.version or "1.0").strip() or "1.0"
    try:
        fit.sort_order = int(request.POST.get("sort_order", 0) or 0)
    except ValueError:
        fit.sort_order = 0
    if fit.eft:
        lines = fit.eft.split("\n")
        lines[0] = re.sub(r"(\[[^\]]+)\s+v[\w.]+\]", r"\1]", lines[0])
        lines[0] = re.sub(
            r"(\[[^\]]+)\]", lambda m: f"{m.group(1)} v{fit.version}]", lines[0]
        )
        fit.eft = "\n".join(lines)
    if ship_type_id.isdigit():
        fit.ship_type_id = int(ship_type_id)
    if fit.eft and fit.ship_type_id:
        # A header pasted without the ship ("[Fit Name]") won't import into
        # EVE; fill it in from the selected hull.
        ship = EveType.objects.filter(id=fit.ship_type_id).first()
        if ship:
            fit.eft = ensure_eft_ship_name(fit.eft, ship.name)

    # PI plans (skill plans only)
    if is_skill_plan:
        pi_raw = request.POST.get("pi_plans", "[]")
        try:
            fit.pi_plans = json.loads(pi_raw) or None
        except (json.JSONDecodeError, ValueError):
            fit.pi_plans = None
    else:
        fit.pi_plans = None

    fit.save()

    changelog_note = request.POST.get("changelog_note", "").strip()
    if is_new:
        FitChangeLog.objects.create(
            fit=fit,
            version=fit.version,
            note=changelog_note or "Created",
            changed_by=request.user,
        )
    elif changelog_note:
        FitChangeLog.objects.create(
            fit=fit, version=fit.version, note=changelog_note, changed_by=request.user
        )

    # Rebuild tiers atomically — delete-before-create must not leave the fit
    # with zero tiers if a DB error fires mid-loop (would score everyone Ready).
    with transaction.atomic():
        fit.tiers.all().delete()
        for n in range(1, 11):
            skills_json = request.POST.get(f"tier_skills_{n}", "")
            label = request.POST.get(f"tier_label_{n}", "").strip()
            if not label and not skills_json:
                continue
            label = label or f"Tier {n}"
            try:
                skills_data = json.loads(skills_json or "[]")
            except (json.JSONDecodeError, ValueError):
                skills_data = []
            tier = FitTier.objects.create(
                fit=fit,
                label=label,
                description=request.POST.get(f"tier_desc_{n}", "").strip(),
                warning=request.POST.get(f"tier_warn_{n}", "").strip(),
                sort_order=n - 1,
            )
            for i, skill in enumerate(skills_data):
                type_id = _safe_int(skill.get("type_id", 0))
                level = _safe_int(skill.get("level", 1))
                if type_id and 1 <= level <= 5:
                    FitTierSkill.objects.create(
                        tier=tier,
                        skill_type_id=type_id,
                        required_level=level,
                    )

    from .tasks import invalidate_readiness_cache

    invalidate_readiness_cache()

    return fit


def _fit_form_draft(request, fit, doctrine):
    """
    Unsaved Fit plus tier dicts built from the POST, so a rejected save
    re-renders with everything the user typed instead of wiping the form.
    """
    post = request.POST
    draft = Fit(pk=fit.pk if fit else None, doctrine=doctrine)
    draft.name = post.get("name", "").strip()
    draft.role = post.get("role", "").strip()
    draft.description = post.get("description", "").strip()
    draft.notes = post.get("notes", "").strip()
    draft.eft = post.get("eft", "").strip()
    draft.is_skill_plan = "is_skill_plan" in post
    draft.version = post.get("version", "").strip() or "1.0"
    draft.sort_order = _safe_int(post.get("sort_order", 0))
    draft.changelog_note = post.get("changelog_note", "").strip()
    ship_type_id = post.get("ship_type_id", "").strip()
    draft.ship_type_id = (
        int(ship_type_id)
        if ship_type_id.isdigit()
        else (fit.ship_type_id if fit else None)
    )
    ship = (
        EveType.objects.filter(id=draft.ship_type_id).first()
        if draft.ship_type_id
        else None
    )
    draft.ship_name = ship.name if ship else ""
    try:
        draft.pi_plans = json.loads(post.get("pi_plans", "") or "null") or None
    except (json.JSONDecodeError, ValueError):
        draft.pi_plans = None

    tiers = []
    for n in range(1, 11):
        skills_json = post.get(f"tier_skills_{n}", "")
        label = post.get(f"tier_label_{n}", "").strip()
        if not label and not skills_json:
            continue
        try:
            skills_data = json.loads(skills_json or "[]")
        except (json.JSONDecodeError, ValueError):
            skills_data = []
        skills = [
            {
                "type_id": _safe_int(sk.get("type_id", 0)),
                "level": _safe_int(sk.get("level", 1)),
            }
            for sk in skills_data
            if isinstance(sk, dict)
        ]
        tiers.append(
            {
                "label": label,
                "description": post.get(f"tier_desc_{n}", "").strip(),
                "warning": post.get(f"tier_warn_{n}", "").strip(),
                "skills": [sk for sk in skills if sk["type_id"]],
            }
        )
    names = EveType.objects.filter(
        id__in={sk["type_id"] for t in tiers for sk in t["skills"]}
    ).in_bulk()
    for t in tiers:
        for sk in t["skills"]:
            et = names.get(sk["type_id"])
            sk["name"] = et.name if et else f"Skill #{sk['type_id']}"
    return draft, tiers


def _saved_fit_tiers(fit):
    return [
        {
            "label": tier.label,
            "description": tier.description,
            "warning": tier.warning,
            "skills": [
                {
                    "type_id": sk.skill_type_id,
                    "name": sk.skill_name,
                    "level": sk.required_level,
                }
                for sk in tier.skills.all()
            ],
        }
        for tier in fit.tiers.all()
    ]


def _render_fit_form(request, doctrine, fit=None, error=None):
    """
    Render the fit form. `fit` is the saved Fit (None when creating) and
    drives Edit vs New; `form` holds the field values, taken from the POST
    when re-rendering after an error.
    """
    if error:
        form, tiers = _fit_form_draft(request, fit, doctrine)
    elif fit:
        form, tiers = fit, _saved_fit_tiers(fit)
        ship = EveType.objects.filter(id=fit.ship_type_id).first()
        fit.ship_name = ship.name if ship else ""
    else:
        form, tiers = None, []
    context = {
        "fit": fit,
        "form": form,
        "form_tiers": tiers,
        "doctrine": doctrine,
        "error": error,
    }
    if fit:
        context["changelog"] = fit.changelog.select_related(
            "changed_by__profile__main_character"
        ).all()
        context["can_manage_doctrines"] = request.user.has_perm(
            "aa_fleet_hub.manage_doctrines"
        )
    return render(request, "aa_fleet_hub/management/fit_form.html", context)


@login_required
@manage_access_required
def fit_create(request, doctrine_id):
    doctrine = get_object_or_404(_manageable_doctrines(request.user), pk=doctrine_id)
    if request.method == "POST":
        ship_type_id = request.POST.get("ship_type_id", "").strip()
        is_skill_plan = "is_skill_plan" in request.POST
        error = None
        if not request.POST.get("name", "").strip():
            error = "Fit name is required."
        elif not ship_type_id.isdigit() and not is_skill_plan:
            error = "Please select a ship."
        else:
            error = _eft_form_error(
                request, int(ship_type_id) if ship_type_id.isdigit() else 0
            )
        if error:
            return _render_fit_form(request, doctrine, error=error)
        fit = Fit(
            doctrine=doctrine,
            ship_type_id=int(ship_type_id) if ship_type_id.isdigit() else 0,
        )
        _save_fit(request, fit, doctrine)
        messages.success(request, "Fit created.")
        return redirect("aa_fleet_hub:manage_index")
    return _render_fit_form(request, doctrine)


@login_required
@manage_access_required
def fit_edit(request, fit_id):
    manageable = _manageable_doctrines(request.user).values_list("pk", flat=True)
    fit = get_object_or_404(
        Fit.objects.select_related("doctrine"), pk=fit_id, doctrine__in=manageable
    )
    if request.method == "POST":
        if not request.POST.get("name", "").strip():
            error = "Fit name is required."
        else:
            posted_ship = request.POST.get("ship_type_id", "").strip()
            error = _eft_form_error(
                request,
                int(posted_ship) if posted_ship.isdigit() else fit.ship_type_id,
            )
        if error:
            return _render_fit_form(request, fit.doctrine, fit, error=error)
        _save_fit(request, fit, fit.doctrine)
        messages.success(request, "Fit updated.")
        return redirect("aa_fleet_hub:manage_index")
    return _render_fit_form(request, fit.doctrine, fit)


@login_required
@manage_access_required
def fit_duplicate(request, fit_id):
    if request.method != "POST":
        return redirect("aa_fleet_hub:manage_index")
    manageable = _manageable_doctrines(request.user).values_list("pk", flat=True)
    src = get_object_or_404(
        Fit.objects.prefetch_related("tiers__skills"),
        pk=fit_id,
        doctrine__in=manageable,
    )
    new_fit = _copy_fit(src, src.doctrine, f"{src.name} (Copy)")
    _queue_readiness_recompute()
    messages.success(request, f'Fit duplicated as "{new_fit.name}".')
    return redirect("aa_fleet_hub:manage_index")


@login_required
@manage_access_required
def fit_delete(request, fit_id):
    manageable = _manageable_doctrines(request.user).values_list("pk", flat=True)
    fit = get_object_or_404(Fit, pk=fit_id, doctrine__in=manageable)
    if request.method == "POST":
        fit.delete()
        _queue_readiness_recompute()
        messages.success(request, "Fit removed.")
        return redirect("aa_fleet_hub:manage_index")
    return render(
        request, "aa_fleet_hub/management/fit_confirm_delete.html", {"fit": fit}
    )


@login_required
@permission_required("aa_fleet_hub.manage_doctrines")
def changelog_delete(request, entry_id):
    entry = get_object_or_404(FitChangeLog, pk=entry_id)
    fit_id = entry.fit_id
    if request.method == "POST":
        entry.delete()
        messages.success(request, "Changelog entry removed.")
    return redirect("aa_fleet_hub:fit_edit", fit_id=fit_id)


# ---------------------------------------------------------------------------
# SDE autocomplete
# ---------------------------------------------------------------------------


@login_required
@manage_access_required
def autocomplete_skills(request):
    q = request.GET.get("q", "").strip()
    if len(q) < 2:
        return JsonResponse({"results": []})
    results = list(
        EveType.objects.filter(
            eve_group__eve_category_id=16,
            published=True,
            name__icontains=q,
        )
        .select_related("eve_group")
        .values("id", "name", "eve_group__name")[:50]
    )
    return JsonResponse({"results": results})


@login_required
@manage_access_required
def autocomplete_ships(request):
    q = request.GET.get("q", "").strip()
    if len(q) < 2:
        return JsonResponse({"results": []})
    results = list(
        EveType.objects.filter(
            eve_group__eve_category_id=6,
            published=True,
            name__icontains=q,
        )
        .select_related("eve_group")
        .values("id", "name", "eve_group__name")[:50]
    )
    return JsonResponse({"results": results})


@login_required
@manage_access_required
def autocomplete_skill_exact(request):
    """Exact name lookup for paste list import — returns single typeId or null."""
    name = request.GET.get("name", "").strip()
    if not name:
        return JsonResponse({"id": None})
    try:
        t = EveType.objects.get(
            eve_group__eve_category_id=16,
            published=True,
            name__iexact=name,
        )
        return JsonResponse({"id": t.id, "name": t.name})
    except EveType.DoesNotExist:
        return JsonResponse({"id": None})


def sso_callback(request):
    """Wraps django-esi's SSO callback with a friendly fallback.

    django-esi 9.5.0's receive_callback() doesn't guard against
    request.session.session_key being None (crashes with a 500), which
    happens if a browser drops the session cookie set just before the
    redirect to EVE's login page (e.g. tracking-protection cookie
    purging). It also raises Http404 for a stale/already-used callback
    link. Both cases just mean "start the login over".
    """
    if not request.session.session_key:
        messages.error(
            request,
            "Your login session wasn't found — your browser may have blocked "
            "the session cookie. Please try logging in again.",
        )
        return redirect("/")
    try:
        return _esi_receive_callback(request)
    except Http404:
        messages.error(
            request,
            "Your login link has expired or was already used. Please try logging in again.",
        )
        return redirect("/")
