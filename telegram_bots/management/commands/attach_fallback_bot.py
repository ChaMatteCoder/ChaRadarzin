import os

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from telegram_bots.configuration import ManagedBotConflict, attach_manual_fallback_bot
from tenancy.models import Tenant


class Command(BaseCommand):
    help = "Anexa temporariamente um bot por variável de ambiente; nunca aceita token em argumento."

    def add_arguments(self, parser):
        parser.add_argument("--tenant", required=True)
        parser.add_argument("--token-env", default="TELEGRAM_FALLBACK_BOT_TOKEN")

    def handle(self, *args, **options):
        try:
            tenant = Tenant.objects.get(pk=options["tenant"])
        except Tenant.DoesNotExist as exc:
            raise CommandError("Tenant nao encontrado.") from exc
        token = os.environ.get(options["token_env"], "")
        if not token and options["token_env"] == "TELEGRAM_FALLBACK_BOT_TOKEN":
            token = settings.TELEGRAM_FALLBACK_BOT_TOKEN
        if not token:
            raise CommandError("A variavel indicada por --token-env esta vazia.")
        try:
            bot = attach_manual_fallback_bot(tenant, token)
        except Exception as exc:
            if isinstance(exc, ManagedBotConflict):
                raise CommandError(exc.code) from exc
            raise CommandError("Nao foi possivel anexar o bot fallback.") from exc
        self.stdout.write(self.style.SUCCESS(f"Bot @{bot.username} anexado em modo fallback."))
