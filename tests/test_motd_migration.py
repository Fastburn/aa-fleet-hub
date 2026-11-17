"""#3: MOTDs as their own records. The Motd model rules and the 0026 data
migration, run for real against the test database.

Each migration case is one the #3 acceptance criteria name: doctrines with
and without an MOTD, duplicate doctrines that only hold a different MOTD,
restricted visibility groups, "visible to all members" doctrines, and
multi-line MOTDs with in-game links and formatting. Each checks the text and
access come through unchanged, and that reversing restores the old layout.
"""

import importlib

import pytest
from django.apps import apps as real_apps
from django.db.migrations.state import ProjectState

from aa_fleet_hub.models import Doctrine, Motd

migration = importlib.import_module("aa_fleet_hub.migrations.0026_motd_from_doctrines")

# In-game markup, CRLF and LF line breaks, a tab, non-ASCII, and spaces at
# both ends: all must survive exactly.
INGAME_MOTD = (
    '  <font size="14" color="#ffd98d00"><b>HOME DEFENCE</b></font>\r\n'
    "\r\n"
    'Comms: <a href="joinChannel:player_-12345">Main Fleet</a>\n'
    'Staging: <a href="showinfo:5//30000142">Jita</a>\t— x-up in fleet\n'
    "Doctrine: <url=fitting:11987:2048;1:::>Guardian</url>  \n"
)


@pytest.fixture
def old_apps():
    """The models as 0026 sees them (Doctrine still has motd_template)."""
    return ProjectState.from_apps(real_apps).apps


@pytest.fixture
def run(old_apps):
    """forward()/reverse() run the real migration code; old() reads and
    writes the old inline MOTD."""

    class Runner:
        def forward(self):
            migration.doctrines_to_motds(old_apps, None)

        def reverse(self):
            migration.motds_to_doctrines(old_apps, None)

        def doctrine(self, name, motd="", **kwargs):
            d = Doctrine.objects.create(name=name, **kwargs)
            self.old().filter(pk=d.pk).update(motd_template=motd)
            return d

        def motd_of(self, doctrine):
            return self.old().get(pk=doctrine.pk).motd_template

        def old(self):
            return old_apps.get_model("aa_fleet_hub", "Doctrine").objects

    return Runner()


@pytest.fixture
def groups():
    from django.contrib.auth.models import Group

    return Group.objects.create(name="FCs"), Group.objects.create(name="Capitals")


# --- migration wiring ------------------------------------------------------


def test_migration_runs_forward_and_back_after_the_schema():
    (op,) = migration.Migration.operations
    assert op.code is migration.doctrines_to_motds
    assert op.reverse_code is migration.motds_to_doctrines
    assert migration.Migration.dependencies == [("aa_fleet_hub", "0025_motd")]


@pytest.mark.django_db
def test_forward_leaves_the_inline_motd_for_a_rollback(run):
    # The column is kept this release so the previous wheel still reads it.
    d = run.doctrine("Ferox Fleet", INGAME_MOTD)
    run.forward()
    assert run.motd_of(d).encode() == INGAME_MOTD.encode()


# --- forward ---------------------------------------------------------------


@pytest.mark.django_db
def test_doctrine_without_an_motd_gets_none(run):
    run.doctrine("Logi Only")
    run.forward()
    assert Motd.objects.count() == 0


@pytest.mark.django_db
def test_doctrine_motd_becomes_its_default_owned_by_the_doctrine_creator(run):
    from django.contrib.auth.models import User

    officer = User.objects.create_user("officer")
    d = run.doctrine("Ferox Fleet", "x up", created_by=officer)
    run.forward()
    (m,) = Motd.objects.all()
    assert (m.name, m.text, m.doctrine, m.is_default, m.public, m.created_by) == (
        "Ferox Fleet",
        "x up",
        d,
        True,
        False,
        officer,
    )


@pytest.mark.django_db
def test_motd_only_duplicates_each_keep_their_own_motd(run):
    hd = run.doctrine("Home Defence", "HD motd")
    roam = run.doctrine("Home Defence (Roaming)", "Roam motd")
    run.forward()
    # Not merged: officers move these by hand and archive the duplicate.
    assert hd.motds.get().text == "HD motd"
    assert roam.motds.get().text == "Roam motd"
    assert Motd.objects.filter(is_default=True).count() == 2


@pytest.mark.django_db
def test_restricted_doctrine_keeps_its_groups_and_motd_stays_private(run, groups):
    fcs, caps = groups
    d = run.doctrine("Dread Bomb", "secret")
    d.groups.add(caps)
    d.motd_groups.add(fcs)
    run.forward()
    d.refresh_from_db()
    assert list(d.groups.all()) == [caps]
    assert list(d.motd_groups.all()) == [fcs]
    m = d.motds.get()
    assert not m.public and not m.is_public


@pytest.mark.django_db
def test_visible_to_all_members_doctrine_gets_a_public_motd(run):
    d = run.doctrine("Home Defence", "HD", visible_to_all_members=True)
    run.forward()
    d.refresh_from_db()
    assert d.motds.get().is_public
    # The doctrine setting stays: it still controls applying the structure.
    assert d.visible_to_all_members


@pytest.mark.django_db
def test_ingame_text_comes_through_byte_for_byte(run):
    run.doctrine("HD", INGAME_MOTD)
    run.forward()
    assert Motd.objects.get().text.encode() == INGAME_MOTD.encode()


@pytest.mark.django_db
def test_rerun_does_not_make_a_second_default(run):
    d = run.doctrine("Ferox Fleet", "x up")
    run.forward()
    run.forward()
    assert d.motds.count() == 1


# --- reverse ---------------------------------------------------------------


@pytest.mark.django_db
def test_reverse_restores_every_doctrine_and_removes_the_motds(run, groups):
    fcs, _ = groups
    plain = run.doctrine("Logi Only")
    hd = run.doctrine("HD", INGAME_MOTD, visible_to_all_members=True)
    gated = run.doctrine("Dread Bomb", "secret")
    gated.motd_groups.add(fcs)
    run.forward()
    # Reverse writes each default back even over stale inline text.
    run.old().update(motd_template="stale")
    run.reverse()

    assert Motd.objects.count() == 0
    assert run.motd_of(plain) == ""
    assert run.motd_of(hd).encode() == INGAME_MOTD.encode()
    assert run.motd_of(gated) == "secret"
    hd.refresh_from_db()
    assert hd.visible_to_all_members
    assert list(gated.motd_groups.all()) == [fcs]


@pytest.mark.django_db
def test_reverse_uses_the_default_and_drops_extra_and_standalone_motds(run):
    d = run.doctrine("Ferox Fleet", "old")
    run.forward()
    Motd.objects.create(name="Roaming", text="roam", doctrine=d)
    Motd.objects.create(name="Standalone", text="solo")
    d.motds.filter(is_default=True).update(text="edited default")
    run.reverse()
    assert run.motd_of(d) == "edited default"
    assert Motd.objects.count() == 0


# --- model rules -----------------------------------------------------------


@pytest.mark.django_db
def test_one_default_per_doctrine():
    d = Doctrine.objects.create(name="Ferox Fleet")
    first = Motd.objects.create(name="A", doctrine=d, is_default=True)
    second = Motd.objects.create(name="B", doctrine=d, is_default=True)
    first.refresh_from_db()
    assert not first.is_default and second.is_default
    # Another doctrine's default is left alone.
    other = Motd.objects.create(
        name="C", doctrine=Doctrine.objects.create(name="Other"), is_default=True
    )
    second.refresh_from_db()
    assert second.is_default and other.is_default


@pytest.mark.django_db
def test_standalone_motd_is_never_default_or_public():
    m = Motd.objects.create(name="Solo", is_default=True, public=True)
    assert not m.is_default
    assert not m.is_public


@pytest.mark.django_db
def test_deleting_a_doctrine_keeps_its_motd_as_standalone_and_not_public():
    d = Doctrine.objects.create(name="Home Defence")
    m = Motd.objects.create(
        name="HD", text=INGAME_MOTD, doctrine=d, is_default=True, public=True
    )
    d.delete()
    m.refresh_from_db()
    assert m.doctrine is None
    assert m.text.encode() == INGAME_MOTD.encode()
    assert not m.is_public


@pytest.mark.django_db
def test_motd_text_is_saved_exactly():
    m = Motd.objects.create(name="HD", text=INGAME_MOTD)
    m.refresh_from_db()
    assert m.text.encode() == INGAME_MOTD.encode()
