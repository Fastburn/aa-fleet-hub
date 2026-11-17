"""#3: who can see, make, edit and push MOTDs and fleet structures.

Run against real users and permissions in the test database. Only render,
messages and ESI are mocked, so the permission rules themselves are what's
tested.
"""

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from django.contrib.auth.models import Group, Permission, User
from django.core.exceptions import PermissionDenied
from django.http import Http404
from django.template import Context, Engine
from django.test import RequestFactory

from aa_fleet_hub import views
from aa_fleet_hub.models import Doctrine, FleetStructureTemplate, Motd

TEMPLATES = Path(views.__file__).parent / "templates"
INGAME_MOTD = (
    '  <font size="14" color="#ffd98d00"><b>HOME DEFENCE</b></font>\r\n'
    "\r\n"
    'Comms: <a href="joinChannel:player_-12345">Bane Fleet</a>  \n'
)

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def _urls(settings):
    settings.ROOT_URLCONF = "tests.urls"


def _user(name, *perms, groups=()):
    user = User.objects.create_user(name)
    for codename in ("basic_access", *perms):
        user.user_permissions.add(
            Permission.objects.get(
                codename=codename, content_type__app_label="aa_fleet_hub"
            )
        )
    user.groups.add(*groups)
    # Fresh copy: has_perm caches what it saw first.
    return User.objects.get(pk=user.pk)


@pytest.fixture
def fcs():
    return Group.objects.create(name="FCs")


@pytest.fixture
def people(fcs):
    return {
        "member": _user("member"),
        "fc": _user("fc", "fleet_command", groups=[fcs]),
        "other_fc": _user("other_fc", "fleet_command"),
        "fit_manager": _user("fit_manager", "manage_fits"),
        "manager": _user("manager", "manage_doctrines"),
    }


@pytest.fixture
def doctrines(fcs):
    hd = Doctrine.objects.create(
        name="Home Defence", active=True, public=True, visible_to_all_members=True
    )
    ferox = Doctrine.objects.create(name="Ferox Fleet", active=True, public=True)
    ferox.motd_groups.add(fcs)
    caps = Doctrine.objects.create(name="Dread Bomb", active=True, public=False)
    caps.groups.add(Group.objects.create(name="Capitals"))
    return {"hd": hd, "ferox": ferox, "caps": caps}


def _call(view, user, method="get", data=None, **kwargs):
    request = getattr(RequestFactory(), method)("/", data or {})
    request.user = user
    with (
        patch.object(views, "render") as render,
        patch.object(views, "messages"),
    ):
        response = view(request, **kwargs)
    return response, render


# --- who sees which MOTD ---------------------------------------------------


@pytest.fixture
def motds(doctrines, people):
    return {
        "hd_public": Motd.objects.create(
            name="HD", doctrine=doctrines["hd"], is_default=True, public=True
        ),
        "ferox_gated": Motd.objects.create(
            name="Ferox", doctrine=doctrines["ferox"], is_default=True
        ),
        "caps_public": Motd.objects.create(
            name="Caps", doctrine=doctrines["caps"], public=True
        ),
        "hd_private": Motd.objects.create(name="HD FCs", doctrine=doctrines["hd"]),
        "standalone": Motd.objects.create(name="Roam", created_by=people["other_fc"]),
    }


def _names(user):
    return set(views._visible_motds(user).values_list("name", flat=True))


def test_doctrine_manager_sees_every_motd(people, motds):
    assert _names(people["manager"]) == {m.name for m in motds.values()}


def test_fc_sees_standalone_public_and_their_groups_motds(people, motds):
    # Not the caps MOTD (doctrine hidden from them) or HD's private one.
    assert _names(people["fc"]) == {"HD", "Ferox", "Roam"}


def test_fc_sees_their_own_motd_on_any_doctrine(people, motds):
    motds["hd_private"].created_by = people["fc"]
    motds["hd_private"].save()
    assert "HD FCs" in _names(people["fc"])


def test_member_sees_only_public_motds_on_doctrines_they_can_see(people, motds):
    assert _names(people["member"]) == {"HD"}


def test_member_in_motd_groups_sees_the_gated_motd(people, motds, fcs):
    member = _user("grouped_member", groups=[fcs])
    assert _names(member) == {"HD", "Ferox"}


# --- making and editing MOTDs ----------------------------------------------


def test_member_without_fleet_setup_cannot_make_an_motd(people):
    with pytest.raises(PermissionDenied):
        _call(views.motd_create, people["member"])


def test_fc_makes_a_standalone_motd_with_exact_text(people, doctrines):
    response, _ = _call(
        views.motd_create,
        people["fc"],
        "post",
        {
            "name": "Roam",
            "text": INGAME_MOTD,
            # Ignored: only managers attach or make public.
            "doctrine_id": doctrines["hd"].pk,
            "is_default": "on",
            "public": "on",
        },
    )
    assert response.url == "/fleet-hub/fleet-ops/"
    m = Motd.objects.get()
    assert (m.doctrine, m.is_default, m.public, m.created_by) == (
        None,
        False,
        False,
        people["fc"],
    )
    assert m.text.encode() == INGAME_MOTD.encode()


def test_missing_name_keeps_what_was_typed(people):
    _, render = _call(
        views.motd_create, people["fc"], "post", {"name": " ", "text": INGAME_MOTD}
    )
    context = render.call_args.args[2]
    assert context["error"]
    assert context["form"]["text"] == INGAME_MOTD
    assert not Motd.objects.exists()


def test_fc_edit_keeps_what_a_manager_attached(people, doctrines):
    m = Motd.objects.create(
        name="HD",
        doctrine=doctrines["hd"],
        is_default=True,
        public=True,
        created_by=people["fc"],
    )
    _call(
        views.motd_edit,
        people["fc"],
        "post",
        {"name": "HD v2", "text": "new"},
        motd_id=m.pk,
    )
    m.refresh_from_db()
    assert (m.name, m.text, m.doctrine, m.is_default, m.public) == (
        "HD v2",
        "new",
        doctrines["hd"],
        True,
        True,
    )


@pytest.mark.parametrize("who", ["other_fc", "fit_manager"])
def test_only_the_owner_can_edit_or_delete(people, who):
    m = Motd.objects.create(name="Mine", created_by=people["fc"])
    with pytest.raises(Http404):
        _call(views.motd_edit, people[who], motd_id=m.pk)
    with pytest.raises(Http404):
        _call(views.motd_delete, people[who], "post", motd_id=m.pk)
    assert Motd.objects.filter(pk=m.pk).exists()


def test_owner_deletes_their_motd_by_post_only(people):
    m = Motd.objects.create(name="Mine", created_by=people["fc"])
    response, _ = _call(views.motd_delete, people["fc"], motd_id=m.pk)
    assert response.status_code == 405
    _call(views.motd_delete, people["fc"], "post", motd_id=m.pk)
    assert not Motd.objects.exists()


def test_manager_attaches_any_motd_as_public_default(people, doctrines):
    old = Motd.objects.create(name="Old", doctrine=doctrines["hd"], is_default=True)
    m = Motd.objects.create(name="Roam", created_by=people["fc"])
    _call(
        views.motd_edit,
        people["manager"],
        "post",
        {
            "name": "Roam",
            "text": "x",
            "doctrine_id": doctrines["hd"].pk,
            "is_default": "on",
            "public": "on",
        },
        motd_id=m.pk,
    )
    m.refresh_from_db()
    old.refresh_from_db()
    assert (m.doctrine, m.is_default, m.is_public) == (doctrines["hd"], True, True)
    assert not old.is_default
    assert m.created_by == people["fc"]


def test_manager_detaching_drops_default_and_public(people, doctrines):
    m = Motd.objects.create(
        name="HD", doctrine=doctrines["hd"], is_default=True, public=True
    )
    _call(
        views.motd_edit,
        people["manager"],
        "post",
        {"name": "HD", "text": "x", "public": "on"},
        motd_id=m.pk,
    )
    m.refresh_from_db()
    # The raw flags, not just is_public: a stale public=True would come back
    # the moment the MOTD is attached again.
    assert (m.doctrine, m.is_default, m.public) == (None, False, False)


def test_reattaching_a_detached_motd_does_not_make_it_public(people, doctrines):
    m = Motd.objects.create(name="HD", doctrine=doctrines["hd"], public=True)
    m.doctrine = None
    m.save()
    m.doctrine = doctrines["caps"]
    m.save()
    m.refresh_from_db()
    assert not m.public


def test_deleting_a_doctrine_leaves_its_motds_standalone_and_plain(doctrines):
    m = Motd.objects.create(
        name="HD", doctrine=doctrines["hd"], is_default=True, public=True
    )
    Doctrine.objects.filter(pk=doctrines["hd"].pk).delete()  # as admin does
    m.refresh_from_db()
    assert (m.doctrine, m.is_default, m.public) == (None, False, False)


def test_motd_form_ticks_only_the_flags_that_apply():
    source = (TEMPLATES / "aa_fleet_hub/fleet_ops/motd_form.html").read_text()
    assert "motd.is_default and motd.doctrine_id %}checked" in source
    assert "motd.is_public %}checked" in source


def test_manager_deletes_anyones_motd_and_structure(people):
    m = Motd.objects.create(name="Mine", created_by=people["fc"])
    s = FleetStructureTemplate.objects.create(name="Mine", created_by=people["fc"])
    _call(views.motd_delete, people["manager"], "post", motd_id=m.pk)
    _call(views.structure_delete, people["manager"], "post", structure_id=s.pk)
    assert not Motd.objects.exists()
    assert not FleetStructureTemplate.objects.exists()


@pytest.mark.parametrize("view", [views.motd_create, views.structure_create])
def test_a_garbled_doctrine_id_is_a_404_not_a_500(people, view):
    with pytest.raises(Http404):
        _call(view, people["manager"], "post", {"name": "X", "doctrine_id": "abc"})


def test_only_managers_get_the_doctrine_picker(people, doctrines):
    _, render = _call(views.motd_create, people["fc"])
    assert render.call_args.args[2]["can_attach"] is False
    assert render.call_args.args[2]["doctrines"] == []
    _, render = _call(views.motd_create, people["manager"])
    assert render.call_args.args[2]["can_attach"] is True
    assert set(render.call_args.args[2]["doctrines"]) == set(doctrines.values())


# --- fleet structures follow the same rules --------------------------------


def test_fc_makes_a_standalone_structure(people, doctrines):
    response, _ = _call(
        views.structure_create,
        people["fc"],
        "post",
        {"name": "Roam", "structure": "[]", "doctrine_id": doctrines["hd"].pk},
    )
    assert response.url == "/fleet-hub/fleet-ops/"
    s = FleetStructureTemplate.objects.get()
    assert (s.doctrine, s.created_by) == (None, people["fc"])


def test_fc_edit_keeps_the_managers_doctrine_link(people, doctrines):
    s = FleetStructureTemplate.objects.create(
        name="HD", doctrine=doctrines["hd"], created_by=people["fc"]
    )
    _call(
        views.structure_edit,
        people["fc"],
        "post",
        {"name": "HD v2", "structure": "[]"},
        structure_id=s.pk,
    )
    s.refresh_from_db()
    assert (s.name, s.doctrine) == ("HD v2", doctrines["hd"])


@pytest.mark.parametrize("who", ["other_fc", "fit_manager"])
def test_only_the_owner_edits_a_structure(people, who):
    s = FleetStructureTemplate.objects.create(name="Mine", created_by=people["fc"])
    with pytest.raises(Http404):
        _call(views.structure_edit, people[who], structure_id=s.pk)
    with pytest.raises(Http404):
        _call(views.structure_delete, people[who], "post", structure_id=s.pk)


def test_manager_links_any_structure_to_a_doctrine(people, doctrines):
    s = FleetStructureTemplate.objects.create(name="Roam", created_by=people["fc"])
    _call(
        views.structure_edit,
        people["manager"],
        "post",
        {"name": "Roam", "structure": "[]", "doctrine_id": doctrines["ferox"].pk},
        structure_id=s.pk,
    )
    s.refresh_from_db()
    assert s.doctrine == doctrines["ferox"]


# --- pushing an MOTD with Apply Structure ----------------------------------


def _apply(user, structure, data=None):
    esi = MagicMock()
    fleet = {"fleet_id": 9, "role": "fleet_commander"}
    esi.get.side_effect = lambda url, **kw: MagicMock(
        status_code=200, json=lambda: [] if url.endswith("/wings/") else fleet
    )
    esi.put.return_value = MagicMock(status_code=204)
    with (
        patch.object(views, "esi_requests", esi),
        patch.object(views, "_active_character_id", return_value=1),
        patch("aa_fleet_hub.tasks.apply_fleet_structure_wings"),
    ):
        response, _ = _call(
            views.structure_apply,
            user,
            "post",
            data,
            structure_id=structure.pk,
        )
    pushed = esi.put.call_args.kwargs["json"] if esi.put.called else None
    return response, pushed


WINGS = {"wings": [{"name": "Wing 1", "squads": ["Squad 1"]}]}


@pytest.fixture
def hd_structure(doctrines):
    return FleetStructureTemplate.objects.create(
        name="HD", doctrine=doctrines["hd"], structure=WINGS
    )


def test_apply_pushes_the_doctrines_default_motd(people, motds, hd_structure):
    motds["hd_public"].text = INGAME_MOTD
    motds["hd_public"].save()
    response, pushed = _apply(people["member"], hd_structure)
    assert response.status_code == 200
    assert pushed["motd"] == INGAME_MOTD


def test_apply_pushes_the_chosen_motd(people, motds, hd_structure):
    motds["standalone"].text = "roam text"
    motds["standalone"].save()
    _, pushed = _apply(people["fc"], hd_structure, {"motd_id": motds["standalone"].pk})
    assert pushed["motd"] == "roam text"


def test_apply_with_no_motd_chosen_pushes_none(people, motds, hd_structure):
    motds["hd_public"].text = "hd"
    motds["hd_public"].save()
    _, pushed = _apply(people["fc"], hd_structure, {"motd_id": ""})
    assert "motd" not in pushed


def test_apply_refuses_an_motd_the_user_cannot_see(people, motds, hd_structure):
    motds["caps_public"].text = "caps"
    motds["caps_public"].save()
    response, pushed = _apply(
        people["fc"], hd_structure, {"motd_id": motds["caps_public"].pk}
    )
    assert response.status_code == 403
    assert pushed is None


def test_self_service_member_only_gets_the_doctrines_own_motds(
    people, motds, hd_structure, fcs
):
    # Can see the Ferox MOTD through their group, but not push it via HD.
    member = _user("grouped_member", groups=[fcs])
    motds["ferox_gated"].text = "ferox"
    motds["ferox_gated"].save()
    response, pushed = _apply(
        member, hd_structure, {"motd_id": motds["ferox_gated"].pk}
    )
    assert response.status_code == 403
    assert pushed is None


def test_apply_skips_a_default_motd_the_user_cannot_see(people, doctrines):
    Motd.objects.create(
        name="Ferox", text="gated", doctrine=doctrines["ferox"], is_default=True
    )
    s = FleetStructureTemplate.objects.create(
        name="Ferox", doctrine=doctrines["ferox"], structure=WINGS
    )
    response, pushed = _apply(people["other_fc"], s)
    assert response.status_code == 200
    assert "motd" not in pushed


def test_fleet_runner_cannot_apply_a_structure_on_a_hidden_doctrine(people, doctrines):
    # other_fc isn't in Capitals, so Fleet Setup never lists this structure;
    # guessing its id must not push it either.
    caps = FleetStructureTemplate.objects.create(
        name="Caps", doctrine=doctrines["caps"], structure=WINGS
    )
    response, pushed = _apply(people["other_fc"], caps)
    assert response.status_code == 403
    assert pushed is None
    response, _ = _apply(people["manager"], caps)
    assert response.status_code == 200


def test_self_service_needs_a_doctrine_the_user_can_see(people, doctrines):
    # Self-service on a gated doctrine must not let outsiders push it by id.
    doctrines["caps"].visible_to_all_members = True
    doctrines["caps"].save()
    caps = FleetStructureTemplate.objects.create(
        name="Caps", doctrine=doctrines["caps"], structure=WINGS
    )
    for who in ("other_fc", "member"):
        response, pushed = _apply(people[who], caps)
        assert response.status_code == 403, who
        assert pushed is None
    response, _ = _apply(people["manager"], caps)
    assert response.status_code == 200


def test_self_service_stops_when_the_doctrine_is_archived(people, hd_structure):
    response, _ = _apply(people["member"], hd_structure)
    assert response.status_code == 200
    hd_structure.doctrine.archived = True
    hd_structure.doctrine.save()
    response, pushed = _apply(people["member"], hd_structure)
    assert response.status_code == 403
    assert pushed is None


def test_fleet_runner_can_apply_standalone_and_their_own_structures(people, doctrines):
    standalone = FleetStructureTemplate.objects.create(name="Roam", structure=WINGS)
    own_on_caps = FleetStructureTemplate.objects.create(
        name="Mine",
        doctrine=doctrines["caps"],
        structure=WINGS,
        created_by=people["other_fc"],
    )
    for structure in (standalone, own_on_caps):
        response, _ = _apply(people["other_fc"], structure)
        assert response.status_code == 200


def test_malformed_wings_are_dropped_not_crashing_apply(people):
    s = FleetStructureTemplate.objects.create(
        name="Odd",
        structure={"wings": ["junk", {"name": "W", "squads": ["A", {"x": 1}, 5]}]},
    )
    with patch("aa_fleet_hub.tasks.apply_fleet_structure_wings") as task:
        esi = MagicMock()
        esi.get.side_effect = lambda url, **kw: MagicMock(
            status_code=200,
            json=lambda: []
            if url.endswith("/wings/")
            else {"fleet_id": 9, "role": "fleet_commander"},
        )
        esi.put.return_value = MagicMock(status_code=204)
        with (
            patch.object(views, "esi_requests", esi),
            patch.object(views, "_active_character_id", return_value=1),
        ):
            response, _ = _call(
                views.structure_apply, people["fc"], "post", structure_id=s.pk
            )
    assert response.status_code == 200
    assert task.delay.call_args.args[2] == [{"name": "W", "squads": ["A"]}]


def test_structure_form_saves_cleaned_wings(people):
    posted = '["junk", {"name": "W", "squads": ["A", {"x": 1}, 5]}, {"squads": []}]'
    _call(
        views.structure_create,
        people["fc"],
        "post",
        {"name": "Odd", "structure": posted},
    )
    saved = FleetStructureTemplate.objects.get(name="Odd").structure
    assert saved == {
        "wings": [{"name": "W", "squads": ["A"]}, {"name": "Wing", "squads": []}]
    }


def test_clean_wings_keeps_the_form_shape():
    good = [{"name": "DPS", "squads": ["Alpha", "Bravo"]}]
    assert views._clean_wings(good) == good
    assert views._clean_wings({"wings": good}) == []
    assert views._clean_wings([{"squads": "nope"}]) == [{"name": "Wing", "squads": []}]


# --- editor ----------------------------------------------------------------


def test_editor_keeps_a_leading_newline_through_a_resave():
    engine = Engine(dirs=[str(TEMPLATES)])
    html = engine.get_template("aa_fleet_hub/partials/motd_editor.html").render(
        Context({"field_name": "text", "value": "\nfirst line"})
    )
    # Browsers drop one newline straight after <textarea>; the partial adds it.
    assert 'oninput="motdUpdatePreview()">\n\nfirst line</textarea>' in html


def test_quick_links_keep_ampersands_quotes_and_pipes():
    link = SimpleNamespace(
        label="Wiki", url="https://wiki/?a=1&b='2'|x", link_text="R&D | Wiki"
    )
    html = (
        Engine(dirs=[str(TEMPLATES)])
        .get_template("aa_fleet_hub/partials/motd_editor.html")
        .render(Context({"field_name": "text", "motd_quick_links": [link]}))
    )
    # HTML-escaped once in the attribute, which the browser decodes back to
    # the exact URL and text; nothing is split on "|".
    assert 'data-url="https://wiki/?a=1&amp;b=&#x27;2&#x27;|x"' in html
    assert 'data-text="R&amp;D | Wiki"' in html


# --- Fleet Setup page and pickers (step 2b) --------------------------------


def _fleet_setup(user):
    with patch.object(views, "_active_character_id", return_value=None):
        _, render = _call(views.fleet_ops_index, user)
    return render.call_args[0][2]


def test_fleet_setup_lists_what_the_fc_can_see_and_marks_their_own(people, motds):
    mine = Motd.objects.create(name="Mine", created_by=people["fc"])
    ctx = _fleet_setup(people["fc"])
    assert {m.name for m in ctx["motds"]} == {"HD", "Ferox", "Roam", "Mine"}
    assert {m.name for m in ctx["motds"] if m.editable} == {"Mine"}
    # Copy reads this JSON, so it must hold the exact text.
    assert ctx["motd_texts"][mine.pk] == mine.text
    assert ctx["has_standalone_motds"]


def test_fleet_setup_lets_a_manager_edit_everything(people, motds):
    ctx = _fleet_setup(people["manager"])
    assert all(m.editable for m in ctx["motds"])


def test_fleet_setup_doctrine_card_has_its_default_and_picker(people, motds):
    ctx = _fleet_setup(people["fc"])
    hd = next(i for i in ctx["doctrine_ops"] if i["doctrine"].name == "Home Defence")
    assert hd["default_motd"] == motds["hd_public"]
    labels = [label for label, _ in hd["motd_choices"]]
    assert labels == ["Home Defence", "Standalone", "Ferox Fleet"]


def test_picker_puts_the_doctrine_first_then_standalone_then_others(
    people, motds, doctrines
):
    all_motds = list(views._visible_motds(people["manager"]))
    choices = views._motd_choices(doctrines["ferox"], all_motds)
    assert [label for label, _ in choices] == [
        "Ferox Fleet",
        "Standalone",
        "Dread Bomb",
        "Home Defence",
    ]
    assert [m.name for m in choices[0][1]] == ["Ferox"]
    assert [m.name for m in choices[1][1]] == ["Roam"]
    # A standalone structure has no own group.
    assert views._motd_choices(None, all_motds)[0][0] == "Standalone"


def test_doctrines_page_only_gets_the_motds_the_member_may_see(
    people, motds, doctrines
):
    by_doctrine = views._motds_by_doctrine(people["member"], list(doctrines.values()))
    assert by_doctrine == {doctrines["hd"].pk: [motds["hd_public"]]}


def test_duplicating_a_doctrine_copies_its_motds_to_the_duplicator(
    people, motds, doctrines
):
    response, _ = _call(
        views.doctrine_duplicate,
        people["manager"],
        method="post",
        doctrine_id=doctrines["hd"].pk,
    )
    assert response.status_code == 302
    copy = Doctrine.objects.get(name="Home Defence (Copy)")
    copied = {m.name: m for m in copy.motds.all()}
    assert set(copied) == {"HD", "HD FCs"}
    assert copied["HD"].is_default and copied["HD"].public
    assert all(m.created_by == people["manager"] for m in copied.values())
    # The originals stay on the source doctrine.
    assert doctrines["hd"].motds.count() == 2


def test_picker_preselects_the_default_and_offers_no_motd():
    engine = Engine(dirs=[str(TEMPLATES)])
    a = SimpleNamespace(pk=1, name="HD", is_default=True)
    b = SimpleNamespace(pk=2, name="Roam", is_default=False)
    structure = SimpleNamespace(id=7, name="Standard")
    html = engine.get_template("aa_fleet_hub/partials/motd_picker.html").render(
        Context(
            {
                "structure": structure,
                "choices": [("Home Defence", [a]), ("Standalone", [b])],
                "selected": a,
            }
        )
    )
    assert 'id="motd-pick-7"' in html
    assert '<option value="">No MOTD</option>' in html
    assert '<option value="1" selected>HD (default)</option>' in html
    assert '<option value="2">Roam</option>' in html
    assert html.index('label="Home Defence"') < html.index('label="Standalone"')


# --- Fleet Setup owns fleet structures (step 2c, #7) -----------------------


def test_manage_no_longer_lists_fleet_structures(people, hd_structure):
    _, render = _call(views.manage_index, people["manager"])
    assert "structures" not in render.call_args[0][2]


@pytest.mark.parametrize(
    "old, new",
    [
        ("/fleet-hub/manage/structure/new/", "/fleet-hub/fleet-ops/structure/new/"),
        (
            "/fleet-hub/manage/structure/5/edit/",
            "/fleet-hub/fleet-ops/structure/5/edit/",
        ),
        # A redirect turns POST into GET, so the old delete lands on the edit
        # page, which has the Delete button.
        (
            "/fleet-hub/manage/structure/5/delete/",
            "/fleet-hub/fleet-ops/structure/5/edit/",
        ),
    ],
)
def test_old_manage_structure_links_redirect_to_fleet_setup(old, new):
    from django.urls import resolve

    match = resolve(old)
    request = RequestFactory().get(old)
    response = match.func(request, *match.args, **match.kwargs)
    assert response.status_code == 302
    assert response.url == new


@pytest.mark.parametrize(
    "who, sees", [("fc", True), ("manager", True), ("other_fc", False)]
)
def test_fleet_setup_keeps_structures_on_archived_doctrines_reachable(
    people, doctrines, who, sees
):
    old = Doctrine.objects.create(name="Old Doctrine", archived=True)
    structure = FleetStructureTemplate.objects.create(
        name="Old", doctrine=old, structure=WINGS, created_by=people["fc"]
    )
    sections = dict(_fleet_setup(people[who])["structure_sections"])
    others = sections["Other Fleet Structures"]
    assert (structure in others) is sees
    assert all(s.editable for s in others)
    # It is not listed twice.
    assert structure not in sections["Standalone Fleet Structures"]
