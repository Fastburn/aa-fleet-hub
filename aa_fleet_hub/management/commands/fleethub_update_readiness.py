from django.core.management.base import BaseCommand

from aa_fleet_hub.tasks import update_readiness_cache


class Command(BaseCommand):
    help = "Recompute the Fleet Hub readiness cache for all active doctrines."

    def handle(self, *args, **options):
        update_readiness_cache()
        self.stdout.write(self.style.SUCCESS("Fleet Hub readiness cache updated."))
