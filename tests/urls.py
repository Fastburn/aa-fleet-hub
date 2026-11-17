"""URLs for template tests, so {% url 'aa_fleet_hub:...' %} resolves."""

from django.urls import include, path

urlpatterns = [path("fleet-hub/", include("aa_fleet_hub.urls"))]
