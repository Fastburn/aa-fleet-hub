from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [
        ("aa_fleet_hub", "0011_doctrine_composite_index"),
    ]

    operations = [
        migrations.AlterModelOptions(
            name="general",
            options={
                "default_permissions": (),
                "managed": False,
                "permissions": (
                    ("basic_access", "Can view fleet doctrines"),
                    ("fleet_command", "Can access Fleet Command page"),
                    ("manage_fits", "Can add/edit/remove fits (FC)"),
                    ("manage_doctrines", "Can create/edit/delete doctrines (Officer)"),
                ),
            },
        ),
    ]
