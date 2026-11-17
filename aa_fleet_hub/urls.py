from django.urls import path
from django.views.generic import RedirectView

from . import views

app_name = "aa_fleet_hub"

urlpatterns = [
    # Member-facing
    path("", views.index, name="index"),
    path("fit/<int:fit_id>/", views.fit_detail, name="fit_detail"),
    path("skills/", views.my_skills, name="my_skills"),
    path("character/switch/", views.switch_character, name="switch_character"),
    # Skill export — one button per tier
    path(
        "fit/<int:fit_id>/export/<int:tier_id>/",
        views.export_tier_skills,
        name="export_tier_skills",
    ),
    # Fleet Command (FC+) — view/template names kept as "readiness_*" since that's
    # what they actually compute/display; only the user-facing path is rebranded.
    path("fleet-command/", views.readiness_index, name="readiness_index"),
    path(
        "fleet-command/doctrine/<int:doctrine_id>/",
        views.readiness_doctrine,
        name="readiness_doctrine",
    ),
    path(
        "fleet-command/filter/",
        views.readiness_filter_toggle,
        name="readiness_filter_toggle",
    ),
    # Fleet Setup (#3, #7): MOTDs and fleet structures, made and applied here
    path("fleet-ops/", views.fleet_ops_index, name="fleet_ops_index"),
    path("fleet-ops/motd/new/", views.motd_create, name="motd_create"),
    path("fleet-ops/motd/<int:motd_id>/edit/", views.motd_edit, name="motd_edit"),
    path("fleet-ops/motd/<int:motd_id>/delete/", views.motd_delete, name="motd_delete"),
    path("fleet-ops/structure/new/", views.structure_create, name="structure_create"),
    path(
        "fleet-ops/structure/<int:structure_id>/edit/",
        views.structure_edit,
        name="structure_edit",
    ),
    path(
        "fleet-ops/structure/<int:structure_id>/delete/",
        views.structure_delete,
        name="structure_delete",
    ),
    # Management (FC+)
    path("manage/", views.manage_index, name="manage_index"),
    path("manage/doctrine/new/", views.doctrine_create, name="doctrine_create"),
    path(
        "manage/doctrine/<int:doctrine_id>/edit/",
        views.doctrine_edit,
        name="doctrine_edit",
    ),
    path(
        "manage/doctrine/<int:doctrine_id>/delete/",
        views.doctrine_delete,
        name="doctrine_delete",
    ),
    path(
        "manage/doctrine/<int:doctrine_id>/duplicate/",
        views.doctrine_duplicate,
        name="doctrine_duplicate",
    ),
    path(
        "manage/doctrine/<int:doctrine_id>/archive/",
        views.doctrine_archive,
        name="doctrine_archive",
    ),
    path(
        "manage/doctrine/<int:doctrine_id>/restore/",
        views.doctrine_restore,
        name="doctrine_restore",
    ),
    path(
        "manage/doctrine/<int:doctrine_id>/fit/new/",
        views.fit_create,
        name="fit_create",
    ),
    path("manage/fit/<int:fit_id>/edit/", views.fit_edit, name="fit_edit"),
    path("manage/fit/<int:fit_id>/delete/", views.fit_delete, name="fit_delete"),
    path(
        "manage/fit/<int:fit_id>/duplicate/", views.fit_duplicate, name="fit_duplicate"
    ),
    path(
        "manage/fit/changelog/<int:entry_id>/delete/",
        views.changelog_delete,
        name="changelog_delete",
    ),
    # Dashboard widget opt-in toggle
    path("widget/toggle/", views.fleet_widget_toggle, name="fleet_widget_toggle"),
    # Fleet scope authorization
    path("fleet/authorize/", views.fleet_authorize, name="fleet_authorize"),
    # Fittings scope authorization + save-to-ESI
    path("fittings-authorize/", views.fittings_authorize, name="fittings_authorize"),
    path(
        "fit/<int:fit_id>/save-to-esi/", views.fit_save_to_esi, name="fit_save_to_esi"
    ),
    # Move/copy fit between doctrines
    path(
        "manage/fit/<int:fit_id>/move-copy/", views.fit_move_copy, name="fit_move_copy"
    ),
    # Fleet structures moved from Manage to Fleet Setup (#7); old links redirect.
    path(
        "manage/structure/new/",
        RedirectView.as_view(pattern_name="aa_fleet_hub:structure_create"),
    ),
    path(
        "manage/structure/<int:structure_id>/edit/",
        RedirectView.as_view(pattern_name="aa_fleet_hub:structure_edit"),
    ),
    path(
        "manage/structure/<int:structure_id>/delete/",
        RedirectView.as_view(pattern_name="aa_fleet_hub:structure_edit"),
    ),
    path(
        "fleet/structure/<int:structure_id>/apply/",
        views.structure_apply,
        name="structure_apply",
    ),
    # Fit panel (partial, for offcanvas AJAX load)
    path("fit/<int:fit_id>/panel/", views.fit_panel, name="fit_panel"),
    # SDE autocomplete
    path("autocomplete/skills/", views.autocomplete_skills, name="autocomplete_skills"),
    path("autocomplete/ships/", views.autocomplete_ships, name="autocomplete_ships"),
    path(
        "autocomplete/skill-exact/",
        views.autocomplete_skill_exact,
        name="autocomplete_skill_exact",
    ),
]
