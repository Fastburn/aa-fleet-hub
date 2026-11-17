"""Give the columns added in 0022 and 0023 a database default.

Django only uses a field's default while adding the column, then drops it.
The previous release doesn't know these columns, so after a rollback its
inserts leave them out, and MariaDB in strict mode rejects the row ("Field
'pinned' doesn't have a default value"): no new doctrines, no first widget
toggle. With a database default those inserts work again.

Schema metadata only (ALTER COLUMN ... SET DEFAULT), no data change.
"""

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("aa_fleet_hub", "0026_motd_from_doctrines"),
    ]

    operations = [
        migrations.AlterField(
            model_name="doctrine",
            name="pinned",
            field=models.BooleanField(
                db_default=False,
                default=False,
                help_text="Pinned doctrines list before all others, in their own Pinned section on pages that group by tag.",
            ),
        ),
        migrations.AlterField(
            model_name="fleethubpreference",
            name="readiness_hide_alts",
            field=models.BooleanField(db_default=True, default=True),
        ),
        migrations.AlterField(
            model_name="fleethubpreference",
            name="readiness_hide_cant_fly",
            field=models.BooleanField(db_default=True, default=True),
        ),
    ]
