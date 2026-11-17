from django import template

from ..views import _active_character_id, _owned_characters

register = template.Library()


@register.inclusion_tag(
    "aa_fleet_hub/partials/character_switcher.html", takes_context=True
)
def character_switcher(context):
    request = context["request"]
    return {
        "owned_characters": _owned_characters(request.user),
        "active_character_id": _active_character_id(request),
        "request": request,
    }
