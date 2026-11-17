"""Tests for the single-doctrine view on the Doctrines page."""

import inspect
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import urlencode
from unittest.mock import MagicMock, patch

import pytest
from django.http import QueryDict
from django.template import Context, Engine
from django.test import override_settings

from aa_fleet_hub import views
from aa_fleet_hub.models import Doctrine

TEMPLATES = Path(views.__file__).parent / "templates"
STATIC = Path(views.__file__).parent / "static" / "aa_fleet_hub" / "js" / "fleet.js"


def _all(*items):
    return SimpleNamespace(all=lambda: list(items))


def _doctrine(pk, name="Ferox Fleet", tag="Mainline", collapsed=False):
    doctrine = SimpleNamespace(
        id=pk,
        pk=pk,
        name=name,
        tag=tag,
        tag_color="#4caf6b",
        pinned=False,
        sort_order=0,
        description="",
        notes="",
        is_draft=False,
        collapsed_by_default=collapsed,
        fits=_all(),
        shared_skills=_all(),
        motd_groups=_all(),
        fleet_structures=_all(),
        visible_to_all_members=True,
    )
    doctrine.tag_label = Doctrine.tag_label.fget(doctrine)
    doctrine.DEFAULT_TAG_COLOR = Doctrine.DEFAULT_TAG_COLOR
    doctrine.tag_color_hex = Doctrine.tag_color_hex.fget(doctrine)
    doctrine.tag_text_color = Doctrine.tag_text_color.fget(doctrine)
    return doctrine


class _Visible(list):
    """Stand-in for visible_doctrines(): a list that records filter() and none()."""

    def __init__(self, *doctrines):
        super().__init__(doctrines)
        self.calls = []

    def filter(self, pk):
        self.calls.append(("filter", pk))
        return [d for d in self if d.pk == pk]

    def none(self):
        self.calls.append(("none",))
        return []


def _run_index(query, visible, motds=None):
    """Run the view; motds stands in for _motds_by_doctrine's answer."""
    request = MagicMock()
    request.GET = QueryDict(query)
    request.user.is_superuser = False
    with (
        patch.object(views, "_active_character_id", return_value=None),
        patch.object(views, "visible_doctrines", return_value=visible),
        patch.object(views, "EveType"),
        patch.object(views, "ship_search_info", return_value={}),
        patch.object(views, "_owned_characters", return_value=[]),
        patch.object(views, "_can_see_fleet_ops", return_value=False),
        patch.object(
            views, "_motds_by_doctrine", return_value=motds or {}
        ) as motds_lookup,
        patch.object(views, "render") as render,
    ):
        inspect.unwrap(views.index)(request)
    ctx = render.call_args.args[2]
    ctx["motds_asked_for"] = list(motds_lookup.call_args.args[1])
    return ctx


def _names(ctx):
    return [d["doctrine"].name for d in ctx["doctrine_data"]]


def test_full_page_is_unchanged_without_the_parameter():
    visible = _Visible(_doctrine(1, "Ferox Fleet"), _doctrine(2, "Dreads"))
    ctx = _run_index("", visible)
    assert ctx["single_doctrine"] is False
    assert _names(ctx) == ["Ferox Fleet", "Dreads"]
    assert visible.calls == []


def test_doctrine_parameter_narrows_the_visible_queryset():
    visible = _Visible(_doctrine(1, "Ferox Fleet"), _doctrine(2, "Dreads"))
    ctx = _run_index("doctrine=2", visible)
    assert ctx["single_doctrine"] is True
    assert _names(ctx) == ["Dreads"]
    # Narrowed from visible_doctrines(), so access rules are the full page's.
    assert visible.calls == [("filter", 2)]


def test_doctrine_the_user_cannot_see_is_not_found():
    # Group-restricted, draft, archived and deleted doctrines are all missing
    # from visible_doctrines(), so they look the same as one that never existed.
    ctx = _run_index("doctrine=7", _Visible(_doctrine(1)))
    assert ctx["single_doctrine"] is True
    assert ctx["doctrine_data"] == []


@pytest.mark.parametrize(
    "value", ["", "abc", "5.0", "-1", " 5", "5\n", "²", "9999999999", "1;2"]
)
def test_malformed_id_is_not_found_without_querying_it(value):
    visible = _Visible(_doctrine(1), _doctrine(5))
    ctx = _run_index(urlencode({"doctrine": value}), visible)
    assert ctx["single_doctrine"] is True
    assert ctx["doctrine_data"] == []
    assert visible.calls == [("none",)]


def test_pinned_doctrine_still_shows_alone():
    pinned = _doctrine(3, "Home Defence")
    pinned.pinned = True
    ctx = _run_index("doctrine=3", _Visible(pinned, _doctrine(4, "Dreads")))
    assert [(label, len(items)) for label, items, _ in ctx["doctrine_groups"]] == [
        ("Pinned", 1)
    ]


def _restricted(pk, public=False):
    """A doctrine with a fleet structure, self-service or not."""
    doctrine = _doctrine(pk, "Blackops Drop")
    doctrine.fleet_structures = _all("Blops wings")
    doctrine.visible_to_all_members = public
    return doctrine


def _single(ctx):
    (item,) = ctx["doctrine_data"]
    return [m.name for m in item["motds"]], list(item["structures"])


def test_single_view_asks_for_the_motds_of_the_narrowed_doctrine_only():
    # Which MOTDs a user sees is _visible_motds' job (test_motd_views.py);
    # the page only asks about the doctrines it shows.
    visible = _Visible(_restricted(8), _doctrine(9, "Dreads"))
    ctx = _run_index("doctrine=8", visible)
    assert [d.pk for d in ctx["motds_asked_for"]] == [8]


def test_single_view_shows_the_motds_it_is_given_without_the_structure():
    motd = SimpleNamespace(pk=1, name="Cyno up", text="x", is_default=True)
    ctx = _run_index("doctrine=8", _Visible(_restricted(8)), motds={8: [motd]})
    assert _single(ctx) == (["Cyno up"], [])


def test_single_view_shows_the_structure_on_a_self_service_doctrine():
    ctx = _run_index("doctrine=8", _Visible(_restricted(8, public=True)))
    assert _single(ctx) == ([], ["Blops wings"])


# --- Real database -----------------------------------------------------------


def _db_index(user, query):
    """Run the view on real data: only the character lookups and render are stubbed."""
    request = MagicMock()
    request.GET = QueryDict(query)
    request.user = user
    with (
        patch.object(views, "_active_character_id", return_value=None),
        patch.object(views, "_owned_characters", return_value=[]),
        patch.object(views, "render") as render,
    ):
        inspect.unwrap(views.index)(request)
    return render.call_args.args[2]


@pytest.mark.django_db
def test_access_rules_hold_on_a_real_database(tmp_path):
    from django.contrib.auth.models import Group, User

    caps = Group.objects.create(name="Capitals")
    member = User.objects.create_user("member")
    capital_pilot = User.objects.create_user("capital_pilot")
    capital_pilot.groups.add(caps)

    public = Doctrine.objects.create(name="Ferox Fleet", public=True)
    gated = Doctrine.objects.create(name="Dread Bomb")
    gated.groups.add(caps)
    hidden = {
        "group only": gated,
        "draft": Doctrine.objects.create(name="Draft Op", public=True, is_draft=True),
        "archived": Doctrine.objects.create(name="Old Op", public=True, archived=True),
        "inactive": Doctrine.objects.create(
            name="Paused Op", public=True, active=False
        ),
    }

    assert _names(_db_index(member, f"doctrine={public.pk}")) == ["Ferox Fleet"]
    assert _names(_db_index(capital_pilot, f"doctrine={gated.pk}")) == ["Dread Bomb"]
    for why, doctrine in hidden.items():
        ctx = _db_index(member, f"doctrine={doctrine.pk}")
        assert ctx["doctrine_data"] == [], why
        html = _render(tmp_path / why.replace(" ", "_"), ctx)
        assert "Doctrine not found." in html, why
        assert doctrine.name not in html and "Ferox Fleet" not in html, why


# --- Template ---------------------------------------------------------------


def _render(tmp_path, ctx):
    # A stub base stands in for Alliance Auth's, so only the page content renders.
    (tmp_path / "aa_fleet_hub").mkdir(parents=True)
    (tmp_path / "aa_fleet_hub" / "base.html").write_text(
        "<title>{% block page_title %}{% endblock %}</title>"
        "{% block fleet_content %}{% endblock %}"
    )
    engine = Engine(
        dirs=[str(tmp_path), str(TEMPLATES)],
        libraries={
            "i18n": "django.templatetags.i18n",
            "static": "django.templatetags.static",
        },
    )
    template = engine.get_template("aa_fleet_hub/index.html")
    with override_settings(ROOT_URLCONF="tests.urls"):
        return template.render(Context(ctx))


def _page(doctrines, single):
    data = [
        {
            "doctrine": d,
            "fits": [],
            "fly_count": 0,
            "shared_skill_rows": [],
            "motds": [],
            "motd_choices": [],
            "structures": [],
        }
        for d in doctrines
    ]
    return {
        **views._group_by_tag(data),
        "doctrine_data": data,
        "single_doctrine": single,
        "ship_classes": [],
        "roles": {},
    }


def test_single_view_opens_the_doctrine_with_no_filters(tmp_path):
    html = _render(tmp_path, _page([_doctrine(2, "Dreads", collapsed=True)], True))
    # Expanded even though the doctrine is collapsed by default.
    assert 'class="fh-doc open" id="doctrine-2"' in html
    assert 'aria-expanded="true" aria-controls="doctrine-body-2"' in html
    assert 'href="/fleet-hub/"' in html and "Show all doctrines" in html
    assert 'id="fh-doctrine-filter"' not in html
    assert "Doctrine not found" not in html


def test_not_found_names_nothing(tmp_path):
    html = _render(tmp_path, _page([], True))
    assert "Doctrine not found." in html
    assert "Show all doctrines" in html
    assert "No doctrines available" not in html
    assert 'id="fh-doctrine-filter"' not in html
    assert "fh-doc" not in html.replace("fh-doctrines", "")


def test_hidden_doctrine_never_reaches_the_not_found_page(tmp_path):
    # Doctrine 7 exists but visible_doctrines() leaves it out for this user.
    hidden = _doctrine(7, "Secret Cyno Op", tag="Blackops")
    visible = _Visible(_doctrine(1, "Ferox Fleet"))
    assert hidden not in visible
    ctx = _run_index("doctrine=7", visible)
    html = _render(tmp_path, {**ctx, "ship_classes": [], "roles": {}})
    assert "Doctrine not found." in html
    # Nothing about it, and no fallback to the full list either.
    for text in ("Secret Cyno Op", "Blackops", "Ferox Fleet", "doctrine-7"):
        assert text not in html
    assert "<title>Doctrines</title>" in html


def test_single_view_titles_the_tab_with_the_doctrine(tmp_path):
    html = _render(tmp_path, _page([_doctrine(2, "Dreads")], True))
    assert "<title>Dreads</title>" in html
    assert '<span aria-hidden="true">&#8592;</span> Show all doctrines' in html


def test_old_hash_redirect_only_runs_on_the_doctrines_page(tmp_path):
    # My Skills also has .fh-doc cards and loads fleet.js; the redirect is
    # keyed on this marker, so only the Doctrines page may carry it.
    assert 'data-fh-page="doctrines"' in _render(tmp_path, _page([], False))
    for page in TEMPLATES.glob("aa_fleet_hub/**/*.html"):
        if page.name != "index.html" or page.parent.name != "aa_fleet_hub":
            assert "data-fh-page" not in page.read_text(), page
    assert "querySelector('[data-fh-page=\"doctrines\"]')" in STATIC.read_text()


def test_full_page_keeps_filters_and_collapsed_doctrines(tmp_path):
    html = _render(
        tmp_path,
        _page([_doctrine(1), _doctrine(2, "Dreads", collapsed=True)], False),
    )
    assert 'id="fh-doctrine-filter"' in html
    assert "Show all doctrines" not in html
    assert 'class="fh-doc" id="doctrine-2"' in html


def test_link_button_copies_the_single_doctrine_link():
    js = STATIC.read_text()
    assert "'?doctrine=' + doctrineId" in js
    # Old hash links are sent to the new view rather than scrolled to.
    assert "#doctrine-(\\d+)$" in js
    assert "location.replace(window.location.pathname + '?doctrine=' + m[1])" in js


def test_character_switch_returns_to_the_same_view():
    source = (TEMPLATES / "aa_fleet_hub/partials/character_switcher.html").read_text()
    # The current URL, so a search cleared after load isn't brought back.
    assert (
        "this.form.elements.next.value = location.pathname + location.search" in source
    )
    # Without JS, the URL the page was loaded with.
    assert 'name="next" value="{{ request.get_full_path }}"' in source
