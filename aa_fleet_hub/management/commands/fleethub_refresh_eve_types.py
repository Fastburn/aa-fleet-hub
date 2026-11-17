from django.core.management.base import BaseCommand

from aa_fleet_hub.tasks import refresh_eve_type_catalog


class Command(BaseCommand):
    help = "Refresh the ship (category 6) and skill (category 16) EveType catalog from ESI."

    def handle(self, *args, **options):
        refresh_eve_type_catalog()
        self.stdout.write(
            self.style.SUCCESS(
                "Fleet Hub EveType catalog refresh complete "
                "(related-object tasks may still be queued for the Celery worker)."
            )
        )
