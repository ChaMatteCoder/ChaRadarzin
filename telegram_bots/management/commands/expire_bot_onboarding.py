from django.core.management.base import BaseCommand

from telegram_bots.onboarding import expire_stale_onboarding


class Command(BaseCommand):
    help = "Fecha sessoes de onboarding expiradas."

    def handle(self, *args, **options):
        count = expire_stale_onboarding()
        self.stdout.write(f"onboarding_expired={count}")
