from django.core.management.base import BaseCommand

from monitoring.preview import expire_stale_product_previews


class Command(BaseCommand):
    help = "Marca como expiradas as previas de links que passaram do prazo de confirmacao."

    def handle(self, *args, **options):
        expired_count = expire_stale_product_previews()
        self.stdout.write(self.style.SUCCESS(f"Previas expiradas: {expired_count}"))
