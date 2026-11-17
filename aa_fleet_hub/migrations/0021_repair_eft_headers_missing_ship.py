import re

from django.db import migrations


def repair_eft_headers(apps, schema_editor):
    """Prepend the ship name to EFT headers saved as '[Fit Name]'."""
    Fit = apps.get_model("aa_fleet_hub", "Fit")
    EveType = apps.get_model("eveuniverse", "EveType")
    for fit in Fit.objects.exclude(eft="").filter(ship_type_id__gt=0):
        header, newline, rest = fit.eft.partition("\n")
        m = re.match(r"^\[([^\],]+)\]$", header.strip())
        if not m:
            continue
        ship = EveType.objects.filter(id=fit.ship_type_id).first()
        if not ship:
            continue
        # Keep the header's own line ending, so a CRLF fit stays all CRLF.
        cr = "\r" if header.endswith("\r") else ""
        fit.eft = f"[{ship.name}, {m.group(1).strip()}]{cr}{newline}{rest}"
        fit.save(update_fields=["eft"])


class Migration(migrations.Migration):
    dependencies = [
        ("aa_fleet_hub", "0020_fleetstructuretemplate_collapsed_by_default"),
        ("eveuniverse", "0001_initial"),
    ]

    operations = [
        migrations.RunPython(repair_eft_headers, migrations.RunPython.noop),
    ]
