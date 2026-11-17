import re

from django.contrib.auth.models import Group, User
from django.core.cache import cache
from django.core.validators import RegexValidator
from django.db import models
from django.db.models.functions import Lower
from django.db.models.signals import pre_delete
from django.dispatch import receiver
from eveuniverse.models import EveType

# One ordering for every doctrine list and dropdown (#6): pinned first, then
# Sort Order, then name ignoring case, then pk so equal names never flap.
DOCTRINE_ORDER = ("-pinned", "sort_order", Lower("name"), "pk")

# Tag colours are stored as #RRGGBB (#5). The value goes into a style
# attribute, so nothing else is accepted.
TAG_COLOR_RE = re.compile(r"#[0-9a-f]{6}")
TAG_COLOR_VALIDATOR = RegexValidator(
    r"\A#[0-9a-fA-F]{6}\Z", "Enter a colour as a hex code like #3a7bd5."
)


def _relative_luminance(hex_color: str) -> float:
    """WCAG 2 relative luminance of a #rrggbb colour."""
    channels = []
    for i in (1, 3, 5):
        c = int(hex_color[i : i + 2], 16) / 255
        channels.append(c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4)
    r, g, b = channels
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def _cached_eve_type_name(type_id: int) -> str:
    key = f"fh:type_name:{type_id}"
    name = cache.get(key)
    if name is None:
        try:
            name = EveType.objects.get(id=type_id).name
        except EveType.DoesNotExist:
            name = f"Skill #{type_id}"
        cache.set(key, name, 86400 * 7)
    return name


class General(models.Model):
    class Meta:
        managed = False
        default_permissions = ()
        permissions = (
            ("basic_access", "Can view fleet doctrines"),
            ("fleet_command", "Can access Fleet Command page"),
            ("manage_fits", "Can add/edit/remove fits (FC)"),
            ("manage_doctrines", "Can create/edit/delete doctrines (Officer)"),
            (
                "view_member_assets",
                "Can view member ship ownership & location (High Command)",
            ),
        )


class Doctrine(models.Model):
    name = models.CharField(max_length=100)
    # The swatch row on the doctrine form: the five colours tags had before #5.
    TAG_COLOR_SWATCHES = [
        ("#4caf6b", "Green"),
        ("#4d9de0", "Blue"),
        ("#c8a84b", "Gold"),
        ("#e05252", "Red"),
        ("#9b59b6", "Purple"),
    ]
    DEFAULT_TAG_COLOR = "#4caf6b"
    tag = models.CharField(max_length=50, blank=True)
    tag_color = models.CharField(
        max_length=7,
        default=DEFAULT_TAG_COLOR,
        validators=[TAG_COLOR_VALIDATOR],
        help_text="Tag badge colour as #RRGGBB.",
    )
    description = models.TextField(blank=True)
    notes = models.TextField(blank=True)
    # Deprecated (#3): MOTD text lives on Motd now and nothing reads or writes
    # this. It is kept, frozen at the text 0026 copied, so the previous
    # release still works if it is reinstalled. Drop it in a later release.
    motd_template = models.TextField(blank=True)
    active = models.BooleanField(default=True)
    is_draft = models.BooleanField(default=False)
    public = models.BooleanField(default=False)
    groups = models.ManyToManyField(Group, blank=True, related_name="fleet_doctrines")
    motd_groups = models.ManyToManyField(
        Group, blank=True, related_name="fleet_motd_doctrines"
    )
    visible_to_all_members = models.BooleanField(
        default=False,
        help_text="Self-service: every member can apply this doctrine's fleet "
        "structure to their own fleet from the Doctrines page. Its MOTDs reach "
        "all members only when made public in Fleet Setup. Intended for "
        "doctrines like Home Defence.",
    )
    sort_order = models.IntegerField(default=0)
    # db_default too, so the previous release (which doesn't know this column)
    # can still insert doctrines after a rollback.
    pinned = models.BooleanField(
        default=False,
        db_default=False,
        help_text="Pinned doctrines list before all others, in their own "
        "Pinned section on pages that group by tag.",
    )
    archived = models.BooleanField(default=False)
    collapsed_by_default = models.BooleanField(
        default=True,
        help_text="If checked, this doctrine's card starts collapsed on the "
        "Doctrines and Manage pages until a viewer clicks to expand it.",
    )
    created_by = models.ForeignKey(
        User, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = DOCTRINE_ORDER
        indexes = [models.Index(fields=["active", "archived", "is_draft"])]

    def __str__(self):
        return self.name

    @property
    def tag_label(self):
        """Tag as the tag groups and filters show it, "Untagged" when blank."""
        return (self.tag or "").strip() or "Untagged"

    @property
    def tag_color_hex(self):
        """Tag colour safe to put in a style attribute, the default if invalid.

        The five colour names 0.1.1 stored ("blue"...) map to their swatch,
        so a doctrine saved while rolled back keeps its colour."""
        color = (self.tag_color or "").strip().lower()
        if TAG_COLOR_RE.fullmatch(color):
            return color
        return {name.lower(): hex_ for hex_, name in self.TAG_COLOR_SWATCHES}.get(
            color, self.DEFAULT_TAG_COLOR
        )

    @property
    def tag_text_color(self):
        """Black or white, whichever contrasts more with the tag colour.

        Uses WCAG relative luminance. The better of the two is always at
        least 4.58:1, so tag text meets the 4.5:1 minimum on any colour.
        """
        bg = _relative_luminance(self.tag_color_hex)
        on_black = (bg + 0.05) / 0.05
        on_white = 1.05 / (bg + 0.05)
        return "#000000" if on_black >= on_white else "#ffffff"


class Fit(models.Model):
    doctrine = models.ForeignKey(
        Doctrine, related_name="fits", on_delete=models.CASCADE
    )
    ship_type_id = models.IntegerField()
    name = models.CharField(max_length=100)
    role = models.CharField(max_length=50, blank=True)
    description = models.TextField(blank=True)
    notes = models.TextField(blank=True)
    eft = models.TextField(blank=True)
    is_skill_plan = models.BooleanField(default=False)
    img_endpoint = models.CharField(max_length=10, default="render")
    pi_plans = models.JSONField(null=True, blank=True)
    sort_order = models.IntegerField(default=0)
    version = models.CharField(max_length=20, default="1.0")

    class Meta:
        ordering = ["sort_order", "name"]

    def __str__(self):
        return f"{self.doctrine.name} — {self.name}"

    def ship_image_url(self, size=64):
        """Return EVE image server URL for this fit's ship or skill-plan icon."""
        if not self.ship_type_id or self.ship_type_id <= 0:
            return ""
        ep = self.img_endpoint or ("icon" if self.is_skill_plan else "render")
        return f"https://images.evetech.net/types/{self.ship_type_id}/{ep}?size={size}"

    @property
    def ship_image_url_128(self):
        return self.ship_image_url(128)


class FitChangeLog(models.Model):
    fit = models.ForeignKey(Fit, related_name="changelog", on_delete=models.CASCADE)
    version = models.CharField(max_length=20)
    note = models.TextField(blank=True)
    changed_by = models.ForeignKey(
        User, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.fit.name} v{self.version} — {self.changed_by}"


class FitTier(models.Model):
    fit = models.ForeignKey(Fit, related_name="tiers", on_delete=models.CASCADE)
    label = models.CharField(max_length=50)
    description = models.TextField(blank=True)
    warning = models.TextField(blank=True)
    sort_order = models.IntegerField(default=0)

    class Meta:
        ordering = ["sort_order"]

    def __str__(self):
        return f"{self.fit.name} — {self.label}"


class FitTierSkill(models.Model):
    tier = models.ForeignKey(FitTier, related_name="skills", on_delete=models.CASCADE)
    skill_type_id = models.IntegerField(
        db_index=True
    )  # indexed for bulk filter in readiness task
    required_level = models.PositiveSmallIntegerField()

    def skill_name(self):
        return _cached_eve_type_name(self.skill_type_id)

    def __str__(self):
        return f"{self.skill_name()} {self.required_level}"


class FleetStructureTemplate(models.Model):
    """
    A reusable fleet wing/squad layout. Can be linked to a doctrine or standalone.
    Structure JSON: {"wings": [{"name": "DPS Wing", "squads": ["Alpha", "Bravo"]}, ...]}
    """

    name = models.CharField(max_length=100)
    doctrine = models.ForeignKey(
        Doctrine,
        null=True,
        blank=True,
        related_name="fleet_structures",
        on_delete=models.SET_NULL,
    )
    structure = models.JSONField(default=dict)
    is_free_move = models.BooleanField(default=False)
    collapsed_by_default = models.BooleanField(
        default=True,
        help_text="If checked, this structure template's wing/squad tree starts "
        "collapsed on the Manage and Fleet Setup pages until a viewer "
        "clicks to expand it.",
    )
    created_by = models.ForeignKey(
        User, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return f"{self.name}" + (
            f" ({self.doctrine.name})" if self.doctrine else " (standalone)"
        )

    def wings(self):
        return self.structure.get("wings", [])


class FleetHubPreference(models.Model):
    user = models.OneToOneField(
        User, on_delete=models.CASCADE, related_name="fleet_hub_pref"
    )
    show_dashboard_widget = models.BooleanField(default=False)
    # Fleet Command doctrine page filters. Display preferences only; hidden rows
    # are left out of the page entirely rather than hidden client side.
    # db_default: see Doctrine.pinned (rollback to the previous release).
    readiness_hide_alts = models.BooleanField(default=True, db_default=True)
    readiness_hide_cant_fly = models.BooleanField(default=True, db_default=True)

    class Meta:
        default_permissions = ()

    def __str__(self):
        return f"{self.user} — fleet hub prefs"


class FleetHubSettings(models.Model):
    """Singleton configuration model. Always use FleetHubSettings.get() — never query directly."""

    tier_close_max = models.PositiveSmallIntegerField(
        default=2,
        help_text="Skills missing ≤ this → 'N skills' (close) badge.",
    )
    tier_training_max = models.PositiveSmallIntegerField(
        default=4,
        help_text="Skills missing ≤ this (and > close_max) → 'Training' badge. Above this → 'Not yet'.",
    )
    doctrine_default_visible = models.BooleanField(
        default=False,
        help_text="Pre-check 'Visible to all members' when creating a new doctrine.",
    )
    dashboard_widget_enabled = models.BooleanField(
        default=True,
        help_text="Show the Fleet Readiness dashboard widget. When off, the widget is hidden for all users regardless of their individual opt-in.",
    )

    class Meta:
        verbose_name = "Fleet Hub Settings"
        verbose_name_plural = "Fleet Hub Settings"
        default_permissions = ()

    def save(self, *args, **kwargs):
        self.pk = 1
        super().save(*args, **kwargs)
        from django.core.cache import cache

        cache.delete("fleethub_settings")

    @classmethod
    def get(cls):
        from django.core.cache import cache

        obj = cache.get("fleethub_settings")
        if obj is None:
            obj, _ = cls.objects.get_or_create(pk=1)
            cache.set("fleethub_settings", obj, 300)
        return obj

    def __str__(self):
        return "Fleet Hub Settings"


class DoctrineSharedSkill(models.Model):
    doctrine = models.ForeignKey(
        Doctrine, related_name="shared_skills", on_delete=models.CASCADE
    )
    skill_type_id = models.IntegerField(
        db_index=True
    )  # indexed for bulk filter in readiness task
    required_level = models.PositiveSmallIntegerField()

    def skill_name(self):
        return _cached_eve_type_name(self.skill_type_id)

    def __str__(self):
        return f"{self.doctrine.name} — {self.skill_name()} {self.required_level}"


class Motd(models.Model):
    """
    A fleet MOTD, made on Fleet Setup (#3). Attached to one doctrine, or
    standalone when ``doctrine`` is empty.

    Who sees it: an attached MOTD follows its doctrine's ``motd_groups``,
    plus all members when ``public`` is set. ``public`` only counts while
    attached (see ``is_public``), so an MOTD whose doctrine is deleted drops
    back to Fleet Setup visibility instead of becoming visible to everyone.
    """

    name = models.CharField(max_length=100)
    # Raw in-game text. Never strip or sanitise: it must paste back byte-for-byte.
    text = models.TextField(blank=True)
    doctrine = models.ForeignKey(
        Doctrine,
        null=True,
        blank=True,
        related_name="motds",
        # Keep the text if the doctrine goes; the MOTD becomes standalone.
        on_delete=models.SET_NULL,
    )
    is_default = models.BooleanField(
        default=False,
        help_text="The doctrine's quick-use MOTD. One per doctrine.",
    )
    public = models.BooleanField(
        default=False,
        help_text="Visible to all members on its doctrine, like Home Defence. "
        "Only applies while attached to a doctrine.",
    )
    created_by = models.ForeignKey(
        User, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-is_default", "name"]
        verbose_name = "MOTD"
        verbose_name_plural = "MOTDs"

    def __str__(self):
        return f"{self.name}" + (
            f" ({self.doctrine.name})" if self.doctrine_id else " (standalone)"
        )

    @property
    def is_public(self):
        return self.public and self.doctrine_id is not None

    def save(self, *args, **kwargs):
        # A standalone MOTD can't be a doctrine default or public; clearing
        # both stops a detached MOTD coming back public or default when it is
        # attached again. One default per doctrine is kept here rather than by
        # a conditional unique constraint, which MariaDB silently ignores.
        if self.doctrine_id is None:
            self.is_default = False
            self.public = False
        super().save(*args, **kwargs)
        if self.is_default:
            Motd.objects.filter(doctrine_id=self.doctrine_id, is_default=True).exclude(
                pk=self.pk
            ).update(is_default=False)


@receiver(pre_delete, sender=Doctrine)
def _detach_motds(sender, instance, **kwargs):
    """A deleted doctrine's MOTDs become standalone (SET_NULL skips save()),
    so clear their default and public flags here, for admin deletes too."""
    instance.motds.update(is_default=False, public=False)


class MotdQuickLink(models.Model):
    """Admin-configurable quick-insert links for the MOTD toolbar."""

    label = models.CharField(
        max_length=100,
        help_text="Dropdown label shown in the toolbar (e.g. 'Standing Fleet')",
    )
    url = models.CharField(max_length=500, help_text="Full URL the link points to")
    link_text = models.CharField(
        max_length=100,
        help_text="Text shown in the MOTD link (e.g. 'Join Standing Fleet')",
    )
    order = models.PositiveSmallIntegerField(
        default=0, help_text="Sort order in the dropdown (lower = higher)"
    )

    class Meta:
        ordering = ["order", "label"]

    def __str__(self):
        return self.label
