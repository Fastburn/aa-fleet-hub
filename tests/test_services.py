"""Tests for pure/logic functions in services.py (no DB or ESI calls required)."""

from aa_fleet_hub.services import (
    ROMAN,
    _parse_eft,
    eft_header_error,
    ensure_eft_ship_name,
    pip_states,
)

# ---------------------------------------------------------------------------
# pip_states
# ---------------------------------------------------------------------------


def test_pip_states_fully_trained():
    # Trained to required level — all pips up to required are 'have', rest 'neutral'
    result = pip_states(1, 3, {1: 3}, {})
    assert result[0] == "have"
    assert result[1] == "have"
    assert result[2] == "have target"
    assert result[3] == "neutral"
    assert result[4] == "neutral"


def test_pip_states_untrained():
    result = pip_states(1, 3, {}, {})
    assert result[0] == "unmet"
    assert result[1] == "unmet"
    assert result[2] == "short target"
    assert result[3] == "neutral"
    assert result[4] == "neutral"


def test_pip_states_exceed():
    # Trained beyond required — excess pips are 'exceed'
    result = pip_states(1, 2, {1: 4}, {})
    assert result[0] == "have"
    assert result[1] == "have target"
    assert result[2] == "exceed"
    assert result[3] == "exceed"
    assert result[4] == "neutral"


def test_pip_states_queued_before_short():
    # Skill queued past required level — pip at required_level is queued, not short
    result = pip_states(1, 3, {1: 0}, {1: 4})
    assert result[2] == "queued target"


def test_pip_states_queued_excess():
    # Queued beyond required — excess queued pips are 'queued' not 'neutral'
    result = pip_states(1, 2, {}, {1: 4})
    assert result[3] == "queued"


# ---------------------------------------------------------------------------
# _parse_eft
# ---------------------------------------------------------------------------


def test_parse_eft_basic():
    eft = """[Ferox, My Ferox]
Magnetic Field Stabilizer II
Magnetic Field Stabilizer II

Large Shield Extender II
Large Shield Extender II

Heavy Neutron Blaster II, Antimatter Charge M
Heavy Neutron Blaster II, Antimatter Charge M

Medium Core Defense Field Extender I
Medium Core Defense Field Extender I
Medium Core Defense Field Extender I

Hammerhead II x5
Hobgoblin II x5
"""
    ship, name, sections, qty = _parse_eft(eft)
    assert ship == "Ferox"
    assert name == "My Ferox"
    # Ammo stripped from blaster lines
    assert all("Antimatter" not in m for section in sections for m in section)
    # Drone lines go to qty
    assert any("Hammerhead II x5" in q for q in qty)


def test_parse_eft_empty_slots_skipped():
    eft = """[Ferox, Test]
[empty high slot]
Heavy Neutron Blaster II
"""
    _, _, sections, _ = _parse_eft(eft)
    flat = [m for s in sections for m in s]
    assert not any("empty" in m.lower() for m in flat)
    assert "Heavy Neutron Blaster II" in flat


def test_parse_eft_empty_string():
    ship, name, sections, qty = _parse_eft("")
    assert ship is None
    assert sections == []
    assert qty == []


# ---------------------------------------------------------------------------
# ROMAN constant bounds (guards against IndexError regression)
# ---------------------------------------------------------------------------


def test_roman_levels_1_to_5():
    assert ROMAN[1] == "I"
    assert ROMAN[5] == "V"


# ---------------------------------------------------------------------------
# ensure_eft_ship_name
# ---------------------------------------------------------------------------


def test_ensure_eft_ship_name_adds_missing_ship():
    eft = "[Bellicose Armor v1.0]\nDamage Control II"
    fixed = ensure_eft_ship_name(eft, "Bellicose")
    assert fixed == "[Bellicose, Bellicose Armor v1.0]\nDamage Control II"
    ship, name, _, _ = _parse_eft(fixed)
    assert ship == "Bellicose"
    assert name == "Bellicose Armor v1.0"


def test_ensure_eft_ship_name_keeps_crlf():
    eft = "[Logi Wing]\r\nLarge Remote Armor Repairer II\r\n"
    fixed = ensure_eft_ship_name(eft, "Guardian")
    assert fixed == "[Guardian, Logi Wing]\r\nLarge Remote Armor Repairer II\r\n"


def test_ensure_eft_ship_name_leaves_valid_header():
    eft = "[Bellicose, Bellicose Armor v1.0]\nDamage Control II"
    assert ensure_eft_ship_name(eft, "Bellicose") == eft


def test_ensure_eft_ship_name_ignores_non_header():
    eft = "Damage Control II\nDamage Control II"
    assert ensure_eft_ship_name(eft, "Bellicose") == eft


# ---------------------------------------------------------------------------
# eft_header_error
# ---------------------------------------------------------------------------


def test_eft_header_error_accepts_valid_and_repairable():
    assert (
        eft_header_error("[Bellicose, Armor Fit]\nDamage Control II", "Bellicose")
        is None
    )
    assert eft_header_error("[Armor Fit]\nDamage Control II", "Bellicose") is None
    # Empty-slot placeholders are bracketed but aren't headers
    assert eft_header_error("[Bellicose, X]\n[Empty High slot]", "Bellicose") is None
    assert eft_header_error("", "Bellicose") is None


def test_eft_header_error_rejects_duplicate_header():
    eft = "[Bellicose, Armor Fit]\n[Bellicose, Armor Fit]\nDamage Control II"
    assert "more than one" in eft_header_error(eft, "Bellicose")
    # Typed header without ship, then a pasted export with its own header
    eft = "[Armor Fit]\n[Bellicose, Bellicose fit]\nDamage Control II"
    assert "more than one" in eft_header_error(eft, "Bellicose")


def test_eft_header_error_rejects_missing_header():
    assert "must start" in eft_header_error("Damage Control II", "Bellicose")
    # Ship unknown, so the header can't be repaired
    assert "must start" in eft_header_error("[Armor Fit]\nDamage Control II", "")
