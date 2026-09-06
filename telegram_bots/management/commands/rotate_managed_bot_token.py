from django.core.management.base import BaseCommand, CommandError

from telegram_bots.client import TelegramApiError, TelegramTransportError
from telegram_bots.configuration import ManagedBotConflict, rotate_managed_bot_token
from telegram_bots.models import ManagedBot


class Command(BaseCommand):
    help = "Rotaciona a credencial de um managed bot sem imprimir o token."

    def add_arguments(self, parser):
        parser.add_argument("--tenant", required=True)

    def handle(self, *args, **options):
        try:
            bot = ManagedBot.objects.get(tenant_id=options["tenant"])
        except ManagedBot.DoesNotExist as exc:
            raise CommandError("Bot nao encontrado para o tenant.") from exc
        try:
            rotate_managed_bot_token(bot)
        except (ManagedBotConflict, TelegramApiError, TelegramTransportError) as exc:
            raise CommandError(str(exc)) from exc
        self.stdout.write(self.style.SUCCESS(f"Token rotacionado para @{bot.username}."))
