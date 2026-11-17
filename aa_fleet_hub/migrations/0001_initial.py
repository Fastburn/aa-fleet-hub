from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    initial = True

    dependencies = [
        ("auth", "0012_alter_user_first_name_max_length"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="General",
            fields=[
                (
                    "id",
                    models.AutoField(
                        auto_created=True, primary_key=True, serialize=False
                    ),
                ),
            ],
            options={
                "managed": False,
                "default_permissions": (),
                "permissions": (
                    ("basic_access", "Can view fleet doctrines"),
                    ("manage_fits", "Can add/edit/remove fits (FC)"),
                    ("manage_doctrines", "Can create/edit/delete doctrines (Officer)"),
                ),
            },
        ),
        migrations.CreateModel(
            name="Doctrine",
            fields=[
                (
                    "id",
                    models.AutoField(
                        auto_created=True, primary_key=True, serialize=False
                    ),
                ),
                ("name", models.CharField(max_length=100)),
                ("tag", models.CharField(blank=True, max_length=50)),
                ("description", models.TextField(blank=True)),
                ("active", models.BooleanField(default=True)),
                ("public", models.BooleanField(default=False)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                (
                    "groups",
                    models.ManyToManyField(
                        blank=True, related_name="fleet_doctrines", to="auth.Group"
                    ),
                ),
                (
                    "created_by",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="+",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
            options={"ordering": ["name"]},
        ),
        migrations.CreateModel(
            name="Fit",
            fields=[
                (
                    "id",
                    models.AutoField(
                        auto_created=True, primary_key=True, serialize=False
                    ),
                ),
                ("ship_type_id", models.IntegerField()),
                ("name", models.CharField(max_length=100)),
                ("role", models.CharField(blank=True, max_length=50)),
                ("description", models.TextField(blank=True)),
                ("eft", models.TextField(blank=True)),
                ("is_skill_plan", models.BooleanField(default=False)),
                ("img_endpoint", models.CharField(default="render", max_length=10)),
                ("pi_plans", models.JSONField(blank=True, null=True)),
                ("sort_order", models.IntegerField(default=0)),
                (
                    "doctrine",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="fits",
                        to="aa_fleet_hub.doctrine",
                    ),
                ),
            ],
            options={"ordering": ["sort_order", "name"]},
        ),
        migrations.CreateModel(
            name="FitTier",
            fields=[
                (
                    "id",
                    models.AutoField(
                        auto_created=True, primary_key=True, serialize=False
                    ),
                ),
                ("label", models.CharField(max_length=50)),
                ("description", models.TextField(blank=True)),
                ("warning", models.TextField(blank=True)),
                ("sort_order", models.IntegerField(default=0)),
                (
                    "fit",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="tiers",
                        to="aa_fleet_hub.fit",
                    ),
                ),
            ],
            options={"ordering": ["sort_order"]},
        ),
        migrations.CreateModel(
            name="FitTierSkill",
            fields=[
                (
                    "id",
                    models.AutoField(
                        auto_created=True, primary_key=True, serialize=False
                    ),
                ),
                ("skill_type_id", models.IntegerField()),
                ("required_level", models.PositiveSmallIntegerField()),
                (
                    "tier",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="skills",
                        to="aa_fleet_hub.fittier",
                    ),
                ),
            ],
        ),
        migrations.CreateModel(
            name="DoctrineSharedSkill",
            fields=[
                (
                    "id",
                    models.AutoField(
                        auto_created=True, primary_key=True, serialize=False
                    ),
                ),
                ("skill_type_id", models.IntegerField()),
                ("required_level", models.PositiveSmallIntegerField()),
                (
                    "doctrine",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="shared_skills",
                        to="aa_fleet_hub.doctrine",
                    ),
                ),
            ],
        ),
    ]
