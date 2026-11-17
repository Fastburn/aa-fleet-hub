"""Lift each doctrine's inline MOTD onto the new Motd model (#3).

Every doctrine with MOTD text gets one Motd: attached to it, marked default,
named after the doctrine, owned by whoever made the doctrine. The text is
copied exactly. Doctrines that were "visible to all members" (Home Defence
and the like) get a public MOTD, so the same members keep seeing it.
Visibility otherwise stays on the doctrine's ``motd_groups``, which this
leaves alone, so nobody gains or loses access.

Doctrine.motd_template is not touched here. It stays, unused, so the previous
release still works if reinstalled; a later release drops it.

Reversing deletes every Motd row, including MOTDs made after the release, so
roll back by reinstalling the previous wheel, not by migrating backwards.
"""

from django.db import migrations


def doctrines_to_motds(apps, schema_editor):
    Doctrine = apps.get_model("aa_fleet_hub", "Doctrine")
    Motd = apps.get_model("aa_fleet_hub", "Motd")
    # Running this again (say after a manual reverse and re-apply) must not
    # make a second default for a doctrine that already has one.
    done = set(
        Motd.objects.filter(is_default=True, doctrine__isnull=False).values_list(
            "doctrine_id", flat=True
        )
    )
    Motd.objects.bulk_create(
        Motd(
            name=doctrine.name,
            text=doctrine.motd_template,
            doctrine_id=doctrine.pk,
            is_default=True,
            public=doctrine.visible_to_all_members,
            created_by_id=doctrine.created_by_id,
        )
        for doctrine in Doctrine.objects.exclude(motd_template="").only(
            "pk", "name", "motd_template", "visible_to_all_members", "created_by_id"
        )
        if doctrine.pk not in done
    )


def motds_to_doctrines(apps, schema_editor):
    """Put each doctrine's default MOTD back inline, then drop the Motd rows.

    MOTDs made after the migration that aren't a doctrine's default have no
    place in the old layout and are removed with the rest.
    """
    Doctrine = apps.get_model("aa_fleet_hub", "Doctrine")
    Motd = apps.get_model("aa_fleet_hub", "Motd")
    defaults = dict(
        Motd.objects.filter(is_default=True, doctrine__isnull=False).values_list(
            "doctrine_id", "text"
        )
    )
    for doctrine in Doctrine.objects.only("pk", "motd_template"):
        text = defaults.get(doctrine.pk, "")
        if text != doctrine.motd_template:
            Doctrine.objects.filter(pk=doctrine.pk).update(motd_template=text)
    Motd.objects.all().delete()


class Migration(migrations.Migration):
    dependencies = [
        ("aa_fleet_hub", "0025_motd"),
    ]

    operations = [
        migrations.RunPython(doctrines_to_motds, motds_to_doctrines),
    ]
