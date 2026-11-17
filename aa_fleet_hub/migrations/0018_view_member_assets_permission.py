from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [
        ("aa_fleet_hub", "0017_dashboard_widget_enabled"),
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
                    (
                        "view_member_assets",
                        "Can view member ship ownership & location (High Command)",
                    ),
                ),
            },
        ),
    ]
