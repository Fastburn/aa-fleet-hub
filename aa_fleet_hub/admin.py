import csv

from django.contrib import admin
from django.http import HttpResponse, HttpResponseRedirect
from django.shortcuts import render
from django.urls import path, reverse

from .models import (
    Doctrine,
    DoctrineSharedSkill,
    Fit,
    FitTier,
    FitTierSkill,
    FleetStructureTemplate,
    FleetHubSettings,
    General,
    Motd,
    MotdQuickLink,
)


class DoctrineSharedSkillInline(admin.TabularInline):
    model = DoctrineSharedSkill
    extra = 0


class FitTierSkillInline(admin.TabularInline):
    model = FitTierSkill
    extra = 0


class FitTierInline(admin.StackedInline):
    model = FitTier
    extra = 0
    show_change_link = True


class FitInline(admin.TabularInline):
    model = Fit
    extra = 0
    show_change_link = True
    fields = ("name", "role", "ship_type_id", "is_skill_plan", "sort_order")


@admin.register(Doctrine)
class DoctrineAdmin(admin.ModelAdmin):
    list_display = (
        "name",
        "tag",
        "pinned",
        "active",
        "is_draft",
        "public",
        "created_at",
    )
    list_filter = ("pinned", "active", "is_draft", "public")
    search_fields = ("name", "tag", "description")
    filter_horizontal = ("groups", "motd_groups")
    readonly_fields = ("created_at", "updated_at", "created_by")
    inlines = [DoctrineSharedSkillInline, FitInline]


@admin.register(Fit)
class FitAdmin(admin.ModelAdmin):
    list_display = ("name", "doctrine", "role", "ship_type_id", "is_skill_plan")
    list_filter = ("is_skill_plan", "doctrine")
    search_fields = ("name", "role", "doctrine__name")
    inlines = [FitTierInline]


@admin.register(FitTier)
class FitTierAdmin(admin.ModelAdmin):
    list_display = ("label", "fit", "sort_order")
    search_fields = ("label", "fit__name")
    inlines = [FitTierSkillInline]


@admin.register(FitTierSkill)
class FitTierSkillAdmin(admin.ModelAdmin):
    list_display = ("skill_type_id", "required_level", "tier")
    list_filter = ("required_level",)


@admin.register(FleetStructureTemplate)
class FleetStructureTemplateAdmin(admin.ModelAdmin):
    list_display = ("name", "doctrine", "created_by", "created_at")
    list_filter = ("doctrine",)
    search_fields = ("name",)
    readonly_fields = ("created_at", "updated_at", "created_by")


@admin.register(DoctrineSharedSkill)
class DoctrineSharedSkillAdmin(admin.ModelAdmin):
    list_display = ("skill_type_id", "required_level", "doctrine")


@admin.register(FleetHubSettings)
class FleetHubSettingsAdmin(admin.ModelAdmin):
    """Singleton settings — always edits the one row, no add/delete."""

    fieldsets = [
        (
            "Tier Badge Thresholds",
            {
                "fields": ("tier_close_max", "tier_training_max"),
                "description": (
                    "Skills missing ≤ close_max → N skills badge. "
                    "≤ training_max → Training badge. Above → Not yet."
                ),
            },
        ),
        (
            "Doctrine Defaults",
            {
                "fields": ("doctrine_default_visible",),
            },
        ),
        (
            "Dashboard",
            {
                "fields": ("dashboard_widget_enabled",),
            },
        ),
    ]

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    def changelist_view(self, request, extra_context=None):
        obj, _ = FleetHubSettings.objects.get_or_create(pk=1)
        return HttpResponseRedirect(
            reverse("admin:aa_fleet_hub_fleethubsettings_change", args=[obj.pk])
        )


@admin.register(Motd)
class MotdAdmin(admin.ModelAdmin):
    list_display = (
        "name",
        "doctrine",
        "is_default",
        "public",
        "created_by",
        "updated_at",
    )
    list_filter = (
        "is_default",
        "public",
        ("doctrine", admin.RelatedOnlyFieldListFilter),
    )
    search_fields = ("name", "text", "doctrine__name")
    raw_id_fields = ("created_by",)


@admin.register(MotdQuickLink)
class MotdQuickLinkAdmin(admin.ModelAdmin):
    list_display = ("label", "link_text", "url", "order")
    list_editable = ("order",)
    ordering = ("order", "label")


@admin.register(General)
class FleetHubToolsAdmin(admin.ModelAdmin):
    """Admin tools page — superuser shortcuts for maintenance tasks."""

    change_list_template = "admin/aa_fleet_hub/general/change_list.html"

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    def get_urls(self):
        custom = [
            path(
                "recompute-readiness/",
                self.admin_site.admin_view(self.recompute_readiness_view),
                name="fleethub_recompute_readiness",
            ),
            path(
                "refresh-eve-types/",
                self.admin_site.admin_view(self.refresh_eve_types_view),
                name="fleethub_refresh_eve_types",
            ),
            path(
                "export-member-roster/",
                self.admin_site.admin_view(self.export_member_roster_view),
                name="fleethub_export_member_roster",
            ),
        ]
        return custom + super().get_urls()

    def recompute_readiness_view(self, request):
        if request.method == "POST":
            from .tasks import update_readiness_cache

            update_readiness_cache.delay()
            self.message_user(
                request,
                "Readiness recompute queued — refresh Fleet Command in a minute.",
            )
        return HttpResponseRedirect(reverse("admin:aa_fleet_hub_general_changelist"))

    def refresh_eve_types_view(self, request):
        if request.method == "POST":
            from .tasks import refresh_eve_type_catalog

            refresh_eve_type_catalog.delay()
            self.message_user(
                request,
                "Ship/skill catalog refresh queued — autocomplete updates as Celery processes it.",
            )
        return HttpResponseRedirect(reverse("admin:aa_fleet_hub_general_changelist"))

    def export_member_roster_view(self, request):
        """
        CSV export of the same data the Fleet Command readiness page shows: one
        row per character x doctrine fit, including Main/Alt and ship ownership
        & location. admin_view() only requires is_staff, not is_superuser, so
        this export contains ship-location intel and must enforce superuser
        explicitly rather than relying on the admin wrapper alone.
        """
        if not request.user.is_superuser:
            from django.contrib.auth.views import redirect_to_login

            return redirect_to_login(request.get_full_path())

        from .tasks import get_all_doctrine_readiness

        response = HttpResponse(content_type="text/csv")
        response["Content-Disposition"] = (
            'attachment; filename="fleet_hub_member_roster.csv"'
        )
        writer = csv.writer(response)
        writer.writerow(
            [
                "Character",
                "Is Main",
                "Corporation",
                "Doctrine",
                "Fit",
                "Status",
                "Owned Hulls",
                "Locations",
            ]
        )

        readiness = get_all_doctrine_readiness()
        for doctrine_data in readiness.values():
            doctrine_name = doctrine_data["name"]
            for fit in doctrine_data["fits"]:
                for char in fit["per_char"]:
                    if fit["no_skill_plan"]:
                        status = "No Skill Plan"
                    elif char["bucket"] == "max":
                        status = char["best_tier"] or "Ready"
                    elif char["bucket"] == "rec":
                        status = char["best_tier"] or "Close"
                    elif char["bucket"] == "min":
                        status = char["best_tier"] or "Training"
                    else:
                        status = "Not yet"
                    owned_hulls = char.get("owned_hulls") or []
                    writer.writerow(
                        [
                            char["character_name"],
                            "Yes" if char["is_main"] else "No",
                            char["corp"],
                            doctrine_name,
                            fit["fit_name"],
                            status,
                            len(owned_hulls),
                            "; ".join(owned_hulls),
                        ]
                    )
        return response

    def changelist_view(self, request, extra_context=None):
        context = {
            **self.admin_site.each_context(request),
            "recompute_url": reverse("admin:fleethub_recompute_readiness"),
            "refresh_eve_url": reverse("admin:fleethub_refresh_eve_types"),
            "export_roster_url": reverse("admin:fleethub_export_member_roster"),
            "title": "Fleet Hub Tools",
            "opts": self.model._meta,
        }
        return render(request, self.change_list_template, context)
