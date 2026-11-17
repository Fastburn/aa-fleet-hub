"""Tests for Fleet Command row filters and pilot counts."""

from aa_fleet_hub.tasks import count_pilots_by_tier, filter_readiness_rows, pilot_key


def _char(name, bucket="max", user_id=1, is_main=True):
    return {
        "character_name": name,
        "bucket": bucket,
        "user_id": user_id,
        "is_main": is_main,
    }


# One pilot (user 1) with a main who can't fly and two alts (one can, one
# can't), a main who can fly (user 2), and an unowned character who can't.
ROWS = [
    _char("Main can't fly", bucket="none", user_id=1),
    _char("Alt can fly", user_id=1, is_main=False),
    _char("Alt can't fly", bucket="none", user_id=1, is_main=False),
    _char("Main can fly", user_id=2),
    _char("Unowned can't fly", bucket="none", user_id=None, is_main=False),
]


def _doctrine(rows=ROWS, no_skill_plan=False):
    return {
        "name": "Ferox",
        "total": len(rows),
        "fits": [{"fit_id": 1, "no_skill_plan": no_skill_plan, "per_char": rows}],
    }


def _names(data):
    return [c["character_name"] for c in data["fits"][0]["per_char"]]


def test_no_filters_shows_everyone():
    fit = filter_readiness_rows(_doctrine(), False, False)["fits"][0]
    assert _names({"fits": [fit]}) == [c["character_name"] for c in ROWS]
    assert (fit["hidden_alts"], fit["hidden_cant_fly"]) == (0, 0)


def test_hiding_alts_never_hides_mains_or_unowned_characters():
    data = filter_readiness_rows(_doctrine(), True, False)
    assert _names(data) == ["Main can't fly", "Main can fly", "Unowned can't fly"]
    assert data["fits"][0]["hidden_alts"] == 2


def test_hiding_cant_fly_keeps_everyone_who_can():
    data = filter_readiness_rows(_doctrine(), False, True)
    assert _names(data) == ["Alt can fly", "Main can fly"]
    assert data["fits"][0]["hidden_cant_fly"] == 3


def test_both_filters_count_what_each_toggle_would_bring_back():
    data = filter_readiness_rows(_doctrine(), True, True)
    fit = data["fits"][0]
    assert _names(data) == ["Main can fly"]
    # "Show alts" alone brings back only the alt who can fly; "Show who can't
    # fly" alone brings back the two non-alts who can't. The alt who can't fly
    # needs both, so it counts toward neither.
    assert fit["hidden_alts"] == 1
    assert fit["hidden_cant_fly"] == 2


def test_cant_fly_filter_skips_fits_with_no_skill_plan():
    rows = [_char("A", bucket="none"), _char("B", bucket="none", user_id=2)]
    data = filter_readiness_rows(_doctrine(rows, no_skill_plan=True), False, True)
    assert _names(data) == ["A", "B"]
    assert data["fits"][0]["hidden_cant_fly"] == 0


def test_cached_data_is_not_modified():
    doctrine = _doctrine()
    filter_readiness_rows(doctrine, True, True)
    assert doctrine["fits"][0]["per_char"] is ROWS
    assert "hidden_alts" not in doctrine["fits"][0]


# ---------------------------------------------------------------------------
# Pilot counts
# ---------------------------------------------------------------------------

TIERS = ["Min", "Rec", "Max"]


def test_main_and_alt_count_once_at_the_best_tier():
    rows = [(pilot_key(7, 1), 0), (pilot_key(7, 2), 2), (pilot_key(7, 3), None)]
    counts = count_pilots_by_tier(rows, TIERS)
    assert counts == {"Min": 0, "Rec": 0, "Max": 1, "none": 0}


def test_unowned_characters_are_separate_pilots():
    # pk 7 must not collide with user id 7 either.
    rows = [
        (pilot_key(None, 7), None),
        (pilot_key(None, 8), None),
        (pilot_key(7, 1), 1),
    ]
    counts = count_pilots_by_tier(rows, TIERS)
    assert counts == {"Min": 0, "Rec": 1, "Max": 0, "none": 2}


def test_pilot_counts_add_up_to_the_number_of_pilots():
    owners = [1, 1, 1, 2, 2, None, None, 3]
    bests = [None, 0, 1, 2, None, 0, None, None]
    rows = [
        (pilot_key(uid, pk), best)
        for pk, (uid, best) in enumerate(zip(owners, bests, strict=True))
    ]
    counts = count_pilots_by_tier(rows, TIERS)
    assert sum(counts.values()) == len({key for key, _ in rows}) == 5
    assert counts == {"Min": 1, "Rec": 1, "Max": 1, "none": 2}


def test_filter_forms_post_the_state_they_want():
    # A toggle that flips the stored value undoes itself on a double-click or
    # a stale tab, so every filter form must say which state it's asking for.
    import re
    from pathlib import Path

    import aa_fleet_hub

    root = Path(aa_fleet_hub.__file__).parent
    template = root / "templates/aa_fleet_hub/fleet_command/doctrine.html"
    forms = re.findall(
        r"<form[^>]*readiness_filter_toggle.*?</form>",
        template.read_text(),
        re.DOTALL,
    )
    assert len(forms) == 4
    assert all('name="hide"' in form for form in forms)
