import logging
import re
from collections import Counter

import requests as esi_requests

from django.core.cache import cache
from django.db.models import Max

from memberaudit.models import (
    Character,
    CharacterAsset,
    CharacterSkill,
    CharacterSkillqueueEntry,
)
from eveuniverse.models import EveMarketGroup, EveType

from .models import Doctrine, FitTier

logger = logging.getLogger(__name__)


TIER_BADGE_READY = "ready"
TIER_BADGE_CLOSE = "close"
TIER_BADGE_TRAINING = "training"
TIER_BADGE_FAR = "far"


def get_character_skill_data(character_id: int) -> tuple:
    """Returns (skills, queued, is_registered) dicts for the character using a single Character lookup."""
    try:
        char = Character.objects.get(eve_character__character_id=character_id)
    except Character.DoesNotExist:
        return {}, {}, False
    skills = dict(
        CharacterSkill.objects.filter(character=char).values_list(
            "eve_type_id", "trained_skill_level"
        )
    )
    queued = dict(
        CharacterSkillqueueEntry.objects.filter(character=char)
        .values("eve_type_id")
        .annotate(max_level=Max("finished_level"))
        .values_list("eve_type_id", "max_level")
    )
    return skills, queued, True


def get_character_skills(character_id: int) -> dict:
    return get_character_skill_data(character_id)[0]


def get_owned_ships(character_id: int) -> dict | None:
    """
    Return {eve_type_id: [formatted_location, ...]} for all ships owned by this character.
    Returns None if the character is not registered with Member Audit.
    Locations with multiple hulls show a count, e.g. "Jita IV-4 (×3)".
    """
    try:
        char = Character.objects.get(eve_character__character_id=character_id)
    except Character.DoesNotExist:
        return None
    assets = (
        CharacterAsset.objects.filter(
            character=char,
            eve_type__eve_group__eve_category_id=6,
        )
        .select_related("location")
        .values_list("eve_type_id", "location__name")
    )
    counts: dict = {}
    for type_id, loc_name in assets:
        label = loc_name or "Unknown location"
        per_type = counts.setdefault(type_id, {})
        per_type[label] = per_type.get(label, 0) + 1
    result: dict = {}
    for type_id, locs in counts.items():
        result[type_id] = [
            f"{name} (×{count})" if count > 1 else name for name, count in locs.items()
        ]
    return result


SHIP_SEARCH_CACHE_TTL = 86400
SHIP_SEARCH_MISS_TTL = 15 * 60
SHIP_SEARCH_RETRY_TTL = 6 * 3600


def _ship_search_key(type_id: int) -> str:
    return f"aa_fleet_hub:ship_search:{type_id}"


def ship_search_info(type_ids) -> dict:
    """
    Return {type_id: (ship_name, ship_class)} for doctrine search.
    ship_class is the top market group under "Ships" (e.g. "Cruisers",
    "Capital Ships"), or "" if the type has no market group. Classed hulls
    are cached for a day. Hulls without a class are cached for 15 minutes
    while their market groups are fetched from ESI in the background, so
    the class appears on a later page load.
    """
    type_ids = {tid for tid in type_ids if tid and tid > 0}
    cached = cache.get_many([_ship_search_key(tid) for tid in type_ids])
    info = {
        tid: cached[_ship_search_key(tid)]
        for tid in type_ids
        if _ship_search_key(tid) in cached
    }
    missing = type_ids - info.keys()
    if missing:
        fresh = {tid: ("", "") for tid in missing}
        fresh.update(_ship_search_lookup(missing))
        info.update(fresh)
        classed = {tid: v for tid, v in fresh.items() if v[1]}
        cache.set_many(
            {_ship_search_key(tid): v for tid, v in classed.items()},
            SHIP_SEARCH_CACHE_TTL,
        )
        cache.set_many(
            {
                _ship_search_key(tid): v
                for tid, v in fresh.items()
                if tid not in classed
            },
            SHIP_SEARCH_MISS_TTL,
        )
        _queue_market_group_load(missing - classed.keys())
    return info


def _queue_market_group_load(type_ids) -> None:
    """Queue one background load for hulls not tried in the last 6 hours."""
    from .tasks import load_ship_market_groups

    keys = {tid: f"aa_fleet_hub:ship_search_load:{tid}" for tid in type_ids}
    due = sorted(
        tid for tid, key in keys.items() if cache.add(key, 1, SHIP_SEARCH_RETRY_TTL)
    )
    if not due:
        return
    try:
        load_ship_market_groups.delay(due)
    except Exception:
        # Broker down: release the locks so the next page view tries again.
        cache.delete_many([keys[tid] for tid in due])
        logger.warning(
            "Could not queue market group load for types %s", due, exc_info=True
        )


def _ship_search_lookup(type_ids) -> dict:
    """
    Read hull names and classes from the database. Parents are walked one
    level per query, so the query count follows tree depth, not the number
    of hulls.
    """
    types = EveType.objects.filter(id__in=set(type_ids)).values_list(
        "id", "name", "eve_market_group_id"
    )
    parents = {}  # market group id -> (name, parent id)
    pending = {mg for _, _, mg in types if mg}
    while pending:
        rows = EveMarketGroup.objects.filter(id__in=pending).values_list(
            "id", "name", "parent_market_group_id"
        )
        pending = set()
        for mg_id, name, parent_id in rows:
            parents[mg_id] = (name, parent_id)
            if parent_id and parent_id not in parents:
                pending.add(parent_id)

    def top_class(mg_id):
        # The class is the node just below the root ("Ships").
        child = None
        while mg_id in parents:
            name, parent_id = parents[mg_id]
            if not parent_id:
                return child or ""
            child, mg_id = name, parent_id
        return ""

    return {tid: (name, top_class(mg)) for tid, name, mg in types}


def role_key(role: str) -> str:
    """Fit roles are free text; "Logi", "logi " and "LOGI" share one key."""
    return " ".join((role or "").split()).casefold()


def role_labels(roles) -> dict:
    """
    Return {role_key: label} for the doctrine search Role facet, sorted by
    label. The label is the most common spelling of that key (first seen on
    a tie). Blank roles are left out.
    """
    spellings = {}
    for role in roles:
        key = role_key(role)
        if key:
            spellings.setdefault(key, Counter())[" ".join(role.split())] += 1
    labels = {key: c.most_common(1)[0][0] for key, c in spellings.items()}
    return dict(sorted(labels.items(), key=lambda kv: kv[1].lower()))


def pip_states(
    skill_type_id: int, required_level: int, skills: dict, queued: dict
) -> list:
    """
    Returns a list of 5 pip state strings for levels I–V.
    States: 'have', 'exceed', 'short', 'unmet', 'queued', 'neutral'
    The pip at required_level gets an additional 'target' class to mark the goal.
    """
    trained = skills.get(skill_type_id, 0)
    queued_max = queued.get(skill_type_id, 0)
    pips = []
    for i in range(5):
        level = i + 1
        if trained >= level:
            state = "have" if level <= required_level else "exceed"
        elif level > required_level:
            state = "queued" if queued_max and level <= queued_max else "neutral"
        elif queued_max and level <= queued_max:
            state = "queued"
        elif level == required_level:
            state = "short"
        else:
            state = "unmet"
        if level == required_level:
            state += " target"
        pips.append(state)
    return pips


def missing_skills_for_tier(tier: FitTier, skills: dict) -> list:
    """Return list of (skill_type_id, skill_name, required_level) for skills the pilot is short on."""
    missing = []
    for ts in tier.skills.all():
        trained = skills.get(ts.skill_type_id, 0)
        if trained < ts.required_level:
            missing.append((ts.skill_type_id, ts.skill_name(), ts.required_level))
    return missing


ROMAN = ["", "I", "II", "III", "IV", "V"]


def tier_badge(missing_count: int) -> str:
    """Return the CSS badge class name for a given number of missing skills."""
    from .models import FleetHubSettings

    settings = FleetHubSettings.get()
    if missing_count == 0:
        return TIER_BADGE_READY
    elif missing_count <= settings.tier_close_max:
        return TIER_BADGE_CLOSE
    elif missing_count <= settings.tier_training_max:
        return TIER_BADGE_TRAINING
    else:
        return TIER_BADGE_FAR


def readiness_for_fit(
    fit, skills: dict, queued: dict, eve_names: dict | None = None
) -> dict:
    """
    Compute readiness data for a single fit against a character's skill set.

    Returns a dict with keys:
      tiers        — list of per-tier data dicts (skill rows, badge, missing count)
      best_tier_index — index of highest tier fully met, or None
      highlight    — 'none' | 'recommended' | 'all' (for the fit card's left border colour)
      best_tier_label — display label of best tier met
      status_text  — human-readable summary (used in panel footer)
      status_class — Bootstrap text-* class for status_text colour
      total_missing — unique skill count short across all tiers

    `eve_names` may be passed in as a pre-fetched {type_id: EveType} dict to
    avoid an EveType query per fit when rendering many fits on one page; if
    omitted, it's looked up for just this fit's tier skills.
    """
    if eve_names is None:
        all_type_ids = {
            ts.skill_type_id for tier in fit.tiers.all() for ts in tier.skills.all()
        }
        eve_names = (
            EveType.objects.filter(id__in=all_type_ids).in_bulk()
            if all_type_ids
            else {}
        )

    tiers_data = []
    for tier in fit.tiers.all():
        skill_rows = []
        missing_count = 0
        for ts in tier.skills.all():
            trained = skills.get(ts.skill_type_id, 0)
            queued_max = queued.get(ts.skill_type_id, 0)
            is_queued = queued_max > trained
            met = trained >= ts.required_level
            close = not met and (trained >= ts.required_level - 1 or is_queued)
            sg_class = "met" if met else ("close" if close else "unmet")
            eve_type = eve_names.get(ts.skill_type_id)
            skill_name = eve_type.name if eve_type else f"Skill #{ts.skill_type_id}"
            tooltip = f"{skill_name} — have {ROMAN[min(trained, 5)]}, need {ROMAN[min(ts.required_level, 5)]}"
            if is_queued:
                tooltip += f" — queued to {ROMAN[min(queued_max, 5)]}"
            if not met:
                missing_count += 1
            skill_rows.append(
                {
                    "skill_type_id": ts.skill_type_id,
                    "skill_name": skill_name,
                    "trained": trained,
                    "required_level": ts.required_level,
                    "pips": pip_states(
                        ts.skill_type_id, ts.required_level, skills, queued
                    ),
                    "sg_class": sg_class,
                    "tooltip": tooltip,
                }
            )
        has_skills = len(skill_rows) > 0
        tiers_data.append(
            {
                "tier": tier,
                "missing_count": missing_count,
                # A tier with no skills configured has nothing to evaluate — treat it
                # as not-ready (far) rather than "0 missing" reading as complete/green.
                "badge": tier_badge(missing_count) if has_skills else TIER_BADGE_FAR,
                "skill_rows": skill_rows,
                "has_skills": has_skills,
            }
        )

    tier_count = len(tiers_data)
    rec_index = (
        tier_count - 1
        if tier_count == 1
        else (tier_count // 2 if tier_count > 2 else 0)
    )

    best_index = None
    for i, td in enumerate(tiers_data):
        if td["missing_count"] == 0 and td["has_skills"]:
            best_index = i

    if best_index is None:
        highlight = "none"
    elif best_index >= tier_count - 1:
        highlight = "all"
    elif best_index >= rec_index:
        highlight = "recommended"
    else:
        highlight = "min"

    # Summary stats for panel footer
    best_tier_label = (
        tiers_data[best_index]["tier"].label if best_index is not None else "None yet"
    )
    all_met = best_index is not None and best_index >= tier_count - 1

    unique_missing = set()
    for td in tiers_data:
        for row in td["skill_rows"]:
            if row["sg_class"] != "met":
                unique_missing.add(row["skill_type_id"])
    total_missing = len(unique_missing)

    if all_met:
        status_text = "All tiers complete"
        status_class = "text-success"
    elif best_index is not None:
        status_text = (
            f"{total_missing} skill{'s' if total_missing != 1 else ''} to improve"
        )
        status_class = "text-warning"
    elif tiers_data and tiers_data[0]["missing_count"] <= 2:
        status_text = f"Min: {tiers_data[0]['missing_count']} skill{'s' if tiers_data[0]['missing_count'] != 1 else ''} missing"
        status_class = "text-warning"
    else:
        status_text = "Training needed"
        status_class = "text-danger"

    return {
        "tiers": tiers_data,
        "best_tier_index": best_index,
        "highlight": highlight,
        "best_tier_label": best_tier_label,
        "status_text": status_text,
        "status_class": status_class,
        "total_missing": total_missing,
    }


def visible_doctrines(user):
    """
    Return queryset of active, non-archived doctrines visible to the given user.

    Public doctrines are visible to all members. Group-gated doctrines are visible
    only to members of the assigned groups. Draft doctrines are hidden except from
    officers (manage_doctrines permission).
    """
    from django.db.models import Q

    qs = Doctrine.objects.filter(active=True, archived=False)
    if user.has_perm("aa_fleet_hub.manage_doctrines"):
        return qs.prefetch_related(
            "fits__tiers__skills", "shared_skills", "fleet_structures", "motd_groups"
        )
    user_groups = user.groups.all()
    return (
        qs.filter(is_draft=False)
        .filter(Q(public=True) | Q(groups__in=user_groups))
        .distinct()
        .prefetch_related(
            "fits__tiers__skills", "shared_skills", "fleet_structures", "motd_groups"
        )
    )


def _parse_eft(eft_text: str):
    """
    Split EFT text into (ship_name, fit_name, slot_sections, qty_lines).
    slot_sections is a list of lists — one per blank-line-separated slot section
    (high/med/low/rig order). qty_lines are the xN drone/cargo lines.
    Module+ammo entries like 'Laser II, Conflagration M' are stripped to the
    module name only.
    """
    lines = eft_text.strip().splitlines()
    if not lines:
        return None, None, [], []
    m = re.match(r"^\[(.+?),\s*(.+)\]$", lines[0].strip())
    ship_name = m.group(1).strip() if m else ""
    fit_name = m.group(2).strip() if m else ""

    # Split body into groups separated by blank lines
    groups, current = [], []
    for line in lines[1:]:
        stripped = line.strip()
        if not stripped:
            if current:
                groups.append(current)
                current = []
        else:
            current.append(stripped)
    if current:
        groups.append(current)

    slot_sections, qty_lines = [], []
    for group in groups:
        # A qty group has items matching "Name xN" — drones, cargo, implants
        if all(re.match(r"^.+\s+x\d+$", ln) for ln in group):
            qty_lines.extend(group)
        else:
            # Strip ammo suffix ("Module, Ammo") and skip empty-slot lines
            section = []
            for ln in group:
                if re.match(r"^\[empty\s", ln, re.IGNORECASE):
                    continue
                name = re.split(r"\s*,\s*", ln)[0].strip()
                if name:
                    section.append(name)
            if section:
                slot_sections.append(section)

    return ship_name, fit_name, slot_sections, qty_lines


def ensure_eft_ship_name(eft_text: str, ship_name: str) -> str:
    """
    Repair an EFT header that is missing the ship name, e.g.
    '[Bellicose Armor v1.0]' -> '[Bellicose, Bellicose Armor v1.0]'.
    EVE and _parse_eft both require '[Ship, Fit Name]'. Headers that already
    have a ship part, or text with no bracketed header, are returned unchanged.
    """
    if not eft_text or not ship_name:
        return eft_text
    lines = eft_text.split("\n")
    m = re.match(r"^\[([^\],]+)\]$", lines[0].strip())
    if not m:
        return eft_text
    # Keep the header's own line ending, so a CRLF fit stays all CRLF.
    cr = "\r" if lines[0].endswith("\r") and len(lines) > 1 else ""
    lines[0] = f"[{ship_name}, {m.group(1).strip()}]{cr}"
    return "\n".join(lines)


EFT_HEADER_RE = re.compile(r"^\[[^\],]+,[^\]]+\]$")


def eft_header_error(eft_text: str, ship_name: str):
    """
    Return a user-facing error if the EFT header is still unusable after
    ensure_eft_ship_name, else None. Catches a header that isn't
    '[Ship, Fit Name]' and a second header line (typed header plus a pasted
    export that brings its own), which EVE rejects and _parse_eft misreads
    as a module.
    """
    if not eft_text:
        return None
    lines = ensure_eft_ship_name(eft_text.strip(), ship_name).splitlines()
    if not EFT_HEADER_RE.match(lines[0].strip()):
        return "EFT must start with a header like [Ship, Fit Name]."
    if any(EFT_HEADER_RE.match(ln.strip()) for ln in lines[1:]):
        return "EFT has more than one [Ship, Fit Name] header line. Keep only one."
    return None


def _resolve_type_names(names: set) -> dict:
    """
    Resolve EVE type names to {name: type_id} via ESI /universe/ids/.
    Falls back to local EveType DB for anything ESI doesn't return.
    Logs a warning if the ESI call fails so the error isn't swallowed silently.
    """
    result = {}
    names = [n for n in names if n]
    if not names:
        return result
    try:
        r = esi_requests.post(
            "https://esi.evetech.net/latest/universe/ids/?datasource=tranquility",
            json=names,
            timeout=10,
        )
        if r.status_code == 200:
            for item in r.json().get("inventory_types", []):
                result[item["name"]] = item["id"]
        else:
            logger.warning("Fleet Hub: ESI /universe/ids/ returned %s", r.status_code)
    except Exception:
        logger.warning("Fleet Hub: ESI /universe/ids/ request failed", exc_info=True)
    # Fill any gaps from the local DB
    missing = [n for n in names if n not in result]
    if missing:
        for t in EveType.objects.filter(name__in=missing):
            result[t.name] = t.id
    return result


def parse_eft_for_esi(eft_text: str) -> tuple:
    """
    Parse EFT and return (ship_type_id, fit_name, esi_items, warnings).
    Type IDs are resolved via ESI /universe/ids/ so no local module catalog
    is needed. EFT slot sections (hi/med/lo/rig/sub) map to the corresponding
    ESI flag families positionally. Drones get DroneBay; other xN items Cargo.
    """
    # ESI flag prefix per EFT slot section, in standard EFT order
    SLOT_FLAGS = ["LoSlot", "MedSlot", "HiSlot", "RigSlot", "SubSystemSlot"]
    SLOT_MAX = [8, 5, 8, 3, 5]

    ship_name, fit_name, slot_sections, qty_lines = _parse_eft(eft_text)
    all_names = set()
    for section in slot_sections:
        all_names.update(section)
    for line in qty_lines:
        qm = re.match(r"^(.+?)\s+x\d+$", line)
        if qm:
            all_names.add(qm.group(1).strip())
    if ship_name:
        all_names.add(ship_name)

    type_map = _resolve_type_names(all_names)
    warnings, items = [], []

    ship_type_id = type_map.get(ship_name)
    if not ship_type_id:
        return None, fit_name, [], [f"Ship '{ship_name}' not found."]

    for sec_idx, section in enumerate(slot_sections):
        flag_prefix = SLOT_FLAGS[sec_idx] if sec_idx < len(SLOT_FLAGS) else "Cargo"
        max_slots = SLOT_MAX[sec_idx] if sec_idx < len(SLOT_MAX) else 8
        slot_num = 0
        for name in section:
            tid = type_map.get(name)
            if not tid:
                warnings.append(f"'{name}' not found — skipped.")
                continue
            items.append(
                {
                    "type_id": tid,
                    "quantity": 1,
                    "flag": f"{flag_prefix}{min(slot_num, max_slots - 1)}",
                }
            )
            slot_num += 1

    # Resolve drone vs cargo in one batched query
    qty_parsed = []
    qty_type_ids = set()
    for line in qty_lines:
        qm = re.match(r"^(.+?)\s+x(\d+)$", line)
        if not qm:
            continue
        name, qty = qm.group(1).strip(), int(qm.group(2))
        tid = type_map.get(name)
        if not tid:
            warnings.append(f"'{name}' not found — skipped.")
            continue
        qty_parsed.append((tid, qty))
        qty_type_ids.add(tid)

    drone_type_ids = {
        t.pk
        for t in EveType.objects.filter(pk__in=qty_type_ids).select_related("eve_group")
        if getattr(getattr(t, "eve_group", None), "eve_category_id", None) == 18
    }
    for tid, qty in qty_parsed:
        flag = "DroneBay" if tid in drone_type_ids else "Cargo"
        items.append({"type_id": tid, "quantity": qty, "flag": flag})

    return ship_type_id, fit_name, items, warnings


def format_skill_export(missing: list) -> str:
    """Format a list of missing skills as plain text for EVESkillboard / SkillQ import."""
    lines = [f"{name} {ROMAN[level]}" for _, name, level in missing]
    return "\n".join(lines)
