from __future__ import annotations

from django.core.management.base import BaseCommand, CommandError

from telegram_bots.delivery import send_test_notification
from telegram_bots.models import ManagedBot


class Command(BaseCommand):
    help = "Envia uma notificacao operacional de teste, sem criar historico de preco."

    def add_arguments(self, parser) -> None:
        parser.add_argument("--bot-username", required=True)
        parser.add_argument("--confirm", action="store_true")

    def handle(self, *args, **options) -> None:
        if not options["confirm"]:
            raise CommandError(
                "Nenhuma mensagem foi enviada. Repita com --confirm para autorizar o teste."
            )
        username = str(options["bot_username"]).strip().lstrip("@")
        if not username:
            raise CommandError("Informe um username de bot valido.")
        try:
            bot = ManagedBot.objects.select_related("tenant").get(username__iexact=username)
        except ManagedBot.DoesNotExist as exc:
            raise CommandError("Bot nao encontrado.") from exc

        result = send_test_notification(bot)
        if result.status != "SENT":
            raise CommandError(f"Notificacao nao enviada ({result.error_code}).")
        self.stdout.write(
            self.style.SUCCESS(f"Notificacao de teste enviada para @{bot.username}.")
        )
