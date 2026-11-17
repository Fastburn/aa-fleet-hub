"""Celery tasks for Fleet Hub."""

import logging
import time

import requests as esi_requests
from celery import shared_task
from django.core.cache import cache
from django.db.models import Max
from eveuniverse.models import EveType

from .app_settings import readiness_cache_ttl

logger = logging.getLogger(__name__)

# Readiness is cached as one entry per doctrine (full per-character rows) plus
# a summary entry (counts only) for the Fleet Command index and dashboard, so
# a page only loads what it shows. The computed-at marker is written last and
# is what tells "not computed yet" apart from "no data for this doctrine".
READINESS_CACHE_PREFIX = "fleet_hub_readiness_v2"
READINESS_SUMMARY_KEY = f"{READINESS_CACHE_PREFIX}:summary"
READINESS_COMPUTED_KEY = f"{READINESS_CACHE_PREFIX}:computed_at"
LEGACY_READINESS_CACHE_KEY = "fleet_hub_readiness_v1"

# Per-fit fields too large for the summary: one row per character.
_DETAIL_FIT_FIELDS = ("per_char", "gap_analysis")

FLEET_SCOPES = ["esi-fleets.read_fleet.v1", "esi-fleets.write_fleet.v1"]
ESI_BASE = "https://esi.evetech.net/latest"


@shared_task
def refresh_eve_type_catalog():
    """
    Re-pull all ship (category 6) and skill (category 16) types from ESI so new
    hulls/skills released by CCP show up in the doctrine/fit autocompletes
    without anyone needing to run a management command. Run monthly by Celery beat.
    """
    from eveuniverse.tasks import load_eve_types

    load_eve_types(category_ids=[6, 16])


@shared_task
def load_ship_market_groups(type_ids):
    """
    eveuniverse skips market groups unless asked. Fetch them for hulls the
    doctrine search found without a ship class, so the class shows up.
    """
    for type_id in type_ids:
        try:
            EveType.objects.update_or_create_esi(
                id=type_id, enabled_sections=[EveType.Section.MARKET_GROUPS]
            )
        except Exception:
            logger.warning(
                "Could not load market groups for type %s", type_id, exc_info=True
            )


@shared_task
def update_readiness_cache():
    """
    Compute alliance-wide readiness stats for all active doctrines.
    Stores result in Django cache (Redis). Run by Celery beat every
    FLEET_HUB_READINESS_INTERVAL minutes, after doctrine/fit edits (debounced
    via invalidate_readiness_cache), and by the admin "Recompute Readiness Now"
    button. Skill syncs deliberately don't trigger it; the next scheduled run
    picks them up.
    """
    try:
        from memberaudit.models import (
            Character,
            CharacterAsset,
            CharacterSkill,
            CharacterSkillqueueEntry,
        )
    except ImportError:
        return

    from allianceauth.authentication.models import CharacterOwnership, UserProfile

    from .models import Doctrine, FitTierSkill, DoctrineSharedSkill

    # Only load the skill typeIds our doctrines actually reference
    # This avoids loading thousands of irrelevant skill rows
    needed_type_ids = set(
        FitTierSkill.objects.values_list("skill_type_id", flat=True)
    ) | set(DoctrineSharedSkill.objects.values_list("skill_type_id", flat=True))

    if not needed_type_ids:
        _store_readiness({})
        return

    # --- Load only relevant character skills in bulk ---
    # {character_pk: {eve_type_id: trained_level}}
    all_skills = {}
    for cs in CharacterSkill.objects.filter(eve_type_id__in=needed_type_ids).values(
        "character_id", "eve_type_id", "trained_skill_level"
    ):
        cid = cs["character_id"]
        if cid not in all_skills:
            all_skills[cid] = {}
        all_skills[cid][cs["eve_type_id"]] = cs["trained_skill_level"]

    # {character_pk: {eve_type_id: max_queued_level}} — only needed typeIds
    all_queued = {}
    for entry in (
        CharacterSkillqueueEntry.objects.filter(eve_type_id__in=needed_type_ids)
        .values("character_id", "eve_type_id")
        .annotate(max_level=Max("finished_level"))
    ):
        cid = entry["character_id"]
        if cid not in all_queued:
            all_queued[cid] = {}
        all_queued[cid][entry["eve_type_id"]] = entry["max_level"]

    # --- Load character info ---
    chars = list(
        Character.objects.select_related("eve_character").values(
            "pk",
            "eve_character_id",
            "eve_character__character_id",
            "eve_character__character_name",
            "eve_character__corporation_name",
        )
    )

    # Main/alt lookup: eve_character pk (of the owning EveCharacter) -> owning user,
    # and user -> their main's eve_character pk. Bulk, no N+1 per character.
    ownership_user_map = dict(
        CharacterOwnership.objects.values_list("character_id", "user_id")
    )
    main_char_map = dict(
        UserProfile.objects.exclude(main_character__isnull=True).values_list(
            "user_id", "main_character_id"
        )
    )

    # --- Load all doctrine/fit/tier/skill data ---
    doctrines = list(
        Doctrine.objects.filter(active=True, is_draft=False).prefetch_related(
            "fits__tiers__skills"
        )
    )

    # Single bulk EveType fetch for all skill type IDs across all doctrines/fits
    all_type_ids = {
        sk.skill_type_id
        for doctrine in doctrines
        for fit in doctrine.fits.all()
        for tier in fit.tiers.all()
        for sk in tier.skills.all()
    }
    eve_type_names = EveType.objects.filter(id__in=all_type_ids).in_bulk()

    # Bulk ship-ownership lookup for every doctrine ship (with or without a skill
    # plan) — {(character_pk, ship_type_id): {location_name: count}}. Gated for
    # display behind the view_member_assets permission, not computed on demand,
    # so it rides the same hourly cache as everything else.
    all_ship_type_ids = {
        fit.ship_type_id
        for doctrine in doctrines
        for fit in doctrine.fits.all()
        if fit.ship_type_id
    }
    owned_hulls_map: dict = {}
    if chars and all_ship_type_ids:
        char_pks = [c["pk"] for c in chars]
        for cid, tid, loc_name in (
            CharacterAsset.objects.filter(
                character_id__in=char_pks, eve_type_id__in=all_ship_type_ids
            )
            .select_related("location")
            .values_list("character_id", "eve_type_id", "location__name")
        ):
            label = loc_name or "Unknown location"
            per = owned_hulls_map.setdefault((cid, tid), {})
            per[label] = per.get(label, 0) + 1

    char_pilot = {
        char["pk"]: pilot_key(
            ownership_user_map.get(char["eve_character_id"]), char["pk"]
        )
        for char in chars
    }
    pilot_total = len(set(char_pilot.values()))

    result = {}
    for doctrine in doctrines:
        doctrine_data = {
            "name": doctrine.name,
            # Must equal len(chars): tier_counts below buckets every char (including
            # those with no relevant skills, who land in "none"), so total has to
            # cover the same population or "total - tier_counts[...]" goes negative.
            "total": len(chars),
            "pilot_total": pilot_total,
            "fits": [],
        }

        for fit in doctrine.fits.all():
            tiers = list(fit.tiers.all())
            tier_count = len(tiers)
            # A ship with no skill tiers defined has nothing to evaluate — surface
            # it as "not ready" (red) rather than silently omitting it, so it can't
            # read as green/fine to anyone scanning the breakdown.
            no_skill_plan = tier_count == 0

            rec_index = (
                tier_count - 1
                if tier_count == 1
                else (tier_count // 2 if tier_count > 2 else 0)
            )

            # Pre-build tier skill requirements: [(type_id, level), ...]
            tier_skills = [
                [(sk.skill_type_id, sk.required_level) for sk in tier.skills.all()]
                for tier in tiers
            ]
            tier_labels = [t.label for t in tiers]

            # Per-tier counts using actual tier labels + "none"
            tier_counts = {label: 0 for label in tier_labels}
            tier_counts["none"] = 0
            per_char = []
            pilot_rows = []  # (pilot key, best tier index or None) per character

            for char in chars:
                cid = char["pk"]
                skills = all_skills.get(cid, {})

                # Find highest tier met (never any if there's no skill plan)
                best = None
                if not no_skill_plan:
                    for i, reqs in enumerate(tier_skills):
                        if all(skills.get(tid, 0) >= lvl for tid, lvl in reqs):
                            best = i

                tier_label = tier_labels[best] if best is not None else None
                bucket_key = tier_label if tier_label else "none"
                tier_counts[bucket_key] += 1
                pilot_rows.append((char_pilot[cid], best))

                # Legacy bucket for breakdown page badge colours
                if best is None:
                    bucket = "none"
                elif best >= tier_count - 1:
                    bucket = "max"
                elif best >= rec_index:
                    bucket = "rec"
                else:
                    bucket = "min"

                eve_char_pk = char["eve_character_id"]
                user_id = ownership_user_map.get(eve_char_pk)
                is_main = (
                    user_id is not None and main_char_map.get(user_id) == eve_char_pk
                )

                hulls = owned_hulls_map.get((cid, fit.ship_type_id), {})
                owned_hulls = [
                    f"{name} (×{count})" if count > 1 else name
                    for name, count in hulls.items()
                ]

                per_char.append(
                    {
                        "character_id": char["eve_character__character_id"],
                        "character_name": char["eve_character__character_name"],
                        "corp": char["eve_character__corporation_name"],
                        "best_tier": tier_label,
                        "bucket": bucket,
                        "user_id": user_id,
                        "is_main": is_main,
                        "owned_hulls": owned_hulls,
                    }
                )

            pilot_tier_counts = count_pilots_by_tier(pilot_rows, tier_labels)

            # Gap analysis — count how many pilots are missing each skill
            # across ALL tiers (unique per skill, using highest required level).
            # Empty when no_skill_plan, since tier_skills is empty.
            skill_gaps = {}  # {type_id: {"name": str, "level": int, "count": int}}
            all_reqs = {}  # {type_id: max required level across all tiers}
            for reqs in tier_skills:
                for tid, lvl in reqs:
                    all_reqs[tid] = max(all_reqs.get(tid, 0), lvl)

            for tid, req_lvl in all_reqs.items():
                missing_count = sum(
                    1
                    for char in chars
                    if all_skills.get(char["pk"], {}).get(tid, 0) < req_lvl
                )
                if missing_count > 0:
                    eve_type = eve_type_names.get(tid)
                    if not eve_type:
                        logger.warning(
                            "Fleet Hub: EveType %s not in local DB — run eveuniverse sync",
                            tid,
                        )
                    name = eve_type.name if eve_type else f"Skill #{tid}"
                    skill_gaps[tid] = {
                        "name": name,
                        "level": req_lvl,
                        "count": missing_count,
                    }

            # Sort by most pilots missing, then by skill name
            gap_list = sorted(
                skill_gaps.values(), key=lambda x: (-x["count"], x["name"])
            )

            # Cap to skills missing by at least 33% of chars, max 5 entries
            if chars:
                threshold = max(1, round(len(chars) * 0.33))
                gap_list = [g for g in gap_list if g["count"] >= threshold][:5]

            if no_skill_plan:
                display_tiers = [
                    {
                        "label": "No Skill Plan",
                        "count": len(chars),
                        "pilots": pilot_total,
                        "color": "#e05252",
                    }
                ]
            else:
                # Build display list: highest tier first (green), down to lowest (yellow), then not yet (red)
                tier_colors = ["#4caf6b", "#4d9de0", "#c8a84b", "#e08c22", "#c8a84b"]
                display_tiers = []
                for i, label in enumerate(reversed(tier_labels)):
                    color = tier_colors[min(i, len(tier_colors) - 1)]
                    display_tiers.append(
                        {
                            "label": label,
                            "count": tier_counts[label],
                            "pilots": pilot_tier_counts[label],
                            "color": color,
                        }
                    )
                display_tiers.append(
                    {
                        "label": "Not Yet",
                        "count": tier_counts["none"],
                        "pilots": pilot_tier_counts["none"],
                        "color": "#e05252",
                    }
                )

            doctrine_data["fits"].append(
                {
                    "fit_id": fit.pk,
                    "fit_name": fit.name,
                    "ship_type_id": fit.ship_type_id,
                    "no_skill_plan": no_skill_plan,
                    "tier_labels": tier_labels,
                    "tier_counts": tier_counts,
                    "pilot_tier_counts": pilot_tier_counts,
                    "display_tiers": display_tiers,
                    "per_char": per_char,
                    "gap_analysis": gap_list,
                }
            )

        result[doctrine.pk] = doctrine_data

    _store_readiness(result)


def _doctrine_readiness_key(doctrine_id):
    return f"{READINESS_CACHE_PREFIX}:doctrine:{doctrine_id}"


def _store_readiness(result):
    """
    Write {doctrine_pk: doctrine_data} as per-doctrine entries plus a summary.

    Entries for doctrines that dropped out since the last run (archived,
    deactivated, turned back into drafts) are deleted so they can't be served
    stale, along with the pre-split single-entry cache.
    """
    ttl = readiness_cache_ttl()
    previous = cache.get(READINESS_SUMMARY_KEY) or {}
    summary = {
        doctrine_id: {
            **data,
            "fits": [
                {k: v for k, v in fit.items() if k not in _DETAIL_FIT_FIELDS}
                for fit in data["fits"]
            ],
        }
        for doctrine_id, data in result.items()
    }
    cache.set_many(
        {_doctrine_readiness_key(pk): data for pk, data in result.items()}, ttl
    )
    cache.set(READINESS_SUMMARY_KEY, summary, ttl)
    cache.set(READINESS_COMPUTED_KEY, time.time(), ttl)
    cache.delete_many(
        [_doctrine_readiness_key(pk) for pk in previous if pk not in result]
        + [LEGACY_READINESS_CACHE_KEY]
    )


def get_readiness_summary():
    """
    Return {doctrine_pk: summary} (counts per fit, no per-character rows), or
    None if readiness has not been computed yet.
    """
    return cache.get(READINESS_SUMMARY_KEY)


def get_doctrine_readiness(doctrine_id):
    """
    Return (doctrine_data, computed) for one doctrine, reading only its entry.

    doctrine_data is None when there's nothing cached for it; computed says
    whether a run has completed at all, so callers queue a recompute only for
    a cold cache, not for a doctrine the task skips (e.g. a draft).
    """
    key = _doctrine_readiness_key(doctrine_id)
    found = cache.get_many([key, READINESS_COMPUTED_KEY])
    return found.get(key), READINESS_COMPUTED_KEY in found


def get_all_doctrine_readiness():
    """Return {doctrine_pk: doctrine_data} for every cached doctrine (admin export)."""
    summary = get_readiness_summary() or {}
    found = cache.get_many([_doctrine_readiness_key(pk) for pk in summary])
    return {
        pk: found[_doctrine_readiness_key(pk)]
        for pk in summary
        if _doctrine_readiness_key(pk) in found
    }


def filter_readiness_rows(doctrine_data, hide_alts, hide_cant_fly):
    """
    Return a copy of doctrine_data with hidden characters left out of per_char.

    Each fit gains hidden_alts / hidden_cant_fly: how many rows that toggle
    would bring back on its own. A row matching both filters counts for
    neither, since showing just one would still leave it hidden. "Can't fly"
    never applies to a fit with no skill plan, where every row would match.
    """
    fits = []
    for fit in doctrine_data["fits"]:
        shown = []
        hidden_alts = hidden_cant_fly = 0
        for char in fit["per_char"]:
            # Same alt rule as the page: no main, but an owner (Q5: an unowned
            # character stands as its own pilot and is never an alt).
            alt = hide_alts and not char["is_main"] and char["user_id"] is not None
            cant_fly = (
                hide_cant_fly and not fit["no_skill_plan"] and char["bucket"] == "none"
            )
            if alt and not cant_fly:
                hidden_alts += 1
            elif cant_fly and not alt:
                hidden_cant_fly += 1
            elif not alt:
                shown.append(char)
        fits.append(
            {
                **fit,
                "per_char": shown,
                "hidden_alts": hidden_alts,
                "hidden_cant_fly": hidden_cant_fly,
            }
        )
    return {**doctrine_data, "fits": fits}


def pilot_key(user_id, char_pk):
    """
    Group a character under its pilot: the owning user (main + alts). A
    character with no owner counts as its own pilot, so it's never folded
    into someone else and never collides with a user id.
    """
    return user_id if user_id is not None else ("char", char_pk)


def count_pilots_by_tier(pilot_rows, tier_labels):
    """
    Same buckets as a fit's tier_counts, but each pilot counted once, in the
    best tier any of their characters reaches.

    pilot_rows: (pilot key, best tier index or None) for each character.
    """
    pilot_best = {}  # pilot key -> best tier index (-1 = none)
    for key, best in pilot_rows:
        pilot_best[key] = max(pilot_best.get(key, -1), -1 if best is None else best)
    counts = {label: 0 for label in tier_labels}
    counts["none"] = 0
    for best in pilot_best.values():
        counts[tier_labels[best] if best >= 0 else "none"] += 1
    return counts


RECOMPUTE_SCHEDULED_KEY = "fleet_hub_readiness_recompute_scheduled"
RECOMPUTE_DEBOUNCE = 30  # seconds — matches the recompute countdown below


def invalidate_readiness_cache():
    """
    Queue a recompute without clearing the existing cache.

    The stale data stays in place (and keeps serving read requests) until
    update_readiness_cache() overwrites it with fresh results, so pages
    never hit an empty cache while a recompute is pending.

    Uses cache.add() as a short-lived lock so a burst of edits (e.g. an
    officer saving several fits in a row) collapses into a single queued
    recompute instead of one full recompute per save.
    """
    if cache.add(RECOMPUTE_SCHEDULED_KEY, True, RECOMPUTE_DEBOUNCE):
        update_readiness_cache.apply_async(countdown=RECOMPUTE_DEBOUNCE)


@shared_task
def apply_fleet_structure_wings(character_id, fleet_id, wings, existing_wing_ids):
    """
    Create/rename wings+squads for a fleet structure template and clear the old
    wings, all via ESI. Runs off the request-response cycle: a full template
    (many wings x squads) is dozens of sequential ESI round-trips, easily long
    enough to outrun a web worker's request timeout if done inline.

    Re-derives its own ESI token from character_id rather than having the view
    pass a bearer token through the task queue/broker.
    """
    from esi.models import Token

    token = Token.get_token(character_id, FLEET_SCOPES)
    if not token:
        logger.warning(
            "Fleet Hub: apply_fleet_structure_wings — no fleet token for character %s",
            character_id,
        )
        return
    try:
        access_token = token.valid_access_token()
    except Exception:
        logger.warning(
            "Fleet Hub: apply_fleet_structure_wings — token refresh failed for character %s",
            character_id,
            exc_info=True,
        )
        return

    headers = {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json",
    }

    created_wings = 0
    created_squads = 0
    new_wing_ids = []
    for wing_data in wings:
        r = esi_requests.post(
            f"{ESI_BASE}/fleets/{fleet_id}/wings/", headers=headers, timeout=10
        )
        if r.status_code not in (200, 201):
            logger.warning(
                "Fleet Hub: ESI wing creation failed — %s %s", r.status_code, r.text
            )
            # Clean up any wings this attempt already created so a partial
            # failure doesn't leave orphaned empty wings in the live fleet.
            for wid in new_wing_ids:
                esi_requests.delete(
                    f"{ESI_BASE}/fleets/{fleet_id}/wings/{wid}/",
                    headers=headers,
                    timeout=10,
                )
            return
        wing_id = r.json()["wing_id"]
        new_wing_ids.append(wing_id)
        rename = esi_requests.put(
            f"{ESI_BASE}/fleets/{fleet_id}/wings/{wing_id}/",
            headers=headers,
            json={"name": wing_data.get("name", "Wing")},
            timeout=10,
        )
        if rename.status_code not in (200, 204):
            logger.warning(
                "Fleet Hub: ESI wing rename failed — %s %s",
                rename.status_code,
                rename.text,
            )
        else:
            created_wings += 1

        for squad_name in wing_data.get("squads", []):
            r = esi_requests.post(
                f"{ESI_BASE}/fleets/{fleet_id}/wings/{wing_id}/squads/",
                headers=headers,
                timeout=10,
            )
            if r.status_code not in (200, 201):
                logger.warning(
                    "Fleet Hub: ESI squad creation failed — %s %s",
                    r.status_code,
                    r.text,
                )
                continue
            squad_id = r.json()["squad_id"]
            rename = esi_requests.put(
                f"{ESI_BASE}/fleets/{fleet_id}/squads/{squad_id}/",
                headers=headers,
                json={"name": squad_name},
                timeout=10,
            )
            if rename.status_code not in (200, 204):
                logger.warning(
                    "Fleet Hub: ESI squad rename failed — %s %s",
                    rename.status_code,
                    rename.text,
                )
                continue
            created_squads += 1

    # Clear old wings now that new structure exists (DELETE also removes their squads)
    for old_wing_id in existing_wing_ids:
        esi_requests.delete(
            f"{ESI_BASE}/fleets/{fleet_id}/wings/{old_wing_id}/",
            headers=headers,
            timeout=10,
        )

    logger.info(
        "Fleet Hub: apply_fleet_structure_wings done for character %s — %d wing(s), %d squad(s)",
        character_id,
        created_wings,
        created_squads,
    )
