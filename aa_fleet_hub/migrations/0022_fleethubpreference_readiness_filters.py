from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("aa_fleet_hub", "0021_repair_eft_headers_missing_ship"),
    ]

    operations = [
        migrations.AddField(
            model_name="fleethubpreference",
            name="readiness_hide_alts",
            field=models.BooleanField(default=True),
        ),
        migrations.AddField(
            model_name="fleethubpreference",
            name="readiness_hide_cant_fly",
            field=models.BooleanField(default=True),
        ),
    ]
