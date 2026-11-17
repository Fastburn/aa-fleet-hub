from allianceauth import hooks
from allianceauth.menu.hooks import MenuItemHook
from allianceauth.services.hooks import UrlHook

from . import urls
from .views import dashboard_widget


class FleetHubMenuItem(MenuItemHook):
    def __init__(self):
        super().__init__(
            "Fleet Hub",
            "fas fa-ship fa-fw",
            "aa_fleet_hub:index",
            navactive=["aa_fleet_hub:"],
        )

    def render(self, request):
        if request.user.has_perm("aa_fleet_hub.basic_access"):
            return super().render(request)
        return ""


class FleetHubDashboardHook(hooks.DashboardItemHook):
    def __init__(self):
        super().__init__(dashboard_widget, 5)


@hooks.register("menu_item_hook")
def register_menu():
    return FleetHubMenuItem()


@hooks.register("url_hook")
def register_urls():
    return UrlHook(urls, "aa_fleet_hub", r"^fleet-hub/")


@hooks.register("dashboard_hook")
def register_dashboard():
    return FleetHubDashboardHook()
