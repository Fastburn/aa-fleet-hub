"""Tests for custom doctrine tag colours."""

import importlib
import inspect
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from django.core.exceptions import ValidationError
from django.http import QueryDict
from django.template import Context, Engine

from aa_fleet_hub import views
from aa_fleet_hub.models import TAG_COLOR_VALIDATOR, Doctrine, _relative_luminance

migration = importlib.import_module(
    "aa_fleet_hub.migrations.0024_doctrine_tag_color_hex"
)
TEMPLATES = Path(views.__file__).parent / "templates"


def _doctrine(tag_color, tag="Mainline"):
    d = SimpleNamespace(tag=tag, tag_color=tag_color)
    d.DEFAULT_TAG_COLOR = Doctrine.DEFAULT_TAG_COLOR
    d.TAG_COLOR_SWATCHES = Doctrine.TAG_COLOR_SWATCHES
    d.tag_color_hex = Doctrine.tag_color_hex.fget(d)
    d.tag_text_color = Doctrine.tag_text_color.fget(d)
    return d


def _contrast(a, b):
    la, lb = sorted((_relative_luminance(a), _relative_luminance(b)), reverse=True)
    return (la + 0.05) / (lb + 0.05)


@pytest.mark.parametrize(
    "color, luminance",
    [("#ffffff", 1.0), ("#000000", 0.0), ("#808080", 0.2159), ("#ff0000", 0.2126)],
)
def test_relative_luminance_matches_known_values(color, luminance):
    # Fixed WCAG values, so a wrong formula can't pass by agreeing with itself.
    assert _relative_luminance(color) == pytest.approx(luminance, abs=1e-4)


@pytest.mark.parametrize(
    "color, text",
    [
        ("#ffff00", "#000000"),
        ("#0000ff", "#ffffff"),
        ("#767676", "#000000"),  # close call: black 4.62:1 beats white 4.54:1
        ("#e05252", "#000000"),
        ("#1b2a6b", "#ffffff"),
        ("#f5e663", "#000000"),
    ],
)
def test_tag_text_color_known_answers(color, text):
    assert _doctrine(color).tag_text_color == text


@pytest.mark.parametrize(
    "color",
    ["#000000", "#ffffff", "#ffff00", "#0000ff", "#777777", "#767676", "#808080"]
    + [hex_ for hex_, _ in Doctrine.TAG_COLOR_SWATCHES],
)
def test_tag_text_meets_four_point_five_to_one(color):
    doctrine = _doctrine(color)
    assert doctrine.tag_text_color in ("#000000", "#ffffff")
    assert _contrast(color, doctrine.tag_text_color) >= 4.5


def test_tag_text_meets_contrast_on_every_grey():
    # The hardest backgrounds sit around mid grey, where black and white are
    # closest; checking every grey covers that crossover exactly.
    for v in range(256):
        color = f"#{v:02x}{v:02x}{v:02x}"
        assert _contrast(color, _doctrine(color).tag_text_color) >= 4.5, color


@pytest.mark.parametrize(
    "stored",
    ["", None, "orange", "#fff", "#12345g", "red;background:url(x)", "#1234567"],
)
def test_invalid_stored_colour_renders_as_the_default(stored):
    assert _doctrine(stored).tag_color_hex == Doctrine.DEFAULT_TAG_COLOR


@pytest.mark.parametrize(
    "stored, shown",
    [
        ("green", "#4caf6b"),
        ("blue", "#4d9de0"),
        ("Gold", "#c8a84b"),
        ("red", "#e05252"),
        ("purple", "#9b59b6"),
    ],
)
def test_colour_name_saved_on_a_rollback_shows_its_swatch(stored, shown):
    # 0.1.1 stores names; a doctrine saved while rolled back keeps its colour.
    assert _doctrine(stored).tag_color_hex == shown
    assert migration.NAME_TO_HEX[stored.lower()] == shown


def test_stored_colour_is_lowercased_for_display():
    assert _doctrine("#3A7BD5").tag_color_hex == "#3a7bd5"


@pytest.mark.parametrize("value", ["#3a7bd5", "#3A7BD5", "#000000"])
def test_validator_accepts_hex(value):
    TAG_COLOR_VALIDATOR(value)


@pytest.mark.parametrize(
    "value", ["green", "#fff", "3a7bd5", "#3a7bd5 ", "#3a7bd5\n", "#3a7bdz", ""]
)
def test_validator_rejects_everything_else(value):
    with pytest.raises(ValidationError):
        TAG_COLOR_VALIDATOR(value)


def _post(data):
    request = MagicMock()
    request.POST = QueryDict(data)
    return request


@pytest.mark.parametrize(
    "posted",
    ["green", "#fff", "%233a7bdz", "red%3Bbackground%3Aurl(x)", "%233a7bd5ff"],
)
def test_form_rejects_invalid_colour_server_side(posted):
    error = views._doctrine_form_error(_post(f"name=Ferox+Fleet&tag_color={posted}"))
    assert error == "Tag colour must be a hex code like #3a7bd5."


def test_form_requires_a_name_first():
    error = views._doctrine_form_error(_post("name=&tag_color=green"))
    assert error == "Doctrine name is required."


@pytest.mark.parametrize(
    "posted, saved",
    [("%233A7BD5", "#3a7bd5"), ("%23e05252", "#e05252"), ("", "#4caf6b")],
)
def test_save_stores_lowercase_hex(posted, saved):
    request = _post(f"name=Ferox+Fleet&tag_color={posted}")
    assert views._doctrine_form_error(request) is None
    doctrine = MagicMock()
    with patch("aa_fleet_hub.tasks.invalidate_readiness_cache"):
        inspect.unwrap(views._save_doctrine)(request, doctrine)
    assert doctrine.tag_color == saved


@pytest.mark.parametrize(
    "view, args", [("doctrine_create", ()), ("doctrine_edit", (1,))]
)
def test_create_and_edit_show_the_error_and_do_not_save(view, args):
    request = _post("name=Ferox+Fleet&tag_color=nope")
    request.method = "POST"
    with (
        patch.object(views, "get_object_or_404", return_value=MagicMock()),
        patch.object(views, "_doctrine_form_context", return_value={}),
        patch.object(views, "_save_doctrine") as save,
        patch.object(views, "render") as render,
    ):
        inspect.unwrap(getattr(views, view))(request, *args)
    save.assert_not_called()
    assert render.call_args.args[2]["error"].startswith("Tag colour must be")


def test_duplicate_keeps_the_tag_colour():
    src = MagicMock(tag="Signature", tag_color="#3a7bd5")
    request = MagicMock(method="POST")
    with (
        patch.object(views, "get_object_or_404", return_value=src),
        patch.object(views.Doctrine.objects, "create") as create,
        patch.object(views.Motd.objects, "bulk_create"),
        patch.object(views, "_queue_readiness_recompute"),
        patch.object(views, "redirect"),
        patch.object(views, "messages"),
    ):
        inspect.unwrap(views.doctrine_duplicate)(request, 1)
    assert create.call_args.kwargs["tag_color"] == "#3a7bd5"


def test_migration_keeps_existing_colours():
    # The five old names map to the swatches, so no doctrine changes colour.
    assert list(migration.NAME_TO_HEX.values()) == [
        hex_ for hex_, _ in Doctrine.TAG_COLOR_SWATCHES
    ]
    assert migration.NAME_TO_HEX["green"] == Doctrine.DEFAULT_TAG_COLOR


def _run(func, stored):
    """Run a migration function over doctrines with the given tag_color values."""
    rows = [SimpleNamespace(pk=i, tag_color=c) for i, c in enumerate(stored)]
    result = dict(enumerate(stored))
    model = MagicMock()
    model.objects.only.return_value = rows

    def filter_(pk):
        return SimpleNamespace(
            update=lambda tag_color: result.__setitem__(pk, tag_color)
        )

    model.objects.filter.side_effect = filter_
    apps = MagicMock()
    apps.get_model.return_value = model
    func(apps, None)
    return [result[i] for i in range(len(stored))]


def test_migration_converts_names_to_hex():
    assert _run(
        migration.names_to_hex, ["green", "blue", "gold", "red", "purple", ""]
    ) == ["#4caf6b", "#4d9de0", "#c8a84b", "#e05252", "#9b59b6", "#4caf6b"]


def test_migration_rerun_keeps_hex_and_greens_unknown_names():
    # MySQL can't roll back the schema step, so a re-run must not touch hex.
    assert _run(migration.names_to_hex, ["#3a7bd5", "#3A7BD5", "orange", None]) == [
        "#3a7bd5",
        "#3a7bd5",
        "#4caf6b",
        "#4caf6b",
    ]


def test_migration_converts_values_before_narrowing_the_column():
    # The column shrinks from 10 to 7 characters, so names must be hex first.
    run_python, alter_field = migration.Migration.operations
    assert run_python.code is migration.names_to_hex
    assert run_python.reverse_code is migration.hex_to_names
    assert alter_field.field.max_length == 7


def test_migration_reverses_to_names():
    assert _run(
        migration.hex_to_names, ["#4caf6b", "#4D9DE0", "#c8a84b", "#3a7bd5"]
    ) == ["green", "blue", "gold", "green"]


def _render_badge(doctrine):
    engine = Engine(dirs=[str(TEMPLATES)])
    template = engine.get_template("aa_fleet_hub/partials/doctrine_tag.html")
    return template.render(Context({"doctrine": doctrine}))


def test_badge_shows_label_colour_and_text_colour():
    html = _render_badge(_doctrine("#9b59b6", tag="Signature"))
    assert 'style="background:#9b59b6;color:#ffffff"' in html
    assert ">Signature</span>" in html


def test_badge_escapes_the_tag_and_never_uses_a_raw_colour():
    html = _render_badge(_doctrine('red;"><script>', tag="<b>x</b>"))
    assert "<script>" not in html and "<b>" not in html
    assert "background:#4caf6b;" in html


def test_no_badge_without_a_tag():
    assert _render_badge(_doctrine("#9b59b6", tag="")).strip() == ""


@pytest.mark.parametrize(
    "page",
    [
        "index.html",
        "my_skills.html",
        "fleet_command/index.html",
        "fleet_command/doctrine.html",
        "fleet_ops/index.html",
        "management/index.html",
    ],
)
def test_every_doctrine_page_uses_the_shared_badge(page):
    source = (TEMPLATES / "aa_fleet_hub" / page).read_text()
    assert 'include "aa_fleet_hub/partials/doctrine_tag.html"' in source
    assert "badge-tag-" not in source
