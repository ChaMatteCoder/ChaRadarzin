from django.core.management.base import BaseCommand, CommandError

from telegram_bots.client import TelegramApiError, TelegramTransportError
from telegram_bots.configuration import ManagedBotConflict, configure_manager_webhook, manager_client_from_settings


class Command(BaseCommand):
    help = "Valida a capacidade do bot gerenciador sem exibir token."

    def add_arguments(self, parser):
        parser.add_argument("--configure-webhook", action="store_true")

    def handle(self, *args, **options):
        try:
            if options["configure_webhook"]:
                me = configure_manager_webhook()
                self.stdout.write(self.style.SUCCESS("Webhook do manager configurado."))
            else:
                me = manager_client_from_settings().get_me()
            self.stdout.write(f"manager_bot_id={me.get('id')}")
            self.stdout.write(f"manager_username=@{me.get('username', '')}")
            self.stdout.write(f"can_manage_bots={bool(me.get('can_manage_bots'))}")
            if not me.get("can_manage_bots"):
                raise CommandError("O bot nao possui can_manage_bots.")
        except (ManagedBotConflict, TelegramApiError, TelegramTransportError) as exc:
            raise CommandError(str(exc)) from exc
