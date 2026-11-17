import re

import django.core.validators
from django.db import migrations, models

# The five named colours before #5, as the hex the badges were drawn with.
NAME_TO_HEX = {
    "green": "#4caf6b",
    "blue": "#4d9de0",
    "gold": "#c8a84b",
    "red": "#e05252",
    "purple": "#9b59b6",
}
HEX_TO_NAME = {hex_: name for name, hex_ in NAME_TO_HEX.items()}


def names_to_hex(apps, schema_editor):
    Doctrine = apps.get_model("aa_fleet_hub", "Doctrine")
    for doctrine in Doctrine.objects.only("pk", "tag_color"):
        value = doctrine.tag_color or ""
        if re.fullmatch(r"#[0-9a-fA-F]{6}", value):
            # Already hex (a re-run on MySQL, which can't roll back): keep it.
            new = value.lower()
        else:
            # Blank and unknown names showed as green before, so they become green.
            new = NAME_TO_HEX.get(value, NAME_TO_HEX["green"])
        if new != doctrine.tag_color:
            Doctrine.objects.filter(pk=doctrine.pk).update(tag_color=new)


def hex_to_names(apps, schema_editor):
    Doctrine = apps.get_model("aa_fleet_hub", "Doctrine")
    for doctrine in Doctrine.objects.only("pk", "tag_color"):
        # Custom colours have no name, so they fall back to green.
        new = HEX_TO_NAME.get((doctrine.tag_color or "").lower(), "green")
        Doctrine.objects.filter(pk=doctrine.pk).update(tag_color=new)


class Migration(migrations.Migration):
    dependencies = [
        ("aa_fleet_hub", "0023_doctrine_pinned"),
    ]

    operations = [
        migrations.RunPython(names_to_hex, hex_to_names),
        migrations.AlterField(
            model_name="doctrine",
            name="tag_color",
            field=models.CharField(
                default="#4caf6b",
                help_text="Tag badge colour as #RRGGBB.",
                max_length=7,
                validators=[
                    django.core.validators.RegexValidator(
                        "\\A#[0-9a-fA-F]{6}\\Z",
                        "Enter a colour as a hex code like #3a7bd5.",
                    )
                ],
            ),
        ),
    ]
