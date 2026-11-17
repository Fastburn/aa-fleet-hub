from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("aa_fleet_hub", "0013_doctrine_visible_to_all_members"),
    ]

    operations = [
        migrations.AddField(
            model_name="doctrine",
            name="tag_color",
            field=models.CharField(
                blank=True,
                choices=[
                    ("green", "Green"),
                    ("blue", "Blue"),
                    ("gold", "Gold"),
                    ("red", "Red"),
                    ("purple", "Purple"),
                ],
                default="green",
                max_length=10,
            ),
        ),
    ]
