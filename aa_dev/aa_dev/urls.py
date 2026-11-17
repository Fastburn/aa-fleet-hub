from django.urls import include, path

from allianceauth import urls

from aa_fleet_hub.views import sso_callback

urlpatterns = [
    # Override esi's callback with a friendly-fallback wrapper (see aa_fleet_hub.views.sso_callback)
    path("sso/callback/", sso_callback, name="esi_callback_safe"),
    path("", include(urls)),
]

handler500 = "allianceauth.views.Generic500Redirect"
handler404 = "allianceauth.views.Generic404Redirect"
handler403 = "allianceauth.views.Generic403Redirect"
handler400 = "allianceauth.views.Generic400Redirect"
